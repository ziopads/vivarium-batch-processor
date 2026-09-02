#!/usr/bin/env python3
"""
make_read_copies.py — downscaled copies of frames 1/2/3 for the Cowork enrichment pass.

WHY
    data/books/ holds full-size iPhone frames, 4-8 MB each. Every enrichment run in
    batch 2 was read from ~250 KB copies instead, and read them well enough to catch
    underinked copyright blocks, en-dash ISBNs and a broken C in a roman numeral.
    Reading the originals costs roughly twenty times the bytes for the same reading.

    This is a backfill. Books committed before the read-copy step existed have no
    small copies, so this walks the queue and makes them. The resize itself belongs
    in `commit` going forward, written from the same decode as the full-size frame.

WHAT IT TOUCHES
    Reads   data/books/<folder>/{1,2,3}.jpg
    Writes  data/_work/enrich-read/<folder>/{1,2,3}.jpg

    Nothing else. data/books/ is never modified. Gallery frames (IMG_*.jpg and
    anything else) are not copied, so the enrichment pass cannot read them by
    accident rather than by instruction.

SCOPE
    Driven off the keys in data/records_source.json, which is exactly the set of
    books awaiting enrichment. Books absent from that file are not touched.

ORIENTATION
    exif_transpose only. `commit` bakes rotation into the pixels, so for anything it
    wrote this is a no-op; for older folders carrying an EXIF orientation tag it is
    the correction. There is no rotate.json handling here on purpose — that sidecar
    belongs to prep_images.py and applying it in two places is how rotation bugs
    start.

USAGE
    python3 scripts/make_read_copies.py                      # every book in the records file
    python3 scripts/make_read_copies.py --only 0511-book     # one book, for a read test
    python3 scripts/make_read_copies.py --force              # rebuild copies that already exist
    python3 scripts/make_read_copies.py --max 2000 --quality 88

REQUIRES
    Pillow (already in requirements.txt, already used by prep_images.py)
"""
import os
import sys
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths

from PIL import Image, ImageOps

OUT_DIR = os.path.join(paths.WORK, "enrich-read")

# Only the three positional frames. prep_images.py maps these to front-cover,
# copyright and rear-cover, and they are the only frames the enrichment pass reads.
POSITIONS = ("1", "2", "3")

# Early folders are not uniformly .jpg — batch 2 had .jpeg in places. Output is
# always .jpg regardless of what the source was called.
SRC_EXTS = (".jpg", ".jpeg", ".JPG", ".JPEG")


def find_frame(book_dir, position):
    """Return the path of frame `position` in `book_dir`, or None."""
    for ext in SRC_EXTS:
        p = os.path.join(book_dir, position + ext)
        if os.path.isfile(p):
            return p
    return None


def shrink(src, dst, maxdim, quality):
    """Write a downscaled JPEG. Returns (bytes_in, bytes_out)."""
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode != "RGB":
            im = im.convert("RGB")
        w, h = im.size
        scale = min(1.0, maxdim / max(w, h))
        if scale < 1.0:
            im = im.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
        im.save(dst, "JPEG", quality=quality, optimize=True)
    return os.path.getsize(src), os.path.getsize(dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", default=paths.RECORDS_SOURCE,
                    help="records file whose keys define the scope")
    ap.add_argument("--source", default=paths.BOOKS)
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--only", action="append", default=None,
                    help="limit to this folder name; repeatable")
    ap.add_argument("--force", action="store_true",
                    help="rebuild copies that already exist")
    ap.add_argument("--max", type=int, default=1600, help="long edge in pixels")
    ap.add_argument("--quality", type=int, default=82)
    a = ap.parse_args()

    if not os.path.isfile(a.records):
        sys.exit(f"No records file at {a.records}")

    with open(a.records) as fh:
        keys = list(json.load(fh).keys())

    if a.only:
        wanted = set(a.only)
        unknown = wanted - set(keys)
        if unknown:
            sys.exit("Not in the records file: " + ", ".join(sorted(unknown)))
        keys = [k for k in keys if k in wanted]

    print(f"{len(keys)} book folder(s) in scope -> {a.out}")
    print(f"long edge {a.max}px, quality {a.quality}\n")

    made = skipped = 0
    bytes_in = bytes_out = 0
    missing_folder = []
    no_frames = []
    failed = []

    for name in keys:
        book_dir = os.path.join(a.source, name)
        if not os.path.isdir(book_dir):
            missing_folder.append(name)
            continue

        dst_dir = os.path.join(a.out, name)
        wrote_here = 0

        for pos in POSITIONS:
            src = find_frame(book_dir, pos)
            if src is None:
                continue
            dst = os.path.join(dst_dir, pos + ".jpg")
            if os.path.isfile(dst) and not a.force:
                skipped += 1
                wrote_here += 1
                continue
            os.makedirs(dst_dir, exist_ok=True)
            try:
                bi, bo = shrink(src, dst, a.max, a.quality)
            except Exception as e:
                failed.append(f"{name}/{os.path.basename(src)}: {e}")
                continue
            bytes_in += bi
            bytes_out += bo
            made += 1
            wrote_here += 1

        if wrote_here == 0:
            no_frames.append(name)

    def mb(n):
        return f"{n / 1_048_576:.1f} MB"

    print(f"frames written : {made}")
    if skipped:
        print(f"already present: {skipped}  (--force to rebuild)")
    if made:
        ratio = bytes_in / bytes_out if bytes_out else 0
        print(f"read  {mb(bytes_in)} -> wrote {mb(bytes_out)}   ({ratio:.0f}x smaller)")

    if no_frames:
        print(f"\n! {len(no_frames)} folder(s) with no 1/2/3 frame at all:")
        for n in no_frames[:10]:
            print("   ", n)
        if len(no_frames) > 10:
            print(f"    ... and {len(no_frames) - 10} more")

    if missing_folder:
        print(f"\n! {len(missing_folder)} record(s) with no folder in {a.source}:")
        for n in missing_folder[:10]:
            print("   ", n)
        if len(missing_folder) > 10:
            print(f"    ... and {len(missing_folder) - 10} more")

    if failed:
        print(f"\n! {len(failed)} frame(s) failed to convert:")
        for f in failed[:10]:
            print("   ", f)
        if len(failed) > 10:
            print(f"    ... and {len(failed) - 10} more")

    if missing_folder or failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
