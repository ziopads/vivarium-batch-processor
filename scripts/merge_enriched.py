#!/usr/bin/env python3
"""
merge_enriched.py — merge subagent enrichment output into records_source.json, safely.

WHY THIS EXISTS
    Enrichment fans out across subagents. Concurrent writes to records_source.json
    corrupt it, so each subagent writes its own batch file and a single merge step
    produces one write. That merge step was typed fresh into every session and
    thrown away at the end of it. It is the thing that caught four records whose
    `owner` had been silently overwritten, and it should not depend on being
    remembered correctly next time.

    Everything below is an assertion made BEFORE the write. A failure stops the run.
    Nothing here repairs anything: a batch file that violates the schema is a batch
    file to regenerate, not to patch.

WHAT IT DOES
    Reads   data/records_source.json          (the baseline, untouched)
            <batch-dir>/*.json                (subagent output, one file per subagent)
    Writes  data/records_source.json          (atomically, once)

    Records outside the merged scope are guaranteed byte-identical afterwards.

CARRY-THROUGH FIELDS
    itemType, owner and location are restored from the baseline unconditionally.
    The subagent's values for them are discarded without being compared, because
    comparing invites someone to write down what the value is supposed to be, and
    a rules file asserting `owner: "James"` from one sampled record is exactly how
    four of Valerie's books nearly went missing.

USAGE
    python3 scripts/merge_enriched.py --batch-dir out                 # merge and write
    python3 scripts/merge_enriched.py --batch-dir out --dry-run       # check only
    python3 scripts/merge_enriched.py --batch-dir out --expect 100    # assert scope size

EXIT CODES
    0  merged and written (or dry run passed)
    1  an assertion failed; nothing was written
"""
import os
import sys
import json
import glob
import shutil
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths

CARRY_THROUGH = ("itemType", "owner", "location")

TAXONOMY_STR = ("section", "shelf")
TAXONOMY_LIST = ("genres", "subjects", "places")

ISBN_STATUS = {"present", "none", "unverified", "illegible"}

FORBIDDEN_FIELDS = ("description", "discussion")


class Failed(Exception):
    pass


def dump(obj):
    """The file's exact serialization. Must match byte-for-byte on a round trip."""
    return json.dumps(obj, indent=1, ensure_ascii=False)


