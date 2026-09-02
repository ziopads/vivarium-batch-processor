#!/usr/bin/env python3
"""
resolve_isbns.py — turn ISBNs into titles and authors via Open Library and Google Books.

WHY
    An ISBN identifies an edition. Title and author are stable across that edition,
    so a lookup gives a canonical reading that no amount of squinting at a cover
    photograph improves on. Every record this fills is a book the vision pass does
    not need to open 1.jpg for.

WHAT IT FILLS
    title, author — and nothing else.

    Publisher, year, edition and printing are deliberately NOT taken from the
    lookup. Reprints reuse ISBNs, and the most common anomaly in this collection is
    an edition statement contradicting its own number line. For those fields the
    physical copy in hand is the authority and the database is not.

PROVENANCE
    Every value written is logged to data/_work/resolved.json with the source and
    the raw response fields, because a wrong ISBN produces a completely plausible
    wrong book. If a title later looks wrong, that log says whether it came from a
    lookup or from the page.

    By default only blank fields are filled. --overwrite will replace existing
    values; --verify writes nothing and reports disagreements instead, which is the
    right mode for checking a vision pass after the fact.

USAGE
    python3 scripts/resolve_isbns.py --dry-run
    python3 scripts/resolve_isbns.py
    python3 scripts/resolve_isbns.py --verify        # compare, write nothing
"""
import os
import sys
import json
import time
import shutil
import argparse
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths

LOG = os.path.join(paths.WORK, "resolved.json")
UA = "vivarium-batch-processor/1.0 (personal library cataloguing)"
TIMEOUT = 15


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def from_open_library(isbn):
    url = (
        "https://openlibrary.org/api/books?bibkeys=ISBN:"
        + urllib.parse.quote(isbn)
        + "&format=json&jscmd=data"
    )
    data = get_json(url)
    entry = data.get("ISBN:" + isbn)
    if not entry:
        return None
    title = entry.get("title", "").strip()
    sub = (entry.get("subtitle") or "").strip()
    if sub:
        title = f"{title}: {sub}"
    authors = [a.get("name", "").strip() for a in entry.get("authors", [])]
    authors = [a for a in authors if a]
    if not title:
        return None
    return {"title": title, "author": ", ".join(authors), "source": "openlibrary"}


def from_google_books(isbn):
    url = (
        "https://www.googleapis.com/books/v1/volumes?q=isbn:"
        + urllib.parse.quote(isbn)
    )
    data = get_json(url)
    items = data.get("items") or []
    if not items:
        return None
    info = items[0].get("volumeInfo", {})
    title = (info.get("title") or "").strip()
    sub = (info.get("subtitle") or "").strip()
    if sub:
        title = f"{title}: {sub}"
    authors = [a.strip() for a in (info.get("authors") or []) if a.strip()]
    if not title:
        return None
    return {"title": title, "author": ", ".join(authors), "source": "googlebooks"}


def resolve(isbn):
    for fn in (from_open_library, from_google_books):
        try:
            hit = fn(isbn)
        except Exception as e:
            print(f"    {fn.__name__} failed: {e}")
            continue
        if hit:
            return hit
    return None


def dump(obj):
    return json.dumps(obj, indent=1, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", default=paths.RECORDS_SOURCE)
    ap.add_argument("--only", action="append", default=None)
    ap.add_argument("--overwrite", action="store_true",
                    help="replace title/author even when already filled")
    ap.add_argument("--verify", action="store_true",
                    help="write nothing; report where lookup disagrees with the record")
    ap.add_argument("--delay", type=float, default=0.4, help="seconds between lookups")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    with open(a.records, "rb") as fh:
        raw = fh.read()
    records = json.loads(raw)
    if dump(records).encode("utf-8") != raw:
        sys.exit("records_source.json does not round-trip; investigate before writing.")

    keys = list(records)
    if a.only:
        keys = [k for k in keys if k in set(a.only)]
    todo = [k for k in keys if (records[k].get("isbn") or "").strip()]

    print(f"{len(todo)} record(s) hold an ISBN")
    if a.verify:
        print("verify mode — nothing will be written\n")
    else:
        print(f"filling blanks only" if not a.overwrite else "overwriting existing values")
        print()

    log = {}
    filled = unresolved = untouched = 0
    disagree = []

    for i, key in enumerate(todo, 1):
        rec = records[key]
        isbn = rec["isbn"].strip()
        hit = resolve(isbn)
        time.sleep(a.delay)

        if not hit:
            unresolved += 1
            continue

        log[key] = {"isbn": isbn, **hit}

        had_title = (rec.get("title") or "").strip()
        if a.verify:
            if had_title and had_title.lower() != hit["title"].lower():
                disagree.append((key, had_title, hit["title"]))
            continue

        wrote = False
        if not had_title or a.overwrite:
            rec["title"] = hit["title"]
            wrote = True
        if hit["author"] and (not (rec.get("author") or "").strip() or a.overwrite):
            rec["author"] = hit["author"]
            wrote = True
        if wrote:
            filled += 1
        else:
            untouched += 1

        if i % 25 == 0:
            print(f"  ...{i}/{len(todo)}   filled {filled}")

    print(f"\nresolved      : {len(log)}")
    print(f"no match      : {unresolved}")
    if a.verify:
        print(f"disagreements : {len(disagree)}")
        for key, page, db in disagree[:25]:
            print(f"  {key}\n    page: {page}\n    db  : {db}")
        if len(disagree) > 25:
            print(f"  ... and {len(disagree) - 25} more")
        return
    print(f"records filled: {filled}")
    if untouched:
        print(f"already filled: {untouched}  (--overwrite to replace)")

    os.makedirs(paths.WORK, exist_ok=True)
    with open(LOG, "w", encoding="utf-8") as fh:
        fh.write(dump(log))
    print(f"provenance    : {LOG}")

    if a.dry_run:
        print("\ndry run — records file untouched")
        return

    if filled:
        shutil.copy2(a.records, a.records + ".bak")
        tmp = a.records + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(dump(records))
        os.replace(tmp, a.records)
        print(f"\nwritten: {a.records}")
    else:
        print("\nnothing filled; file untouched")


if __name__ == "__main__":
    main()
