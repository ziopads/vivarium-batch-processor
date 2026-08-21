#!/usr/bin/env python3
"""
sync_images.py — reconcile each item's images[] with the webp files on disk.

WHY: locally, lib/data.ts rebuilds a gallery by scanning public/items/<id6>/ on
every read (scanImages), so files appended by apply_images.py just appear. ONLINE
(Supabase configured) there is no scan — the gallery is exactly the images[] array
stored on the row. This script bakes the folder scan into data/items.json so the
online copy matches the local one. Run it AFTER apply_images.py has appended
supplemental shots, and BEFORE seeding Supabase / uploading to R2.

Ordering & labels mirror lib/data.ts scanImages() exactly:
  - stems = sorted webp basenames (excluding -thumb); src = "<id6>/<stem>"
  - label = humanize(stem): drop leading "NN-", dashes -> spaces, Title Case
  - cover first: honor item.cover if it still points to a present file; else the
    file whose stem contains "cover"; else the first stem. image + cover are set
    to that primary src so the DB row is self-consistent (no runtime reorder online).

SAFETY:
  - An item is left ALONE and reported if any of its current images[] point at files
    that are not on local disk. That covers both the wholly-R2 case and the PARTIAL
    case — an item with five images in the row and four on disk. Rebuilding from what
    is visible would silently drop the fifth from the online gallery, which is exactly
    what happened to seven items on 21 Aug 2026: photographs added through the app go
    to R2 and Supabase without ever being written to public/items/, so the local
    folder is not a complete picture of the item.
  - DRY-RUN by default: prints what would change and writes nothing.
  - --write persists, backing up items.json to items.json.syncbak first.
  - Never touches R2 or Supabase. After --write, run:
        npm run upload      # images-to-r2.mjs, uploads new webp
        npm run seed        # seed-new-items.mjs, insert-only

USAGE:  python3 scripts/sync_images.py            # dry run
        python3 scripts/sync_images.py --write    # persist to items.json
        (set VIVARIUM_APP if the catalogue app isn't a sibling directory)
"""
import os, re, json, sys, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths

ITEMS_DIR = paths.PUBLIC_ITEMS
DATA = paths.ITEMS


def humanize(stem: str) -> str:
    s = re.sub(r"^\d+-", "", stem).replace("-", " ").strip()
    return re.sub(r"\b\w", lambda m: m.group().upper(), s)


def cover_stems(dirpath: str):
    if not os.path.isdir(dirpath):
        return []
    return sorted(
        f[:-5] for f in os.listdir(dirpath)
        if f.endswith(".webp") and not f.endswith("-thumb.webp")
    )


def pick_cover_stem(stems, cover_ptr):
    if cover_ptr:
        want = cover_ptr.split("/")[-1]
        if want in stems:
            return want
    for s in stems:
        if "cover" in s:
            return s
    return stems[0] if stems else None


def build_images(iid: int, cover_ptr):
    id6 = f"{iid:06d}"
    stems = cover_stems(f"{ITEMS_DIR}/{id6}")
    if not stems:
        return None, id6, stems
    imgs = [{"src": f"{id6}/{s}", "label": humanize(s)} for s in stems]
    cov = pick_cover_stem(stems, cover_ptr)
    ci = next((i for i, im in enumerate(imgs) if im["src"] == f"{id6}/{cov}"), 0)
    if ci > 0:
        imgs.insert(0, imgs.pop(ci))
    return imgs, id6, stems


def main():
    write = "--write" in sys.argv
    with open(DATA) as fh:
        items = json.load(fh)

    changed, no_files, partial, bad_copyright = [], [], [], []

    for it in items:
        iid = it["id"]
        imgs, id6, stems = build_images(iid, it.get("cover"))
        if imgs is None:
            if it.get("images"):
                no_files.append(iid)  # has images[] in JSON but no local webp (R2-only)
            continue

        # Partial visibility. An existing entry pointing at a file that is not on disk
        # means the row knows about images this folder does not, so the folder is not
        # authoritative and rebuilding from it would delete them.
        missing = [i.get("src") for i in (it.get("images") or [])
                   if i.get("src", "").split("/")[-1] not in stems]
        if missing:
            partial.append((iid, len(missing)))
            continue

        cp = it.get("copyright")
        if cp and cp.split("/")[-1] not in stems:
            bad_copyright.append(iid)

        primary = imgs[0]["src"]
        old_srcs = [i.get("src") for i in (it.get("images") or [])]
        new_srcs = [i["src"] for i in imgs]
        if old_srcs != new_srcs or it.get("image") != primary or it.get("cover") != primary:
            changed.append((iid, len(old_srcs), len(new_srcs)))
            if write:
                it["images"] = imgs
                it["image"] = primary
                it["cover"] = primary

    print(f"Scanned {len(items)} items.")
    print(f"  would change: {len(changed)}")
    for iid, o, n in changed[:40]:
        delta = f"  (+{n - o})" if n != o else ""
        print(f"   #{iid:06d}: {o} -> {n} image(s){delta}")
    if len(changed) > 40:
        print(f"   ... and {len(changed) - 40} more")
    if no_files:
        print(f"  note: {len(no_files)} item(s) have images[] in JSON but no local webp "
              f"(R2-only) — left untouched: {no_files[:20]}{' ...' if len(no_files) > 20 else ''}")
    if partial:
        print(f"  note: {len(partial)} item(s) have images the local folder does not "
              f"(added through the app) — left untouched so they are not dropped:")
        for iid, n in partial[:20]:
            print(f"     #{iid:06d}: {n} image(s) not on disk")
        if len(partial) > 20:
            print(f"     ... and {len(partial) - 20} more")
    if bad_copyright:
        print(f"  ! {len(bad_copyright)} item(s) have a copyright pointer to a missing file: "
              f"{bad_copyright[:20]}{' ...' if len(bad_copyright) > 20 else ''}")

    if write:
        shutil.copy(DATA, DATA + ".syncbak")
        with open(DATA, "w") as fh:
            json.dump(items, fh, ensure_ascii=False, indent=1)
        print(f"\nWROTE {DATA}  (backup at items.json.syncbak).")
        print("Next: npm run upload  &&  npm run seed")
    else:
        print("\nDry run — nothing written. Re-run with --write to persist.")


if __name__ == "__main__":
    main()
