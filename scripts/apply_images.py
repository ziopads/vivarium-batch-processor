#!/usr/bin/env python3
"""
apply_images.py  —  Stage 2 of the manual image pipeline.

Takes the prepared WEBP from data/ready/ and ingests it into the catalogue app:
  - EXISTING item folder (numeric name):
        copies the images into public/items/<id6>/, numbered to CONTINUE after any
        images already there (so it appends supplemental shots without clobbering).
        The app rebuilds each gallery by scanning the folder, so for an item that
        already had images this writes NO data — nothing to collide with.
        (If the item had no images yet, its cover/image pointer is set.)
  - NEW item folder (non-numeric name):
        assigns the next free id, copies the images into public/items/<id6>/, and
        creates a record in items.json (title from title.txt or the folder name;
        empty description/discussion so the write-up task fills them later).

SAFETY: the write-up scheduled task and this script both edit items.json, so if a
        run of the task might be active, pause it first. A backup (items.json.imgbak)
        is written before any change.

USAGE:  python3 scripts/apply_images.py
        (set VIVARIUM_APP if the catalogue app isn't a sibling directory)
"""
import os, re, json, shutil, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths

READY = paths.READY
ITEMS_DIR = paths.PUBLIC_ITEMS
DATA = paths.ITEMS

def load_items():
    for _ in range(6):
        try:
            return json.load(open(DATA))
        except json.JSONDecodeError:
            time.sleep(1)
    raise SystemExit("Could not read items.json cleanly (is the task writing?). Try again.")

def cover_stems(dirpath):
    if not os.path.isdir(dirpath):
        return []
    return sorted(f[:-5] for f in os.listdir(dirpath)
                  if f.endswith(".webp") and not f.endswith("-thumb.webp"))

def max_prefix(dirpath):
    mx = 0
    for s in cover_stems(dirpath):
        m = re.match(r"(\d+)-", s)
        if m:
            mx = max(mx, int(m.group(1)))
    return mx

def label_of(stem):                 # "03-copyright-page" -> "Copyright Page"
    return re.sub(r"^\d+-", "", stem).replace("-", " ").strip().title()

def strip_prefix(stem):             # "03-copyright-page" -> "copyright-page"
    return re.sub(r"^\d+-", "", stem)

def copy_pair(src_dir, stem, dst_dir, new_stem):
    shutil.copy(f"{src_dir}/{stem}.webp", f"{dst_dir}/{new_stem}.webp")
    tp = f"{src_dir}/{stem}-thumb.webp"
    if os.path.exists(tp):
        shutil.copy(tp, f"{dst_dir}/{new_stem}-thumb.webp")

def pick_cover(stems):
    return next((s for s in stems if "cover" in s), stems[0])

def read_title(src_dir, folder):
    tt = f"{src_dir}/title.txt"
    if os.path.exists(tt):
        lines = [l.strip() for l in open(tt) if l.strip()]
        if lines:
            return lines[0], (lines[1] if len(lines) > 1 else "")
    # No title.txt (the positional 1/2/3 convention): leave title blank so the
    # analysis step fills it. A blank title also flags the record as un-analyzed.
    return "", ""

def main():
    paths.require_app()
    if not os.path.isdir(READY):
        print("No data/ready/ folder — run prep_images.py first.")
        return
    folders = sorted(d for d in os.listdir(READY)
                     if os.path.isdir(f"{READY}/{d}") and not d.startswith("."))
    if not folders:
        print("Nothing to apply.")
        return

    items = load_items()
    by = {i["id"]: i for i in items}
    maxid = max(i["id"] for i in items)
    shutil.copy(DATA, DATA + ".imgbak")
    data_changed = False

    for name in folders:
        src_dir = f"{READY}/{name}"
        ready = cover_stems(src_dir)
        if not ready:
            continue

        if name.isdigit():                                   # ---- existing item ----
            iid = int(name)
            if iid not in by:
                print(f"  ! {name}: no item with that id — skipped")
                continue
            id6 = f"{iid:06d}"
            dst = f"{ITEMS_DIR}/{id6}"
            had_images = os.path.isdir(dst) and len(cover_stems(dst)) > 0
            os.makedirs(dst, exist_ok=True)
            start = max_prefix(dst)
            for j, stem in enumerate(ready, start=start + 1):
                copy_pair(src_dir, stem, dst, f"{j:02d}-{strip_prefix(stem)}")
            if not had_images:                                # give it a cover pointer
                placed = cover_stems(dst)
                cov = pick_cover(placed)
                it = by[iid]
                it["cover"] = f"{id6}/{cov}"
                it["image"] = f"{id6}/{cov}"
                it["images"] = [{"src": f"{id6}/{s}", "label": label_of(s)} for s in placed]
                data_changed = True
            print(f"  {name}: {'appended' if had_images else 'set first'} "
                  f"{len(ready)} image(s) on item #{id6}")

        else:                                                # ---- new item ----
            iid = maxid + 1
            maxid = iid
            id6 = f"{iid:06d}"
            dst = f"{ITEMS_DIR}/{id6}"
            os.makedirs(dst, exist_ok=True)
            for j, stem in enumerate(ready, start=1):
                copy_pair(src_dir, stem, dst, f"{j:02d}-{strip_prefix(stem)}")
            placed = cover_stems(dst)
            cov = pick_cover(placed)
            title, author = read_title(src_dir, name)
            items.append({
                "id": iid, "itemType": "Book", "title": title, "author": author,
                "publisher": "", "placeOfPublication": "", "year": "", "edition": "",
                "printing": "", "isbn": "", "format": "", "description": "",
                "blurb": "", "discussion": "", "signed": False, "inscription": "",
                "genres": [], "shelf": "", "subjects": [], "places": [], "condition": "",
                "location": "", "owner": "", "notes": "",
                "cover": f"{id6}/{cov}", "image": f"{id6}/{cov}",
                "images": [{"src": f"{id6}/{s}", "label": label_of(s)} for s in placed],
            })
            data_changed = True
            shown = title or "(untitled — needs analysis)"
            print(f"  {name}: created NEW item #{id6}  '{shown}'  ({len(placed)} image(s))")

    if data_changed:
        json.dump(items, open(DATA, "w"), ensure_ascii=False, indent=1)
        print("\nitems.json updated (backup at items.json.imgbak).")
    else:
        print("\nNo items.json changes needed — existing items just received files.")

if __name__ == "__main__":
    main()
