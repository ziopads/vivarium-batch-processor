#!/usr/bin/env python3
"""Inject the enriched records (data/records_source.json) into apply-created skeletons.

Usage:
  python3 scripts/apply_images.py | tee /tmp/apply.log
  python3 scripts/merge_records.py --apply-log /tmp/apply.log            # dry run
  python3 scripts/merge_records.py --apply-log /tmp/apply.log --write    # patch items.json (.mergebak backup)

Matches on the FOLDER name printed by apply_images.py, which is the same name the
book has had since `commit` — ingest_batch.py no longer renumbers, so there is no
second records file to key against. Sets every field except description/discussion
(the write-up task owns those) and tags image 2 as the copyright page.
"""
import os, re, json, sys, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
READY  = paths.READY
DATA   = paths.ITEMS
RECORDS = paths.RECORDS_SOURCE
master = json.load(open(RECORDS))
# Only what the enrichment pass fills. section, shelf, genres, subjects, places,
# owner and location are deliberately absent: those belong to the shelving pass in
# the app. Pushing them from here would write blanks over shelving already done,
# which matters because this script is re-runnable from a kept apply log.
PUSH = ["itemType","publisher","placeOfPublication","year","edition","printing","isbn",
        "isbn_status","format","signed","inscription","condition","notes"]
def cover_stems(d):
    if not os.path.isdir(d): return []
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".webp") and not f.endswith("-thumb.webp"))
def merge_one(rec, name):
    m = master.get(name)
    if not m: return False
    for k in PUSH: rec[k] = m.get(k, rec.get(k, ""))
    id6 = f"{rec['id']:06d}"; stems = cover_stems(f"{READY}/{name}")
    # Frame 2 is the copyright page by convention, and prep_images.py names it
    # 02-copyright, so the pointer is derivable rather than configured.
    cp = next((s for s in stems if s.startswith("02-")), None)
    if cp: rec["copyright"] = f"{id6}/{cp}"
    return True
def main():
    if "--apply-log" not in sys.argv: sys.exit(__doc__)
    log = sys.argv[sys.argv.index("--apply-log")+1]; write = "--write" in sys.argv
    mp = {}
    for line in open(log):
        m = re.search(r"^\s*(.+?): created NEW item #(\d+)", line)
        if m: mp[m.group(1)] = int(m.group(2))
    if not mp: sys.exit("No 'created NEW item' lines in apply log.")
    items = json.load(open(DATA)); by = {i["id"]: i for i in items}
    patched = 0; miss = []
    for name, iid in mp.items():
        if iid in by and merge_one(by[iid], name): patched += 1
        elif name not in master: miss.append(name)
    print(f"apply-log folders: {len(mp)} | patched: {patched} | no master row: {miss}")
    if write:
        shutil.copy(DATA, DATA+".mergebak"); json.dump(items, open(DATA,"w"), ensure_ascii=False, indent=1)
        print(f"WROTE {DATA} (backup items.json.mergebak)")
    else:
        print("dry run — add --write to persist.")
if __name__ == "__main__": main()