def load_baseline(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        data = json.loads(raw)
    except Exception as e:
        raise Failed(f"{path} does not parse: {e}")
    if dump(data).encode("utf-8") != raw:
        raise Failed(
            f"{path} does not round-trip through json.dumps(indent=1, "
            f"ensure_ascii=False) with no trailing newline.\n"
            f"  Writing it would reformat every line of the file, which makes the "
            f"out-of-scope guarantee meaningless. Investigate before merging."
        )
    return data, raw


def load_batches(batch_dir):
    files = sorted(glob.glob(os.path.join(batch_dir, "*.json")))
    if not files:
        raise Failed(f"No batch files in {batch_dir}")

    merged = {}
    origin = {}
    for path in files:
        try:
            with open(path) as fh:
                data = json.load(fh)
        except Exception as e:
            raise Failed(f"{path} does not parse: {e}")
        if not isinstance(data, dict):
            raise Failed(f"{path} is not a JSON object")
        for key, rec in data.items():
            if key in merged:
                raise Failed(
                    f"key {key!r} appears in two batch files: "
                    f"{os.path.basename(origin[key])} and {os.path.basename(path)}"
                )
            if not isinstance(rec, dict):
                raise Failed(f"{path}: {key!r} is not an object")
            merged[key] = rec
            origin[key] = path
    return merged, files


def check_record(key, new, old):
    """Every assertion that applies to one record. Raises on the first failure."""
    for f in FORBIDDEN_FIELDS:
        if f in new:
            raise Failed(f"{key}: has a {f!r} field, which this pass does not write")

    if list(new.keys()) != list(old.keys()):
        missing = [k for k in old if k not in new]
        extra = [k for k in new if k not in old]
        detail = []
        if missing:
            detail.append(f"missing {missing}")
        if extra:
            detail.append(f"unexpected {extra}")
        if not detail:
            detail.append("same fields in a different order")
        raise Failed(f"{key}: field list differs from the baseline — {'; '.join(detail)}")

    for field, old_val in old.items():
        new_val = new[field]
        if type(new_val) is not type(old_val):
            raise Failed(
                f"{key}.{field}: type changed from "
                f"{type(old_val).__name__} to {type(new_val).__name__}"
            )

    for field in TAXONOMY_STR:
        if new.get(field) != "":
            raise Failed(f"{key}.{field}: taxonomy must stay empty")
    for field in TAXONOMY_LIST:
        if new.get(field) != []:
            raise Failed(f"{key}.{field}: taxonomy must stay empty")

    status = new.get("isbn_status")
    if status not in ISBN_STATUS:
        raise Failed(f"{key}.isbn_status: {status!r} is not one of {sorted(ISBN_STATUS)}")

    isbn = (new.get("isbn") or "").strip()
    if status == "present" and not isbn:
        raise Failed(f"{key}: isbn_status is 'present' but isbn is empty")
    if status != "present" and isbn:
        raise Failed(f"{key}: isbn is filled but isbn_status is {status!r}")

    if not (new.get("title") or "").strip():
        raise Failed(f"{key}: title is empty")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", default=paths.RECORDS_SOURCE)
    ap.add_argument("--batch-dir", required=True,
                    help="directory of subagent batch files (*.json)")
    ap.add_argument("--expect", type=int, default=None,
                    help="assert this many records are in scope")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    a = ap.parse_args()

    try:
        baseline, raw = load_baseline(a.records)
        incoming, files = load_batches(a.batch_dir)

        print(f"baseline : {a.records}  ({len(baseline)} records, {len(raw)} bytes)")
        print(f"batches  : {len(files)} file(s), {len(incoming)} records in scope")

        unknown = [k for k in incoming if k not in baseline]
        if unknown:
            raise Failed(
                f"{len(unknown)} key(s) are not in the baseline — "
                f"keys are folder names and must match exactly: {unknown[:5]}"
            )

        if a.expect is not None and len(incoming) != a.expect:
            raise Failed(f"expected {a.expect} records in scope, found {len(incoming)}")

        # Restore carry-through fields before checking anything else. The subagent's
        # values for these are discarded, not compared.
        restored = 0
        for key, rec in incoming.items():
            old = baseline[key]
            for field in CARRY_THROUGH:
                if field in old:
                    if rec.get(field) != old[field]:
                        restored += 1
                    rec[field] = old[field]

        for key, rec in incoming.items():
            check_record(key, rec, baseline[key])

        # Build the result in the baseline's key order.
        result = {k: (incoming[k] if k in incoming else baseline[k]) for k in baseline}

        if list(result.keys()) != list(baseline.keys()):
            raise Failed("key order changed")

        changed = [k for k in baseline if result[k] != baseline[k]]
        untouched_differs = [
            k for k in baseline if k not in incoming and result[k] is not baseline[k]
        ]
        if untouched_differs:
            raise Failed(f"out-of-scope records were rebuilt: {untouched_differs[:5]}")

        out = dump(result)

        print(f"\nrecords changed         : {len(changed)}")
        print(f"carry-through restored  : {restored} field value(s)")
        filled = sum(1 for k in incoming if (result[k].get("isbn") or "").strip())
        print(f"with an isbn            : {filled} of {len(incoming)}")
        print(f"output size             : {len(out.encode('utf-8'))} bytes")

        if set(changed) - set(incoming):
            raise Failed("a record outside the scope changed")

        if a.dry_run:
            print("\ndry run — nothing written")
            return 0

        if not a.no_backup:
            bak = a.records + ".bak"
            shutil.copy2(a.records, bak)
            print(f"backup                  : {bak}")

        tmp = a.records + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(out)
        os.replace(tmp, a.records)

        # Re-read from disk and confirm what landed is what we built.
        with open(a.records, "rb") as fh:
            back = fh.read()
        if back != out.encode("utf-8"):
            raise Failed("the file on disk does not match what was written")
        reloaded = json.loads(back)
        if list(reloaded.keys()) != list(baseline.keys()):
            raise Failed("the file on disk has a different key set or order")
        for k in baseline:
            if k not in incoming and reloaded[k] != baseline[k]:
                raise Failed(f"out-of-scope record {k} changed on disk")

        print(f"\nwritten and verified    : {a.records}")
        return 0

    except Failed as e:
        print(f"\nFAILED: {e}", file=sys.stderr)
        print("Nothing was written.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
