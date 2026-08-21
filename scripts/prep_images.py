#!/usr/bin/env python3
"""
prep_images.py  —  Stage 1 of the manual image pipeline (safe: touches NO app data).

Reads raw photos from an intake folder and writes resized, auto-oriented WEBP
(cover-size + thumbnail) into a parallel "image-ready" folder, ready to be applied.

FOLDER CONVENTION (one subfolder per item, inside data/intake/):
  - Folder named with a NUMBER  ->  an EXISTING item id.
        e.g.  data/intake/210/   or  data/intake/000210/
        (images will be ATTACHED to item 210 when you run apply_images.py)
  - Folder named with anything ELSE  ->  a NEW item to be created from the folder.
        e.g.  data/intake/new-vermeer-catalogue/
        (optionally drop a `title.txt` in the folder: line 1 = title, line 2 = author)

NAMING: images are sorted and given unique names 01-<slug>, 02-<slug>, ...
        If a source filename contains "cover" or "copyright" the word is kept in the
        name, so the app shows a sensible label (and you can pick cover/copyright there).

POSITIONAL CONVENTION (recommended for new batches — no title.txt needed):
        name the photos by position and they get meaningful labels automatically:
            1.jpg -> Front Cover   (also becomes the item's cover image)
            2.jpg -> Copyright      (title-page verso: publisher / year / ISBN)
            3.jpg -> Rear Cover     (optional; rear-cover ISBN barcode)
        Any other filename falls back to the slug behavior above.

USAGE:   python3 scripts/prep_images.py
         (set VIVARIUM_APP if the catalogue app isn't a sibling directory)

REQUIRES: pip3 install Pillow          (and, for iPhone HEIC files: pip3 install pillow-heif)
"""
import os, re, sys, json, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
from PIL import Image, ImageOps
try:
    import pillow_heif; pillow_heif.register_heif_opener()
except Exception:
    pass

INTAKE = paths.INTAKE
READY  = paths.READY
COVER_MAX, COVER_Q = 1400, 82
THUMB_MAX, THUMB_Q = 420, 80
EXTS = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp", ".bmp")

# Positional convention: a file whose name (sans extension) is exactly 1/2/3
# gets a meaningful label carried through to the app.
POSITIONAL = {"1": "front-cover", "2": "copyright", "3": "rear-cover"}

def slug(fname: str) -> str:
    stem = re.sub(r"\.[^.]+$", "", fname)
    if stem.strip() in POSITIONAL:                 # 1/2/3 -> labelled
        return POSITIONAL[stem.strip()]
    s = stem.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:40] or "image"

def save_webp(im: Image.Image, path: str, maxdim: int, q: int, angle: int = 0) -> None:
    im = ImageOps.exif_transpose(im)            # honor camera rotation
    # Sidecar correction from group_batch.py. CONVENTION: `angle` is degrees
    # CLOCKWISE required to make the image upright, measured AFTER exif_transpose.
    # PIL rotates counter-clockwise, hence the negation. Applied to the pixels here
    # because WebP does not carry orientation metadata reliably.
    if angle:
        im = im.rotate(-angle, expand=True)
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGB")
    w, h = im.size
    scale = min(1.0, maxdim / max(w, h))
    if scale < 1.0:
        im = im.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    if im.mode == "RGBA":
        im = im.convert("RGB")
    im.save(path, "WEBP", quality=q, method=6)

def process(name: str):
    src_dir = f"{INTAKE}/{name}"
    files = sorted(f for f in os.listdir(src_dir)
                   if f.lower().endswith(EXTS) and not f.startswith("."))
    # Optional rotation sidecar written by vivarium-batch-processor: {filename: degrees}
    rot = {}
    rot_path = f"{src_dir}/rotate.json"
    if os.path.exists(rot_path):
        try:
            rot = json.load(open(rot_path))
        except Exception as e:
            print(f"    ! ignoring bad rotate.json in {name}: {e}")
    out_dir = f"{READY}/{name}"
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)                  # regenerate cleanly
    os.makedirs(out_dir, exist_ok=True)
    made = 0
    for i, f in enumerate(files, start=1):
        base = f"{i:02d}-{slug(f)}"
        angle = int(rot.get(f, 0) or 0)
        try:
            with Image.open(f"{src_dir}/{f}") as im:
                save_webp(im.copy(), f"{out_dir}/{base}.webp", COVER_MAX, COVER_Q, angle)
                save_webp(im.copy(), f"{out_dir}/{base}-thumb.webp", THUMB_MAX, THUMB_Q, angle)
            made += 1
        except Exception as e:
            print(f"    ! skipped {f}: {e}")
    # carry a title.txt through for new-item folders
    tt = f"{src_dir}/title.txt"
    if os.path.exists(tt):
        shutil.copy(tt, f"{out_dir}/title.txt")
    return made

def main():
    if not os.path.isdir(INTAKE):
        os.makedirs(INTAKE, exist_ok=True)
        print(f"Created intake folder (it was empty):\n  {INTAKE}\nDrop item subfolders there and re-run.")
        return
    os.makedirs(READY, exist_ok=True)
    folders = sorted(d for d in os.listdir(INTAKE)
                     if os.path.isdir(f"{INTAKE}/{d}") and not d.startswith("."))
    if not folders:
        print(f"Nothing to process in {INTAKE}")
        return
    print(f"Prepping {len(folders)} folder(s)  ->  {READY}\n")
    for name in folders:
        made = process(name)
        kind = f"existing item #{int(name):06d}" if name.isdigit() else "NEW item"
        print(f"  {name:<28} {made:>3} image(s)   [{kind}]")
    print("\nDone. Review data/ready/, then run apply_images.py to ingest.")

if __name__ == "__main__":
    main()
