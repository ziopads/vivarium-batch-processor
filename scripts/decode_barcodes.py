#!/usr/bin/env python3
"""
decode_barcodes.py — read ISBNs off rear-cover barcodes, locally, before enrichment.

WHY
    Most books after about 1970 carry their ISBN as an EAN-13 barcode. A decoder
    reads it deterministically and validates the check digit, so a misread almost
    always fails rather than producing a plausible wrong number. That is strictly
    better than any model reading digits by eye, and it costs nothing per book.

    Every book this clears is a book the vision pass never opens. Combined with
    resolve_isbns.py, a decoded barcode yields title and author without an image
    ever leaving the machine.

WHAT IT TOUCHES
    Reads   data/books/<folder>/3.jpg, then 2.jpg          (full-size originals)
    Writes  data/records_source.json                        isbn, isbn_status only

    Full-size on purpose. Barcodes are small in frame and the 1600px reading copies
    lose detail that matters here. There is no token cost to reading the originals
    locally.

    Records that already hold an isbn are skipped unless --force.

REQUIRES
    python3 -m pip install zxing-cpp

    Use the module form: `pip` is often not on PATH even when python3 is, and the
    module form guarantees the package lands in the interpreter that runs this
    script. If Homebrew python refuses with an externally-managed-environment
    error, add --user, then --break-system-packages.

    zxing-cpp ships wheels and needs no system library. pyzbar is used as a fallback
    if it happens to be installed, but is not required.

USAGE
    python3 scripts/decode_barcodes.py --dry-run
    python3 scripts/decode_barcodes.py
    python3 scripts/decode_barcodes.py --only 0511-book
"""
import os
import sys
import json
import shutil
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths

from PIL import Image

# Frames to try, in order. 3.jpg is the rear cover where the barcode lives; 2.jpg
# occasionally carries one on the copyright page. Front covers effectively never do.
FRAMES = ("3", "2")
SRC_EXTS = (".jpg", ".jpeg", ".JPG", ".JPEG")

# Barcodes photographed off a book are often rotated. Try the obvious angles.
ANGLES = (0, 90, 270, 180)


def load_decoder():
    """Return a callable taking a PIL image and yielding candidate digit strings."""
    try:
        import zxingcpp

        def decode(im):
            for r in zxingcpp.read_barcodes(im):
                if r.text:
                    yield r.text
        return decode, "zxing-cpp"
    except ImportError:
        pass

    try:
        from pyzbar.pyzbar import decode as pyzbar_decode

        def decode(im):
            for r in pyzbar_decode(im):
                try:
                    yield r.data.decode("ascii")
                except Exception:
                    continue
        return decode, "pyzbar"
    except ImportError:
        pass

    sys.exit(
        "No barcode decoder available.\n"
        "  pip install zxing-cpp\n"
        "(zxing-cpp needs no system library; pyzbar would need `brew install zbar`.)"
    )


def isbn13_ok(digits):
    """ISBN-13 check digit: alternating weights 1 and 3, sum divisible by 10."""
    if len(digits) != 13 or not digits.isdigit():
        return False
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits))
    return total % 10 == 0


def pick_isbn(candidates):
    """
    From raw barcode payloads, return the one real ISBN-13, or None.

    Books carry a 978/979 EAN-13. A second barcode alongside it is the five-digit
    price add-on, and some scanners return it concatenated. Anything not passing
    the check digit is discarded rather than repaired.
    """
    found = set()
    for raw in candidates:
        digits = "".join(c for c in raw if c.isdigit())
        for n in (digits, digits[:13]):
            if len(n) == 13 and n.startswith(("978", "979")) and isbn13_ok(n):
                found.add(n)
    if len(found) == 1:
        return found.pop()
    return None  # zero, or an ambiguous multiple — leave it for the vision pass


def find_frame(book_dir, position):
    for ext in SRC_EXTS:
        p = os.path.join(book_dir, position + ext)
        if os.path.isfile(p):
            return p
    return None


def scan(path, decode):
    """Try a frame at several rotations. Returns an ISBN-13 or None."""
    try:
        base = Image.open(path)
        base.load()
    except Exception:
        return None
    for angle in ANGLES:
        im = base if angle == 0 else base.rotate(angle, expand=True)
        try:
            isbn = pick_isbn(decode(im))
        except Exception:
            continue
        if isbn:
            return isbn
    return None


def dump(obj):
    return json.dumps(obj, indent=1, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", default=paths.RECORDS_SOURCE)
    ap.add_argument("--source", default=paths.BOOKS)
    ap.add_argument("--only", action="append", default=None)
    ap.add_argument("--force", action="store_true",
                    help="re-decode records that already hold an isbn")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    decode, engine = load_decoder()

    with open(a.records, "rb") as fh:
        raw = fh.read()
    records = json.loads(raw)
    if dump(records).encode("utf-8") != raw:
        sys.exit("records_source.json does not round-trip; investigate before writing.")

    keys = list(records)
    if a.only:
        keys = [k for k in keys if k in set(a.only)]

    print(f"decoder: {engine}")
    print(f"scope  : {len(keys)} record(s)\n")

    hit = miss = skipped = no_frame = 0
    by_frame = {"3": 0, "2": 0}

    for i, key in enumerate(keys, 1):
        rec = records[key]
        if (rec.get("isbn") or "").strip() and not a.force:
            skipped += 1
            continue

        book_dir = os.path.join(a.source, key)
        frames = [(f, find_frame(book_dir, f)) for f in FRAMES]
        frames = [(f, p) for f, p in frames if p]
        if not frames:
            no_frame += 1
            continue

        isbn = None
        for pos, path in frames:
            isbn = scan(path, decode)
            if isbn:
                by_frame[pos] += 1
                break

        if isbn:
            rec["isbn"] = isbn
            rec["isbn_status"] = "present"
            hit += 1
        else:
            miss += 1

        if i % 50 == 0:
            print(f"  ...{i}/{len(keys)}   decoded {hit}")

    print(f"\ndecoded          : {hit}")
    print(f"  from 3.jpg     : {by_frame['3']}")
    print(f"  from 2.jpg     : {by_frame['2']}")
    print(f"no barcode found : {miss}")
    print(f"no 2/3 frame     : {no_frame}")
    if skipped:
        print(f"already had isbn : {skipped}  (--force to redo)")

    if a.dry_run:
        print("\ndry run — nothing written")
        return

    if hit:
        shutil.copy2(a.records, a.records + ".bak")
        tmp = a.records + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(dump(records))
        os.replace(tmp, a.records)
        print(f"\nwritten: {a.records}")
    else:
        print("\nnothing decoded; file untouched")


if __name__ == "__main__":
    main()
