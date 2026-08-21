#!/usr/bin/env python3
"""
ingest_batch.py — stage grouped book folders and convert them to webp.

The reading of each book (title + copyright pages) is done by the Cowork enrichment
pass, which fills records_source.json. THIS script does the mechanical rest: copy each
book folder into data/intake/ with a title.txt, then run prep_images.py to produce
data/ready/.

FOLDER NAMES ARE PRESERVED. A book committed as `0038-some-title` stays
`0038-some-title` through intake, ready, and the apply log. It used to be renumbered
to `001-some-title` here, which forced a second records file keyed by the new name and
meant the apply log spoke a different numbering from the one in data/books/ — hours
lost working out whether two numbers referred to the same book. Sorted order is
catalogue order either way, because `commit` numbers folders sequentially and
zero-pads them.

USAGE:
  python3 scripts/ingest_batch.py                     # defaults to data/books + data/records_source.json
  python3 scripts/ingest_batch.py --source ... --records ...
  (add --no-prep to skip resize/convert; set VIVARIUM_APP if the app isn't a sibling)
"""
import os, re, sys, json, shutil, subprocess, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths

INTAKE = paths.INTAKE
READY = paths.READY
EXTS   = (".jpeg",".jpg",".png",".heic",".heif",".tif",".tiff",".webp",".bmp")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--source", default=paths.BOOKS)
    ap.add_argument("--records", default=paths.RECORDS_SOURCE)
    ap.add_argument("--no-prep",action="store_true")
    ap.add_argument("--reset-intake",action="store_true",
                    help="delete data/intake/ and data/ready/ before staging")
    a=ap.parse_args()
    paths.require_app()
    src=os.path.abspath(a.source)
    recs=json.load(open(os.path.abspath(a.records)))
    print(f"{len(recs)} record(s) to stage")
    # data/intake/ and data/ready/ are scratch: prep_images.py converts EVERY folder in
    # intake, and apply_images.py creates a NEW record for every non-numeric folder in
    # ready. Leftovers from an earlier batch therefore come through again as duplicates.
    # handoff.py clears both after a successful seed, so a leftover means an earlier run
    # died partway. Refuse rather than delete silently.
    leftovers = []
    for d in (INTAKE, READY):
        if os.path.isdir(d):
            leftovers += [f"{os.path.basename(d)}/{x}" for x in sorted(os.listdir(d))
                          if not x.startswith(".")]
    if leftovers and not a.reset_intake:
        print(f"REFUSING TO STAGE — {len(leftovers)} leftover folder(s) from an earlier run:")
        for x in leftovers[:8]: print("  ", x)
        if len(leftovers) > 8: print(f"   ... and {len(leftovers)-8} more")
        print("\nThese would be re-converted and applied as DUPLICATE records.")
        print("Confirm they already reached Supabase, then re-run with --reset-intake.")
        sys.exit(1)
    if a.reset_intake:
        for d in (INTAKE, READY):
            shutil.rmtree(d, ignore_errors=True)
        print("cleared data/intake/ and data/ready/")
    os.makedirs(INTAKE, exist_ok=True)
    staged=imgs=0
    for rel,m in recs.items():
        if os.sep in rel or "/" in rel:
            print(f"  ! nested source folder not supported: {rel}"); continue
        sd=os.path.join(src,rel)
        if not os.path.isdir(sd): print(f"  ! missing source folder: {rel}"); continue
        dd=os.path.join(INTAKE,rel); os.makedirs(dd,exist_ok=True)
        for f in sorted(x for x in os.listdir(sd) if x.lower().endswith(EXTS) and not x.startswith(".")):
            shutil.copy(os.path.join(sd,f),os.path.join(dd,f)); imgs+=1
        open(os.path.join(dd,"title.txt"),"w").write(m.get("title","")+"\n"+m.get("author","")+"\n")
        staged+=1
    print(f"staged {staged} folders / {imgs} images -> data/intake/")
    if not a.no_prep:
        print("running prep_images.py (resize/convert) ...")
        subprocess.run([sys.executable, os.path.join(paths.HERE, "prep_images.py")])
    print("\nNext (gated writes):")
    print("  python3 scripts/apply_images.py | tee /tmp/apply.log")
    print("  python3 scripts/merge_records.py --apply-log /tmp/apply.log --write")
    print("  python3 scripts/sync_images.py --write")
    print("  npm run upload      # images-to-r2.mjs")
    print("  npm run seed        # seed-new-items.mjs, insert-only")
    print("  (run `npm run sync` BEFORE apply_images.py so ids assign above the true max)")
    print("  — or just: python3 scripts/handoff.py")
if __name__=="__main__": main()
