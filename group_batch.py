#!/usr/bin/env python3
"""
group_batch.py — turn a raw dump of iPhone book photos into grouped, role-labelled
book folders ready for vivarium/scripts/ingest_batch.py.

COMMANDS
    init      create the data/ tree
    group     read data/input/, propose a grouping.            READS ONLY.
    import    same, but for folders already one-per-book.      READS ONLY.
    review    drag-and-drop editor for the proposal in a browser.
    orient    set rotation by hand for books already in folders.
    commit    execute the grouping.                            MOVES FILES.
    archive   move handed-off books out of the staging pile.   MOVES FILES.
    status    what is where

`group` never moves or modifies anything. It writes data/_work/grouping.json — a
proposal — plus thumbnails and a static contact sheet. `review` opens that proposal
in a local browser UI where frames can be dragged between books, roles set, and
titles typed; Save writes grouping.json back to disk. Then `commit` renames the
originals to 1/2/3, moves them into data/books/NNNN-slug/, appends to
data/records_source.json, and empties data/input/.

Because the book counter and file hashes live in data/_work/ledger.json, successive
batches ACCUMULATE. Re-dropping photos already committed is caught by hash.

ORDERING
    Frames are ordered by the trailing number in the filename (IMG_0415 -> 415),
    NOT by EXIF timestamp. iPhone numbering is monotonic within a batch; EXIF
    DateTimeOriginal is not reliably so — re-exported or edited files can carry a
    shifted timestamp and get yanked out of sequence. Timestamps are still read,
    and used for gap detection, but any frame whose timestamp disagrees with its
    filename position is flagged rather than reordered.

ROTATION
    You set it in the review UI at step 4 and nothing else touches it. `commit` applies
    the angle to the pixels as it writes the file, so orientation is a property of the
    photograph from then on — no sidecar, nothing downstream to remember, nothing to
    fall out of sync. A frame at 0° is moved untouched and keeps its original bytes and
    format; only a rotated frame is re-encoded, and it lands as JPEG.

    CONVENTION: `rotate` is DEGREES CLOCKWISE required to make the frame upright,
    measured AFTER PIL's exif_transpose. Apply with im.rotate(-angle, expand=True).

    A thumbnail must never be pre-rotated. If it is, the UI's CSS turn applies on top
    of it, the card displays at twice the angle, and correcting what you see destroys
    the stored value. That is what automatic orientation detection cost here before it
    was removed, and it is why orientation now lives in exactly one place.

    prep_images.py still reads a rotate.json if it finds one. `commit` no longer writes
    them, but `orient` does — that is the repair path for images already handed off,
    and it is the only remaining producer.

FILE FORMATS
    `commit` writes JPEG. Every frame is decoded once, turned if you turned it, and
    saved at high quality — so data/books/ is uniformly readable by anything, and the
    HEIC an iPhone happens to produce stops being everyone else's problem. There is no
    second, converted copy of anything.

    Re-encoding does NOT affect duplicate detection. The ledger hash is taken from the
    file as dropped in data/input/, before anything moves it, and nothing ever
    recomputes a hash from data/books/. A re-dropped photo is still caught. (An earlier
    note here claimed otherwise and ruled out re-encoding on those grounds; it was
    wrong, and a pile of scratch-copy machinery existed to work around it.)

REQUIRES
    pip3 install Pillow pillow-heif
"""

import os
import re
import sys
import json
import shutil
import hashlib
import argparse
import datetime
import statistics
import webbrowser
import http.server
import socketserver
import urllib.parse

from PIL import Image, ImageOps

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HAVE_HEIF = True
except Exception:
    HAVE_HEIF = False


# ---------------------------------------------------------------- configuration

IMAGE_EXTS = (".heic", ".heif", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp")

THUMB_MAX = 500
STAT_MAX = 220

PAGE_MIN_BRIGHTNESS = 150
PAGE_MAX_SATURATION = 60
PAGE_MAX_INK = 0.25

GAP_MIN_SECONDS = 8.0
GAP_MULTIPLIER = 2.5
MAX_FRAMES_PER_BOOK = 8

# Quality for the JPEG `commit` writes. High and without chroma subsampling because
# this file is the archival original from here on — everything downstream is derived
# from it, and nothing else keeps a copy.
ARCHIVE_QUALITY = 95

ROLE_MAIN = "1"
ROLE_COPYRIGHT = "2"
ROLE_REAR = "3"
ROLES = (ROLE_MAIN, ROLE_COPYRIGHT, ROLE_REAR)


# ---------------------------------------------------------------------- helpers

def data_paths(root):
    d = os.path.join(root, "data")
    return {
        "data": d,
        "input": os.path.join(d, "input"),
        "books": os.path.join(d, "books"),
        "archive": os.path.join(d, "archive"),
        "work": os.path.join(d, "_work"),
        "thumbs": os.path.join(d, "_work", "thumbs"),
        "committed": os.path.join(d, "_work", "committed"),
        "rejected": os.path.join(d, "_work", "rejected"),
        "ledger": os.path.join(d, "_work", "ledger.json"),
        "grouping": os.path.join(d, "_work", "grouping.json"),
        "sheet": os.path.join(d, "_work", "contact-sheet.html"),
        "records": os.path.join(d, "records_source.json"),
    }


def ensure_dirs(P):
    for k in ("input", "books", "work", "thumbs", "committed", "rejected"):
        os.makedirs(P[k], exist_ok=True)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: {path} is not valid JSON ({e}).")


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)


def slugify(text, fallback="book"):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:40] or fallback


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sequence_number(filename):
    """Trailing digits of the stem: IMG_0415.jpeg -> 415. None when absent."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    matches = re.findall(r"\d+", stem)
    return int(matches[-1]) if matches else None


def scan_input(input_dir):
    found = []
    for dirpath, _dirnames, filenames in os.walk(input_dir):
        for name in sorted(filenames):
            if not name.startswith(".") and name.lower().endswith(IMAGE_EXTS):
                found.append(os.path.join(dirpath, name))
    return found


def exif_timestamp(im, path):
    try:
        exif = im.getexif()
        for tag in (36867, 36868, 306):
            raw = exif.get(tag)
            if raw:
                return datetime.datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return datetime.datetime.fromtimestamp(os.path.getmtime(path))


def downscale(im, maxdim):
    w, h = im.size
    scale = min(1.0, maxdim / max(w, h))
    if scale < 1.0:
        im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    return im


def detect_rotation(im_upright):
    """
    Removed. Orientation is set by hand in the review UI and nowhere else.

    Automatic detection read display type on covers badly, and its wrong angles were
    written into the thumbnails, which put the review UI a rotation ahead of the data
    and turned every correction into a deletion. Kept as a stub so nothing silently
    calls it again.
    """
    raise NotImplementedError("orientation is set in the review UI")


def page_statistics(im_upright):
    small = downscale(im_upright.convert("RGB"), STAT_MAX)
    grey = small.convert("L")
    pixels = list(grey.getdata())
    n = len(pixels) or 1
    brightness = sum(pixels) / n
    ink = sum(1 for p in pixels if p < 100) / n
    hsv = list(small.convert("HSV").getdata())
    saturation = sum(p[1] for p in hsv) / (len(hsv) or 1)
    return {
        "brightness": round(brightness, 1),
        "saturation": round(saturation, 1),
        "ink": round(ink, 3),
        "page_like": (brightness > PAGE_MIN_BRIGHTNESS
                      and saturation < PAGE_MAX_SATURATION
                      and ink < PAGE_MAX_INK),
    }


# ------------------------------------------------------------------ the proposal

def propose_books(frames):
    deltas = [f["gap_before"] for f in frames[1:] if f["gap_before"] is not None]
    positive = [d for d in deltas if d > 0]
    median = statistics.median(positive) if positive else 0.0
    threshold = max(GAP_MIN_SECONDS, GAP_MULTIPLIER * median)

    books, current = [], None
    for i, frame in enumerate(frames):
        if i == 0:
            start, why, conf = True, ["first frame"], "high"
        else:
            gap = frame["gap_before"]
            gap_sig = gap is not None and gap > threshold
            cover_sig = (not frame["page_like"]) and frames[i - 1]["page_like"]
            why = []
            if gap_sig:
                why.append(f"pause {gap:.0f}s > {threshold:.0f}s")
            if cover_sig:
                why.append("cover-like frame after a page")
            start = gap_sig or cover_sig
            conf = "high" if (gap_sig and cover_sig) else "low"
        if start:
            current = {"n": len(books) + 1, "title": "", "author": "",
                       "frames": [], "roles": {}, "confidence": conf,
                       "boundary_reason": "; ".join(why), "flags": [], "notes": ""}
            books.append(current)
        current["frames"].append(frame["idx"])

    by_idx = {f["idx"]: f for f in frames}
    for book in books:
        idxs = book["frames"]
        book["roles"][ROLE_MAIN] = idxs[0]
        copyright_idx = next((i for i in idxs[1:] if by_idx[i]["page_like"]), None)
        if copyright_idx is not None:
            book["roles"][ROLE_COPYRIGHT] = copyright_idx
        else:
            book["flags"].append("copyright page not identified")
        if len(idxs) > MAX_FRAMES_PER_BOOK:
            book["flags"].append(f"{len(idxs)} frames — possible missed boundary")
        if len(idxs) == 1:
            book["flags"].append("single frame")
        for i in idxs:
            fr = by_idx[i]
            if fr.get("time_out_of_sequence"):
                book["flags"].append(f"frame {i}: EXIF time disagrees with filename order")
    return books


# -------------------------------------------------------------------- reporting

def write_contact_sheet(P, grouping):
    by_idx = {f["idx"]: f for f in grouping["frames"]}
    role_of = {}
    for book in grouping["books"]:
        for role, idx in book["roles"].items():
            role_of[idx] = role

    rows = []
    start = grouping.get("start_number", 1)
    for n, book in enumerate(grouping["books"]):
        flags = "".join(f"<li>{f}</li>" for f in book.get("flags", []))
        cells = []
        for idx in book["frames"]:
            fr = by_idx.get(idx)
            if not fr:
                continue
            role = role_of.get(idx)
            badge = f'<span class="role r{role}">{role}</span>' if role else ""
            cells.append(
                f'<figure class="frame">{badge}'
                f'<img src="thumbs/{fr["thumb"]}" loading="lazy">'
                f'<figcaption>{idx} &middot; {fr["file"]}<br>'
                f'{"page" if fr["page_like"] else "cover"}</figcaption></figure>')
        title = book["title"] or "<em>untitled</em>"
        rows.append(f"""
        <section class="book {book.get('confidence','')}">
          <header><h2>#{start + n:04d} &nbsp; {title}</h2>
          <p class="meta">{book.get('confidence','')} &middot; {book.get('boundary_reason','')}</p>
          {'<ul class="flags">' + flags + '</ul>' if flags else ''}</header>
          <div class="strip">{''.join(cells)}</div>
        </section>""")

    html = f"""<!doctype html><meta charset="utf-8"><title>Batch contact sheet</title>
<style>
 body {{ font: 14px/1.5 -apple-system, system-ui, sans-serif; margin: 2rem; color: #1a1a1a; }}
 .book {{ border-top: 1px solid #e5e5e5; padding: 1.25rem 0; }}
 .book.low {{ background: #fffbf0; }}
 .book h2 {{ font-size: 15px; margin: 0; }}
 .meta {{ font-size: 11px; letter-spacing: .06em; text-transform: uppercase; color: #999; }}
 .flags {{ color: #a33; font-size: 12px; }}
 .strip {{ display: flex; gap: .75rem; flex-wrap: wrap; }}
 .frame {{ margin: 0; width: 150px; position: relative; }}
 .frame img {{ width: 100%; border: 1px solid #e5e5e5; display: block; }}
 figcaption {{ font-size: 10px; color: #777; }}
 .role {{ position: absolute; top: 4px; left: 4px; background: #1a1a1a; color: #fff;
          font-size: 11px; padding: 1px 6px; border-radius: 2px; }}
 .role.r2 {{ background: #557; }} .role.r3 {{ background: #777; }}
</style>
<h1>Batch contact sheet</h1>
<p>{len(grouping['books'])} books from {len(grouping['frames'])} frames &middot;
 numbering from #{grouping.get('start_number', 1):04d} &middot;
 generated {grouping['generated']}</p>
{''.join(rows)}"""
    with open(P["sheet"], "w") as fh:
        fh.write(html)


# ------------------------------------------------------------------- validation

def validate_grouping(grouping):
    """Shared by `review` (on save) and `commit`. Returns a list of error strings."""
    by_idx = {f["idx"] for f in grouping["frames"]}
    dropped = set(grouping.get("dropped", []))
    errors, seen = [], {}

    for n, book in enumerate(grouping["books"], 1):
        if not book.get("frames"):
            errors.append(f"book {n}: no frames")
        for i in book.get("frames", []):
            if i not in by_idx:
                errors.append(f"book {n}: frame {i} does not exist")
            elif i in seen:
                errors.append(f"frame {i} is in both book {seen[i]} and book {n}")
            else:
                seen[i] = n
        for role, i in book.get("roles", {}).items():
            if role not in ROLES:
                errors.append(f"book {n}: unknown role {role!r}")
            if i not in book.get("frames", []):
                errors.append(f"book {n}: role {role} points at frame {i}, "
                              f"which is not in that book")
        if ROLE_MAIN not in book.get("roles", {}):
            errors.append(f"book {n}: no role 1 (title/author frame)")

    orphans = [i for i in by_idx if i not in seen and i not in dropped]
    if orphans:
        errors.append(f"frames in no book and not dropped: {sorted(orphans)}")
    return errors


# ------------------------------------------------------------------- the review UI

REVIEW_HTML = r"""<!doctype html>
<meta charset="utf-8"><title>Review grouping</title>
<style>
 * { box-sizing: border-box; }
 body { font: 14px/1.5 -apple-system, system-ui, sans-serif; margin: 0;
        color: #1a1a1a; background: #fafafa; padding-bottom: 6rem; }
 header.top { position: sticky; top: 0; z-index: 20; background: #fff;
        border-bottom: 1px solid #e5e5e5; padding: .75rem 1.5rem;
        display: flex; align-items: center; gap: 1rem; }
 h1 { font-size: 15px; font-weight: 600; margin: 0; }
 .count { font-size: 12px; color: #777; }
 button { font: inherit; font-size: 13px; padding: .35rem .8rem; border-radius: 4px;
          border: 1px solid #1a1a1a; background: #fff; cursor: pointer; }
 button:hover { background: #1a1a1a; color: #fff; }
 button.primary { background: #1a1a1a; color: #fff; }
 button.ghost { border-color: #ccc; color: #666; font-size: 12px; padding: .2rem .5rem; }
 #status { margin-left: auto; font-size: 12px; color: #666; }
 main { padding: 1.5rem; }
 .book { background: #fff; border: 1px solid #e5e5e5; border-radius: 6px;
         margin-bottom: .9rem; padding: .8rem 1rem;
         /* Skip layout and paint for books scrolled out of view. With a thousand
            books this is the difference between a usable page and a stalled one. */
         content-visibility: auto; contain-intrinsic-size: auto 300px; }
 .book.hidden { display: none; }
 body.nocaps .cap { display: none; }
 .book.dragover { border-color: #1a1a1a; box-shadow: 0 0 0 2px rgba(0,0,0,.06); }
 .bh { display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; }
 .num { font-size: 12px; letter-spacing: .06em; color: #999; min-width: 3.2rem; }
 input.t { font: inherit; border: none; border-bottom: 1px solid #e5e5e5;
           padding: .15rem 0; min-width: 16rem; flex: 1; background: transparent; }
 input.a { font: inherit; font-size: 13px; color: #666; border: none;
           border-bottom: 1px solid #e5e5e5; padding: .15rem 0; min-width: 10rem;
           background: transparent; }
 input.t:focus, input.a:focus { outline: none; border-bottom-color: #1a1a1a; }
 .flags { font-size: 11px; color: #a33; margin: .3rem 0 0; }
 .strip { display: flex; gap: .5rem; flex-wrap: wrap; margin-top: .6rem;
          min-height: 90px; padding: .3rem; border-radius: 4px; }
 .card { width: 108px; position: relative; cursor: grab; user-select: none; }
 .card.dragging { opacity: .35; }
 .card img { width: 100%; height: 140px; object-fit: contain; background: #f4f1ec;
             display: block; border: 1px solid #e5e5e5; border-radius: 3px;
             transition: transform .12s ease; }
 .card img.r90  { transform: rotate(90deg)  scale(.72); }
 .card img.r180 { transform: rotate(180deg); }
 .card img.r270 { transform: rotate(270deg) scale(.72); }
 .ang { position: absolute; top: 4px; right: 4px; font-size: 10px; color: #fff;
        background: #557; border-radius: 2px; padding: 0 4px; }
 .card.insert-before { box-shadow: -3px 0 0 #1a1a1a; }
 .cap { font-size: 9px; color: #888; margin-top: .15rem; line-height: 1.3;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
 .roles { position: absolute; top: 4px; left: 4px; display: flex; gap: 2px; }
 .rbtn { width: 19px; height: 19px; border-radius: 3px; background: rgba(255,255,255,.9);
         border: 1px solid #ccc; color: #aaa; font-size: 11px; line-height: 17px;
         text-align: center; cursor: pointer; }
 .rbtn:hover { border-color: #1a1a1a; color: #1a1a1a; }
 .rbtn.on { background: #1a1a1a; color: #fff; border-color: #1a1a1a; }
 .rbtn.on.r2 { background: #557; border-color: #557; }
 .rbtn.on.r3 { background: #888; border-color: #888; }
 .tools { position: absolute; bottom: 22px; right: 3px; display: flex; gap: 2px; }
 .tools button { padding: 0 4px; font-size: 10px; line-height: 16px;
                 border-color: #ccc; background: rgba(255,255,255,.95); }
 .zone { border: 1px dashed #ccc; border-radius: 6px; padding: 1rem; text-align: center;
         color: #999; font-size: 12px; margin-bottom: .9rem; }
 .zone.dragover { border-color: #1a1a1a; color: #1a1a1a; background: #fff; }
 .zone.drop { border-color: #d99; color: #a33; }
 .legend { font-size: 12px; color: #777; margin-bottom: 1rem; line-height: 1.7; }
 .err { background: #fff4f4; border: 1px solid #e5b5b5; color: #a33; padding: .6rem .9rem;
        border-radius: 4px; margin-bottom: 1rem; font-size: 13px; white-space: pre-wrap; }
</style>

<header class="top">
  <h1>Review grouping</h1>
  <span class="count" id="count"></span>
  <button class="ghost" id="filter" title="show only books with flags">all books</button>
  <button class="ghost" id="collapse">toggle captions</button>
  <span id="status"></span>
  <button class="primary" id="save">Save</button>
</header>

<main>
  <p class="legend">
    Drag a frame onto another book to move it, or onto a card to insert before it.
    Each frame carries three role buttons: <b>1</b> title/author, <b>2</b> copyright,
    <b>3</b> rear. Click one to set it directly, click it again to clear it — setting a
    role takes it from whichever frame in that book held it.
    <b>&#9002;</b> splits the book at that frame. <b>&uarr;</b> merges a book into the one above.
    <b>&#10227;</b> on a frame rotates it 90&deg; clockwise; <b>&times;</b> drops it from the batch.
    Rotation is applied to the pixels at commit, so what you set here is what the file
    becomes.
    The number on each book is the folder it will be committed as — the same number the
    dry run prints and the same one that ends up on disk.
  </p>
  <div id="errors"></div>
  <div id="books"></div>
  <div class="zone" id="newbook">drag here to start a new book at the end</div>
  <div class="zone drop" id="dropzone">drag here to drop a frame from the batch (bad shot, stray)</div>
</main>

<script>
let G = null, FR = {};
// The number each book will actually be given on disk. `commit` numbers from the
// ledger, so the first book in this list becomes NNNN-slug where NNNN is
// start_number. Showing position instead meant the UI, the dry run and the folder
// name were three different numbers for the same book.
let START_N = 1;
const ROLE_KEYS = ['1', '2', '3'];

// book object -> its <section>. Rebuilding the whole list on every click was fine at
// forty books and unusable at a thousand: each interaction discarded and recreated
// every node on the page and threw away your scroll position. Everything below
// touches only the books that actually changed.
const EL = new Map();
let flaggedOnly = false;

const host = () => document.getElementById('books');
const posOf = (book) => G.books.indexOf(book);
const numOf = (book) => START_N + posOf(book);
const tag = (n) => '#' + String(n).padStart(4, '0');

async function boot() {
  G = await (await fetch('grouping.json?t=' + Date.now())).json();
  G.dropped = G.dropped || [];
  START_N = G.start_number || 1;
  FR = {};
  for (const f of G.frames) FR[f.idx] = f;
  G.books.forEach(fixRoles);
  renderAll();
}

// ---- rendering ------------------------------------------------------------

function renderAll() {
  const h = host();
  h.innerHTML = '';
  EL.clear();
  const frag = document.createDocumentFragment();
  for (const b of G.books) {
    const el = buildBook(b);
    EL.set(b, el);
    frag.appendChild(el);
  }
  h.appendChild(frag);
  renumber();
  applyFilter();
  updateHeader();
}

// Replace one book's element in place. This is the workhorse: a role click, a drag,
// a split all touch one or two books, never the list.
function refresh(book) {
  const old = EL.get(book);
  const el = buildBook(book);
  EL.set(book, el);
  if (old && old.parentNode) old.parentNode.replaceChild(el, old);
  else host().appendChild(el);
  setNum(book);
  filterOne(book);
  return el;
}

function setNum(book) {
  const el = EL.get(book);
  if (el) el.querySelector('.num').textContent = tag(numOf(book));
}

// Numbers shift whenever a book is added or removed. Rewriting one text node per
// book is cheap; rebuilding the elements would not be.
function renumber() {
  G.books.forEach((b, i) => {
    b.n = i + 1;
    const el = EL.get(b);
    if (el) el.querySelector('.num').textContent = tag(START_N + i);
  });
}

function updateHeader() {
  document.getElementById('count').textContent =
    G.books.length + ' books · ' + G.frames.length + ' frames' +
    (G.dropped.length ? ' · ' + G.dropped.length + ' dropped' : '') +
    ' · numbering from ' + tag(START_N);
}

function filterOne(book) {
  const el = EL.get(book);
  if (!el) return;
  const hide = flaggedOnly && !(book.flags && book.flags.length);
  el.classList.toggle('hidden', hide);
}

function applyFilter() {
  G.books.forEach(filterOne);
  const btn = document.getElementById('filter');
  const flagged = G.books.filter(b => b.flags && b.flags.length).length;
  btn.textContent = flaggedOnly ? 'flagged only (' + flagged + ')' : 'all books';
  btn.classList.toggle('primary', flaggedOnly);
}

// ---- building -------------------------------------------------------------

function buildBook(book) {
  const el = document.createElement('section');
  el.className = 'book';

  const head = document.createElement('div');
  head.className = 'bh';

  const num = document.createElement('span');
  num.className = 'num';
  num.title = 'the folder number this book will be committed as';
  num.textContent = tag(numOf(book));
  head.appendChild(num);

  const t = document.createElement('input');
  t.className = 't'; t.placeholder = 'title'; t.value = book.title || '';
  t.oninput = () => { book.title = t.value; };
  head.appendChild(t);

  const a = document.createElement('input');
  a.className = 'a'; a.placeholder = 'author'; a.value = book.author || '';
  a.oninput = () => { book.author = a.value; };
  head.appendChild(a);

  const up = document.createElement('button');
  up.className = 'ghost'; up.textContent = '↑ merge up';
  up.title = 'merge into previous book';
  up.onclick = () => {
    const at = posOf(book);
    if (at <= 0) return;
    const prev = G.books[at - 1];
    prev.frames = prev.frames.concat(book.frames);
    Object.assign(prev.roles, {});                 // keep prev's own roles
    G.books.splice(at, 1);
    dropElement(book);
    fixRoles(prev);
    refresh(prev);
    renumber();
    updateHeader();
  };
  head.appendChild(up);
  if (posOf(book) === 0) up.style.visibility = 'hidden';

  el.appendChild(head);

  if (book.flags && book.flags.length) {
    const f = document.createElement('p');
    f.className = 'flags'; f.textContent = book.flags.join(' · ');
    el.appendChild(f);
  }

  const strip = document.createElement('div');
  strip.className = 'strip';
  book.frames.forEach(i => strip.appendChild(buildCard(i, book)));
  strip.ondragover = e => { e.preventDefault(); el.classList.add('dragover'); };
  strip.ondragleave = () => el.classList.remove('dragover');
  strip.ondrop = e => {
    e.preventDefault(); el.classList.remove('dragover');
    moveFrame(Number(e.dataTransfer.getData('text/plain')), book, null);
  };
  el.appendChild(strip);
  return el;
}

function buildCard(idx, book) {
  const fr = FR[idx];
  const card = document.createElement('figure');
  card.className = 'card'; card.draggable = true; card.dataset.idx = idx;

  const img = document.createElement('img');
  img.src = 'thumbs/' + fr.thumb; img.loading = 'lazy'; img.decoding = 'async';
  if (fr.rotate) img.className = 'r' + fr.rotate;
  card.appendChild(img);

  // Angle badge. Rotation is DATA, never a re-encode: it lands in the frame's
  // `rotate` field, is written to rotate.json at commit, and is applied once by
  // prep_images.py during the webp conversion.
  const ang = document.createElement('span');
  ang.className = 'ang';
  ang.textContent = fr.rotate ? fr.rotate + '\u00b0' : '';
  ang.style.display = fr.rotate ? '' : 'none';
  card.appendChild(ang);

  const role = roleOf(book, idx);
  const roleBar = document.createElement('div');
  roleBar.className = 'roles';
  const titles = { '1': 'title / author page', '2': 'copyright page', '3': 'rear cover' };
  for (const r of ROLE_KEYS) {
    const b = document.createElement('div');
    b.className = 'rbtn r' + r + (role === r ? ' on' : '');
    b.textContent = r;
    b.title = titles[r] + ' — click to set, click again to clear';
    b.onclick = e => {
      e.stopPropagation();
      if (book.roles[r] === idx) {
        delete book.roles[r];                      // clicking the set role clears it
      } else {
        // this frame can hold only one role, and a role only one frame
        for (const other of ROLE_KEYS)
          if (book.roles[other] === idx) delete book.roles[other];
        book.roles[r] = idx;
      }
      fixRoles(book);
      refresh(book);                               // one book, not the list
    };
    roleBar.appendChild(b);
  }
  card.appendChild(roleBar);

  const tools = document.createElement('div');
  tools.className = 'tools';

  const rot = document.createElement('button');
  rot.textContent = '\u27f3'; rot.title = 'rotate 90\u00b0 clockwise';
  rot.onclick = e => {
    e.stopPropagation();
    fr.rotate = ((fr.rotate || 0) + 90) % 360;
    img.className = fr.rotate ? 'r' + fr.rotate : '';
    ang.textContent = fr.rotate ? fr.rotate + '\u00b0' : '';
    ang.style.display = fr.rotate ? '' : 'none';
  };
  tools.appendChild(rot);

  const del = document.createElement('button');
  del.textContent = '\u00d7'; del.title = 'drop this frame from the batch';
  del.onclick = e => {
    e.stopPropagation();
    const touched = detach(idx);
    G.dropped.push(idx);
    const removed = pruneEmpty(touched);
    touched.forEach(b => { fixRoles(b); refresh(b); });
    if (removed) renumber();
    updateHeader();
  };
  tools.appendChild(del);

  if (book.frames.indexOf(idx) > 0) {
    const sp = document.createElement('button');
    sp.textContent = '❯'; sp.title = 'split into a new book starting here';
    sp.onclick = e => {
      e.stopPropagation();
      const at = book.frames.indexOf(idx);
      const tail = book.frames.splice(at);
      for (const r of ROLE_KEYS)
        if (tail.indexOf(book.roles[r]) >= 0) delete book.roles[r];
      const fresh = { n: 0, title: '', author: '',
                      frames: tail, roles: {}, flags: [], notes: '' };
      const pos = posOf(book);
      G.books.splice(pos + 1, 0, fresh);
      fixRoles(book); fixRoles(fresh);
      refresh(book);
      const el = buildBook(fresh);
      EL.set(fresh, el);
      EL.get(book).insertAdjacentElement('afterend', el);
      filterOne(fresh);
      renumber();
      updateHeader();
    };
    tools.appendChild(sp);
  }
  card.appendChild(tools);

  const cap = document.createElement('figcaption');
  cap.className = 'cap';
  cap.textContent = idx + ' · ' + fr.file + ' · ' + (fr.page_like ? 'page' : 'cover');
  card.appendChild(cap);

  card.ondragstart = e => {
    e.dataTransfer.setData('text/plain', String(idx));
    card.classList.add('dragging');
  };
  card.ondragend = () => card.classList.remove('dragging');
  card.ondragover = e => { e.preventDefault(); e.stopPropagation(); card.classList.add('insert-before'); };
  card.ondragleave = () => card.classList.remove('insert-before');
  card.ondrop = e => {
    e.preventDefault(); e.stopPropagation();
    card.classList.remove('insert-before');
    moveFrame(Number(e.dataTransfer.getData('text/plain')), book, idx);
  };
  return card;
}

// ---- state ----------------------------------------------------------------

function roleOf(book, idx) {
  for (const r of ROLE_KEYS) if (book.roles[r] === idx) return r;
  return null;
}

// Flags are advisory and must describe the book AS IT NOW STANDS. They were
// originally computed once at `group` time and written into grouping.json, so a book
// you split, merged, or gave a role 2 kept warning about a state that no longer
// existed. Everything except the EXIF-ordering note is derivable from current state,
// so recompute those three and keep the one that isn't.
const MAX_FRAMES_PER_BOOK = 8;   // must match group_batch.py

function recomputeFlags(b) {
  const out = (b.flags || []).filter(f => /EXIF time/.test(f));
  if (!b.roles['2']) out.push('copyright page not identified');
  if (b.frames.length > MAX_FRAMES_PER_BOOK)
    out.push(b.frames.length + ' frames — possible missed boundary');
  if (b.frames.length === 1) out.push('single frame');
  b.flags = out;
}

function fixRoles(b) {
  // a book always needs a role 1; default it to its first frame
  if (b.frames.length && (!b.roles['1'] || b.frames.indexOf(b.roles['1']) < 0))
    b.roles['1'] = b.frames[0];
  for (const r of ['2', '3'])
    if (b.roles[r] && b.frames.indexOf(b.roles[r]) < 0) delete b.roles[r];
  recomputeFlags(b);
}

function dropElement(book) {
  const el = EL.get(book);
  if (el && el.parentNode) el.parentNode.removeChild(el);
  EL.delete(book);
}

// Returns the books whose contents changed, so the caller refreshes only those.
function detach(i) {
  const touched = new Set();
  for (const b of G.books) {
    const at = b.frames.indexOf(i);
    if (at >= 0) {
      b.frames.splice(at, 1);
      for (const r of ROLE_KEYS) if (b.roles[r] === i) delete b.roles[r];
      touched.add(b);
    }
  }
  const d = G.dropped.indexOf(i);
  if (d >= 0) G.dropped.splice(d, 1);
  return touched;
}

function pruneEmpty(touched) {
  let removed = false;
  for (let k = G.books.length - 1; k >= 0; k--) {
    const b = G.books[k];
    if (!b.frames.length) {
      G.books.splice(k, 1);
      dropElement(b);
      touched.delete(b);
      removed = true;
    }
  }
  return removed;
}

function moveFrame(i, targetBook, beforeIdx) {
  if (!Number.isFinite(i)) return;
  const touched = detach(i);
  if (targetBook && G.books.indexOf(targetBook) >= 0) {
    const at = beforeIdx == null ? targetBook.frames.length
                                 : targetBook.frames.indexOf(beforeIdx);
    targetBook.frames.splice(at < 0 ? targetBook.frames.length : at, 0, i);
    touched.add(targetBook);
  }
  const removed = pruneEmpty(touched);
  touched.forEach(b => { fixRoles(b); refresh(b); });
  if (removed) renumber();
  updateHeader();
}

// ---- page-level controls --------------------------------------------------

const newbook = document.getElementById('newbook');
newbook.ondragover = e => { e.preventDefault(); e.currentTarget.classList.add('dragover'); };
newbook.ondragleave = e => e.currentTarget.classList.remove('dragover');
newbook.ondrop = e => {
  e.preventDefault(); e.currentTarget.classList.remove('dragover');
  const i = Number(e.dataTransfer.getData('text/plain'));
  if (!Number.isFinite(i)) return;
  const touched = detach(i);
  const fresh = { n: 0, title: '', author: '',
                  frames: [i], roles: {}, flags: [], notes: '' };
  G.books.push(fresh);
  fixRoles(fresh);
  const removed = pruneEmpty(touched);
  touched.forEach(b => { fixRoles(b); refresh(b); });
  const el = buildBook(fresh);
  EL.set(fresh, el);
  host().appendChild(el);
  filterOne(fresh);
  if (removed) renumber(); else setNum(fresh);
  updateHeader();
};

const dropzone = document.getElementById('dropzone');
dropzone.ondragover = e => { e.preventDefault(); e.currentTarget.classList.add('dragover'); };
dropzone.ondragleave = e => e.currentTarget.classList.remove('dragover');
dropzone.ondrop = e => {
  e.preventDefault(); e.currentTarget.classList.remove('dragover');
  const i = Number(e.dataTransfer.getData('text/plain'));
  if (!Number.isFinite(i)) return;
  const touched = detach(i);
  G.dropped.push(i);
  const removed = pruneEmpty(touched);
  touched.forEach(b => { fixRoles(b); refresh(b); });
  if (removed) renumber();
  updateHeader();
};

// Captions are always built now and hidden with a class — instant, no rebuild.
document.getElementById('collapse').onclick = () =>
  document.body.classList.toggle('nocaps');

document.getElementById('filter').onclick = () => {
  flaggedOnly = !flaggedOnly;
  applyFilter();
  window.scrollTo(0, 0);
};

document.getElementById('save').onclick = async () => {
  const touched = new Set();
  pruneEmpty(touched);
  G.books.forEach(fixRoles);
  renumber();
  const s = document.getElementById('status');
  s.textContent = 'saving…';
  const res = await fetch('grouping.json', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(G)
  });
  const out = await res.json();
  const box = document.getElementById('errors');
  if (out.ok) {
    box.innerHTML = '';
    s.textContent = 'saved ' + new Date().toLocaleTimeString();
  } else {
    box.innerHTML = '<div class="err">Not saved — ' + out.errors.join('\n') + '</div>';
    s.textContent = 'not saved';
    window.scrollTo(0, 0);
  }
};

boot();
</script>
"""


def cmd_review(args):
    P = data_paths(args.root)
    if not os.path.exists(P["grouping"]):
        sys.exit("No grouping to review — run `group` first.")

    work_dir = P["work"]

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=work_dir, **kw)

        def log_message(self, *a):
            pass

        def _json(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/index.html"):
                body = REVIEW_HTML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            if path != "/grouping.json":
                self._json(404, {"ok": False, "errors": ["unknown endpoint"]})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                incoming = json.loads(self.rfile.read(length))
            except Exception as e:
                self._json(400, {"ok": False, "errors": [f"bad payload: {e}"]})
                return

            errors = validate_grouping(incoming)
            if errors:
                self._json(200, {"ok": False, "errors": errors})
                return

            shutil.copy(P["grouping"], P["grouping"] + ".bak")
            save_json(P["grouping"], incoming)
            write_contact_sheet(P, incoming)
            print(f"  saved — {len(incoming['books'])} books"
                  + (f", {len(incoming.get('dropped', []))} dropped"
                     if incoming.get("dropped") else ""))
            self._json(200, {"ok": True})

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", args.port), Handler) as httpd:
        url = f"http://127.0.0.1:{args.port}/"
        print(f"review UI at {url}   (ctrl-C when done)")
        if not args.no_open:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped.")


# --------------------------------------------------------------------- commands

def cmd_init(args):
    P = data_paths(args.root)
    ensure_dirs(P)
    if not os.path.exists(P["ledger"]):
        save_json(P["ledger"], {"next": 1, "hashes": {}, "batches": []})
    if not os.path.exists(P["records"]):
        save_json(P["records"], {})
    print(f"ready:\n  drop photos in  {P['input']}\n  books land in   {P['books']}")
    if not HAVE_HEIF:
        print("  ! pillow-heif missing — HEIC files will be skipped")


def cmd_group(args):
    P = data_paths(args.root)
    ensure_dirs(P)
    ledger = load_json(P["ledger"], {"next": 1, "hashes": {}, "batches": []})

    if os.path.exists(P["grouping"]) and not args.force:
        sys.exit(f"ERROR: an uncommitted grouping exists at\n  {P['grouping']}\n"
                 f"       Commit it, delete it, or re-run with --force to discard it.")

    paths = scan_input(P["input"])
    if not paths:
        sys.exit(f"Nothing to group — {P['input']} is empty.")

    # ---- pass one: cheap metadata only, so ordering is settled before thumbnails
    print(f"scanning {len(paths)} file(s) ...")
    entries, excluded = [], []
    for path in paths:
        name = os.path.basename(path)
        digest = sha256_of(path)
        if digest in ledger["hashes"]:
            excluded.append({"file": name,
                             "reason": f"already committed to {ledger['hashes'][digest]['book']}"})
            continue
        try:
            with Image.open(path) as raw:
                timestamp = exif_timestamp(raw, path)
        except Exception as e:
            excluded.append({"file": name, "reason": f"unreadable ({e})"})
            continue
        entries.append({"path": path, "file": name, "sha256": digest,
                        "ts": timestamp, "seq": sequence_number(name)})

    if not entries:
        sys.exit("Nothing groupable — every file was excluded.")

    # Filename sequence is the ordering authority; timestamp only breaks ties and
    # orders anything without a number in its name.
    entries.sort(key=lambda e: ((0, e["seq"]) if e["seq"] is not None else (1, 0), e["ts"], e["file"]))

    # ---- pass two: decode, measure, thumbnail — now with final indices
    frames = []
    for i, e in enumerate(entries, 1):
        with Image.open(e["path"]) as raw:
            upright = ImageOps.exif_transpose(raw.copy())
        stats = page_statistics(upright)

        # The thumbnail is the frame AS SHOT. The review UI is the only thing that
        # turns it, with CSS, so the card and the saved angle can never disagree.
        thumb_name = f"f{i:04d}__{os.path.splitext(e['file'])[0]}.jpg"
        downscale(upright.convert("RGB"), THUMB_MAX).save(
            os.path.join(P["thumbs"], thumb_name), "JPEG", quality=80)

        frames.append({
            "idx": i, "file": e["file"],
            "source": os.path.relpath(e["path"], P["input"]),
            "sha256": e["sha256"], "thumb": thumb_name,
            "taken": e["ts"].isoformat(), "_ts": e["ts"],
            "rotate": 0,
            "gap_before": None, "time_out_of_sequence": False,
            **stats,
        })

    # Gaps come from timestamps, but a negative gap means the EXIF clock disagrees
    # with filename order. Flag it and withhold it from boundary detection rather
    # than letting a bad timestamp invent a pause.
    out_of_sequence = 0
    for i, frame in enumerate(frames):
        if i == 0:
            continue
        delta = (frame["_ts"] - frames[i - 1]["_ts"]).total_seconds()
        if delta < 0:
            frame["time_out_of_sequence"] = True
            out_of_sequence += 1
        else:
            frame["gap_before"] = round(delta, 1)
    for frame in frames:
        frame.pop("_ts")

    books = propose_books(frames)
    grouping = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "start_number": ledger["next"],
        "frames": frames, "books": books, "dropped": [], "excluded": excluded,
    }
    save_json(P["grouping"], grouping)
    write_contact_sheet(P, grouping)

    low = sum(1 for b in books if b["confidence"] == "low")
    print(f"\n  {len(frames)} frames -> {len(books)} proposed books "
          f"({low} low-confidence, numbering from {ledger['next']:04d})")
    if out_of_sequence:
        print(f"  ! {out_of_sequence} frame(s) have an EXIF time that disagrees with "
              f"filename order — kept in filename order, flagged on their book")
    for item in excluded:
        print(f"  ! skipped {item['file']}: {item['reason']}")
    print(f"\n  review: python3 group_batch.py review")


def cmd_import(args):
    """
    Build a grouping from folders that are ALREADY one-per-book.

    `group` derives boundaries from timestamp gaps and page statistics because it is
    handed a flat pile. When the sorting has already been done by hand — a folder per
    book — those heuristics can only make it worse, so this takes the folder structure
    as authoritative and skips boundary detection entirely.

    Roles come from filenames where they exist (1.jpeg -> title page, 2.jpeg ->
    copyright). Folders without them fall back to position, first frame as 1 and second
    as 2, and are FLAGGED so the review UI can show you which assignments were guessed.

    Everything downstream is unchanged: same grouping.json, same review UI, same commit.
    """
    P = data_paths(args.root)
    ensure_dirs(P)
    ledger = load_json(P["ledger"], {"next": 1, "hashes": {}, "batches": []})

    if os.path.exists(P["grouping"]) and not args.force:
        sys.exit(f"ERROR: an uncommitted grouping exists at\n  {P['grouping']}\n"
                 f"       Commit it, delete it, or re-run with --force to discard it.")

    # one level of subdirectories under input/, each holding one book
    parents = {}
    for path in scan_input(P["input"]):
        rel = os.path.relpath(path, P["input"])
        folder = os.path.dirname(rel)
        if not folder:
            continue          # loose files at the top level aren't a book
        parents.setdefault(folder, []).append(path)

    if not parents:
        sys.exit(f"No book folders found under {P['input']}.\n"
                 f"Move the folder tree in there first, e.g.\n"
                 f"  mv jpg1 {P['input']}/")

    def folder_key(name):
        n = sequence_number(name)
        return ((0, n) if n is not None else (1, 0), name)

    order = sorted(parents, key=folder_key)
    print(f"scanning {len(order)} folder(s), {sum(len(v) for v in parents.values())} image(s) ...")

    frames, books, excluded = [], [], []
    idx = 0
    labelled = inferred = 0

    for folder in order:
        paths = sorted(parents[folder],
                       key=lambda p: ((0, sequence_number(p)) if sequence_number(p) is not None
                                      else (1, 0), os.path.basename(p)))
        book_frames, roles_here = [], {}

        for path in paths:
            name = os.path.basename(path)
            digest = sha256_of(path)
            if digest in ledger["hashes"]:
                excluded.append({"file": f"{folder}/{name}",
                                 "reason": f"already committed to {ledger['hashes'][digest]['book']}"})
                continue
            try:
                with Image.open(path) as raw:
                    timestamp = exif_timestamp(raw, path)
                    upright = ImageOps.exif_transpose(raw.copy())
            except Exception as e:
                excluded.append({"file": f"{folder}/{name}", "reason": f"unreadable ({e})"})
                continue

            idx += 1
            stats = page_statistics(upright)

            # As shot. See the note in cmd_group.
            thumb_name = f"f{idx:04d}__{os.path.splitext(name)[0]}.jpg"
            downscale(upright.convert("RGB"), THUMB_MAX).save(
                os.path.join(P["thumbs"], thumb_name), "JPEG", quality=80)

            frames.append({
                "idx": idx, "file": name, "source": os.path.relpath(path, P["input"]),
                "sha256": digest, "thumb": thumb_name, "taken": timestamp.isoformat(),
                "rotate": 0,
                "gap_before": None, "time_out_of_sequence": False, **stats,
            })
            book_frames.append(idx)

            # a filename of exactly "1", "2" or "3" is a role you already assigned
            stem = os.path.splitext(name)[0].strip()
            if stem in ROLES:
                roles_here[stem] = idx

        if not book_frames:
            continue

        flags = []
        if roles_here:
            labelled += 1
        else:
            # nothing labelled — fall back to shooting order and say so
            roles_here[ROLE_MAIN] = book_frames[0]
            if len(book_frames) > 1:
                roles_here[ROLE_COPYRIGHT] = book_frames[1]
            flags.append("roles inferred from order — no 1/2 filenames in this folder")
            inferred += 1

        if ROLE_MAIN not in roles_here:
            roles_here[ROLE_MAIN] = book_frames[0]
            flags.append("no 1.jpeg — first frame assumed")
        if ROLE_COPYRIGHT not in roles_here and len(book_frames) > 1:
            flags.append("no 2.jpeg — copyright page not identified")
        if len(book_frames) == 1:
            flags.append("single frame")

        books.append({
            "n": len(books) + 1, "title": "", "author": "",
            "frames": book_frames, "roles": roles_here,
            "confidence": "high" if not flags else "low",
            "boundary_reason": f"folder: {folder}",
            "flags": flags, "notes": "",
        })

    if not books:
        sys.exit("Nothing to import — every folder was empty or already committed.")

    grouping = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "start_number": ledger["next"], "source": "import",
        "frames": frames, "books": books, "dropped": [], "excluded": excluded,
    }

    print(f"\n  {len(books)} book(s), {len(frames)} frame(s), numbering from {ledger['next']:04d}")
    print(f"  {labelled} folder(s) had 1/2 filenames; {inferred} fell back to shooting order")
    if excluded:
        print(f"  {len(excluded)} image(s) skipped:")
        for item in excluded[:6]:
            print(f"    ! {item['file']}: {item['reason']}")
        if len(excluded) > 6:
            print(f"    ... and {len(excluded)-6} more")

    if args.dry_run:
        print("\nDry run — nothing written. Drop --dry-run to write the proposal.")
        return

    save_json(P["grouping"], grouping)
    write_contact_sheet(P, grouping)
    print(f"\n  proposal: {P['grouping']}")
    print("\nNext: python3 group_batch.py review")
    print("      (filter by the flagged books — those are the ones needing a role set)")


def cmd_commit(args):
    P = data_paths(args.root)
    ensure_dirs(P)
    grouping = load_json(P["grouping"], None)
    if grouping is None:
        sys.exit("No grouping to commit — run `group` first.")
    ledger = load_json(P["ledger"], {"next": 1, "hashes": {}, "batches": []})
    records = load_json(P["records"], {})
    by_idx = {f["idx"]: f for f in grouping["frames"]}

    errors = validate_grouping(grouping)
    if errors:
        print("VALIDATION FAILED — nothing was moved:")
        for e in errors:
            print("  ", e)
        sys.exit(1)

    number = ledger["next"]
    planned = []
    for book in grouping["books"]:
        folder = f"{number:04d}-{slugify(book['title'])}"
        role_by_idx = {i: r for r, i in book["roles"].items()}
        moves, rotations = [], {}
        for i in book["frames"]:
            fr = by_idx[i]
            role = role_by_idx.get(i)
            angle = int(fr.get("rotate", 0) or 0) % 360
            # Everything lands as JPEG. Uniform format is what lets every later step
            # — the enrichment read, prep_images.py, Preview — just open the file.
            stem = role if role else os.path.splitext(fr["file"])[0]
            newname = f"{stem}.jpg"
            moves.append((fr, newname, angle))
            if angle:
                rotations[newname] = angle
        planned.append({"book": book, "folder": folder, "moves": moves, "rotations": rotations})
        number += 1

    if args.dry_run:
        print(f"DRY RUN — {len(planned)} book(s), numbering from {ledger['next']:04d}\n")
        for p in planned:
            print(f"  {p['folder']}")
            for fr, newname, angle in p["moves"]:
                print(f"      {fr['file']:<28} -> {newname}"
                      + (f"   (rotate {angle}\u00b0)" if angle else ""))
        frames = sum(len(p["moves"]) for p in planned)
        total_rot = sum(len(p["rotations"]) for p in planned)
        print(f"\n  {frames} frame(s) would be converted to JPEG, {total_rot} of them "
              f"rotated")
        if grouping.get("dropped"):
            print(f"\n  {len(grouping['dropped'])} dropped frame(s) -> _work/rejected/")
        print(f"\n  would append {len(planned)} record(s) to {P['records']}")
        return

    converted = baked = failed = 0
    for p in planned:
        dest = os.path.join(P["books"], p["folder"])
        os.makedirs(dest, exist_ok=True)
        for fr, newname, angle in p["moves"]:
            src = os.path.join(P["input"], fr["source"])
            out = os.path.join(dest, newname)
            try:
                with Image.open(src) as raw:
                    im = ImageOps.exif_transpose(raw.copy())
                if angle:
                    im = im.rotate(-angle, expand=True)
                im.convert("RGB").save(out, "JPEG", quality=ARCHIVE_QUALITY,
                                       subsampling=0)
                os.remove(src)
                converted += 1
                if angle:
                    baked += 1
            except Exception as e:
                # Never lose a photograph over a failed encode. Move it in its
                # original format instead and say so, loudly enough to go fix by hand.
                keep = os.path.splitext(newname)[0] + os.path.splitext(fr["file"])[1].lower()
                print(f"  ! {p['folder']}/{fr['file']}: conversion failed ({e}) — "
                      f"kept as {keep}"
                      + (f", still needs {angle}\u00b0" if angle else ""))
                shutil.move(src, os.path.join(dest, keep))
                newname = keep
                failed += 1
            ledger["hashes"][fr["sha256"]] = {"book": p["folder"], "file": newname}

        book = p["book"]
        # Every field the enrichment pass or the app will fill, present and empty.
        # Nothing here is decoration: section, shelf, genres, subjects, places, owner
        # and location all belong to the shelving pass in the app, which can see the
        # whole collection. Guessing them one book at a time from two photographs
        # produced drift and got overwritten by hand afterwards.
        records[p["folder"]] = {
            "itemType": "Book", "title": book["title"], "author": book["author"],
            "publisher": "", "placeOfPublication": "", "year": "",
            "edition": "", "printing": "", "isbn": "", "isbn_status": "unverified",
            "format": "", "signed": False, "inscription": "",
            "section": "", "shelf": "", "genres": [], "subjects": [], "places": [],
            "condition": "", "location": "", "owner": "",
            "notes": book.get("notes", ""),
        }
        print(f"  {p['folder']}  ({len(p['moves'])} image(s))")

    for idx in grouping.get("dropped", []):
        fr = by_idx.get(idx)
        if fr:
            src = os.path.join(P["input"], fr["source"])
            if os.path.exists(src):
                shutil.move(src, os.path.join(P["rejected"], fr["file"]))

    for dirpath, _dirnames, filenames in os.walk(P["input"], topdown=False):
        for name in filenames:
            if not name.startswith("."):
                shutil.move(os.path.join(dirpath, name), os.path.join(P["rejected"], name))
        if dirpath != P["input"] and not os.listdir(dirpath):
            os.rmdir(dirpath)

    ledger["next"] = number
    ledger["batches"].append({
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "books": [p["folder"] for p in planned], "frames": len(grouping["frames"]),
    })
    save_json(P["ledger"], ledger)
    save_json(P["records"], records)

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.move(P["grouping"], os.path.join(P["committed"], f"grouping-{stamp}.json"))
    if os.path.exists(P["grouping"] + ".bak"):
        os.remove(P["grouping"] + ".bak")
    if os.path.exists(P["sheet"]):
        shutil.move(P["sheet"], os.path.join(P["committed"], f"contact-sheet-{stamp}.html"))
    for name in os.listdir(P["thumbs"]):
        os.remove(os.path.join(P["thumbs"], name))

    print(f"\ncommitted {len(planned)} book(s); {P['records']} now holds {len(records)}.")
    print(f"input emptied. {converted} frame(s) written as JPEG, {baked} rotated"
          + (f"; {failed} kept in their original format" if failed else "") + ".")

    # The seed of the re-photography worklist: a book with no role 2 has no copyright
    # page in the batch, and the enrichment pass will come back blank on publisher,
    # year and ISBN for it.
    no_copyright = [p["folder"] for p in planned
                    if ROLE_COPYRIGHT not in p["book"].get("roles", {})]
    if no_copyright:
        print(f"\n  {len(no_copyright)} book(s) with no copyright-page frame: "
              f"{', '.join(no_copyright[:8])}"
              + (" ..." if len(no_copyright) > 8 else ""))

    print(f"\nNext batch: drop photos in {P['input']} and run `group`.")


def cmd_archive(args):
    """
    Move handed-off books out of the staging pile.

    WHY: ingest_batch.py stages EVERY key in records_source.json. Left alone, a
    second hand-off after accumulating more books would re-stage the first set,
    and apply_images.py creates a new record for every non-numeric folder it
    finds — so the earlier books would come through again as duplicates. This is
    the same class of collision as the July id incident.

    The ledger is deliberately NOT touched: numbering keeps climbing, and the
    hash index still recognises photos already processed, so re-dropping an
    archived batch is still caught.
    """
    P = data_paths(args.root)
    ensure_dirs(P)
    records = load_json(P["records"], {})
    folders = sorted(d for d in os.listdir(P["books"])
                     if os.path.isdir(os.path.join(P["books"], d)) and not d.startswith("."))

    matched = [f for f in folders if f in records]

    # Optional number range, for when only part of the pile has actually shipped.
    # Folders are named NNNN-slug, and that number is the catalogue order the ledger
    # assigned, so it is a stable handle even after titles change.
    def folder_number(name):
        m = re.match(r"(\d+)", name)
        return int(m.group(1)) if m else None

    if args.from_n is not None or args.upto is not None:
        lo = args.from_n if args.from_n is not None else 0
        hi = args.upto if args.upto is not None else 10 ** 9
        before = len(matched)
        matched = [f for f in matched
                   if folder_number(f) is not None and lo <= folder_number(f) <= hi]
        print(f"  range {lo:04d}-{hi if hi < 10**9 else 9999:04d}: "
              f"{len(matched)} of {before} book(s) selected")

    orphan_folders = [f for f in folders if f not in records]
    orphan_records = [k for k in records if k not in folders]

    if not matched:
        print("Nothing to archive — no book folder has a matching record.")
        if orphan_folders:
            print(f"  {len(orphan_folders)} folder(s) with no record: {orphan_folders[:5]}")
        return

    for f in orphan_folders:
        print(f"  ! folder with no record, left in place: {f}")
    for k in orphan_records:
        print(f"  ! record with no folder, left in place: {k}")

    if os.path.exists(P["grouping"]):
        print("  ! an uncommitted grouping exists — commit or discard it first "
              "if it belongs with these books")

    stamp = args.name or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(P["archive"], stamp)

    if args.dry_run:
        print(f"\nDRY RUN — would archive {len(matched)} book(s) to {dest}")
        for f in matched[:10]:
            print(f"      {f}")
        if len(matched) > 10:
            print(f"      ... and {len(matched) - 10} more")
        print(f"  records_source.json would keep {len(records) - len(matched)} entry(ies)")
        return

    if os.path.exists(dest):
        sys.exit(f"ERROR: {dest} already exists — pass a different --name.")
    os.makedirs(os.path.join(dest, "books"))

    for f in matched:
        shutil.move(os.path.join(P["books"], f), os.path.join(dest, "books", f))
    save_json(os.path.join(dest, "records_source.json"),
              {k: records[k] for k in matched})

    remaining = {k: v for k, v in records.items() if k not in matched}
    save_json(P["records"], remaining)

    ledger = load_json(P["ledger"], {"next": 1, "hashes": {}, "batches": []})
    ledger.setdefault("archives", []).append({
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "name": stamp, "books": matched,
    })
    save_json(P["ledger"], ledger)

    print(f"\narchived {len(matched)} book(s) -> {dest}")
    print(f"  records_source.json now holds {len(remaining)} entry(ies)")
    print(f"  ledger untouched: next number is still {ledger['next']:04d}, "
          f"{len(ledger['hashes'])} hashes retained")


ORIENT_HTML = r"""<!doctype html>
<meta charset="utf-8"><title>Fix orientation</title>
<style>
 * { box-sizing: border-box; }
 body { font: 14px/1.5 -apple-system, system-ui, sans-serif; margin: 0;
        background: #fafafa; color: #1a1a1a; padding-bottom: 5rem; }
 header.top { position: sticky; top: 0; z-index: 20; background: #fff;
        border-bottom: 1px solid #e5e5e5; padding: .7rem 1.2rem;
        display: flex; align-items: center; gap: .8rem; flex-wrap: wrap; }
 h1 { font-size: 15px; font-weight: 600; margin: 0; }
 .count { font-size: 12px; color: #777; }
 button { font: inherit; font-size: 13px; padding: .3rem .8rem; border-radius: 4px;
          border: 1px solid #1a1a1a; background: #fff; cursor: pointer; }
 button.primary { background: #1a1a1a; color: #fff; }
 button.ghost { border-color: #ccc; color: #666; font-size: 12px; padding: .2rem .5rem; }
 #status { margin-left: auto; font-size: 12px; color: #666; }
 main { padding: 1rem 1.2rem; }
 .legend { font-size: 12px; color: #777; margin: 0 0 1rem; }
 .bk { margin-bottom: 1.1rem; }
 .bk h2 { font-size: 12px; font-weight: 600; color: #888; margin: 0 0 .35rem;
          letter-spacing: .03em; }
 .grid { display: flex; gap: .5rem; flex-wrap: wrap; }
 .cell { width: 120px; }
 .box { width: 120px; height: 120px; background: #fff; border: 1px solid #e5e5e5;
        border-radius: 3px; display: flex; align-items: center; justify-content: center;
        overflow: hidden; cursor: pointer; position: relative; }
 .box:hover { border-color: #1a1a1a; }
 .box img { max-width: 100%; max-height: 100%; transition: transform .12s ease; }
 .box.r90 img  { transform: rotate(90deg)  scale(.78); }
 .box.r180 img { transform: rotate(180deg); }
 .box.r270 img { transform: rotate(270deg) scale(.78); }
 .cell.changed .box { border-color: #557; box-shadow: 0 0 0 2px rgba(85,85,119,.18); }
 .cap { font-size: 9px; color: #999; margin-top: .15rem; text-align: center;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
 .ang { position: absolute; top: 3px; right: 3px; font-size: 10px; color: #fff;
        background: #557; border-radius: 2px; padding: 0 4px; }
 .rot { position: absolute; bottom: 3px; right: 3px; font: inherit; font-size: 11px;
        line-height: 16px; padding: 0 5px; border-radius: 3px; border: 1px solid #ccc;
        background: rgba(255,255,255,.95); color: #666; cursor: pointer; }
 .box:hover .rot { border-color: #1a1a1a; color: #1a1a1a; }
</style>

<header class="top">
  <h1>Fix orientation</h1>
  <span class="count" id="count"></span>
  <button class="ghost primary" id="f-all">all</button>
  <button class="ghost" id="f-rot">rotated</button>
  <button class="ghost" id="f-ed">edited</button>
  <span id="status"></span>
  <button class="primary" id="save">Save</button>
</header>

<main>
  <p class="legend">
    Click an image — anywhere on it, or the <b>&#10227;</b> in its corner — to turn it
    90&deg; clockwise. Four clicks return it to where it started. The blue corner number
    is the angle that will be saved, and a blue outline marks what you have changed.
    <b>rotated</b> shows only images carrying an angle and <b>edited</b> only what you
    have touched this session; use them to check your work before saving.
    Nothing is re-encoded here: angles go to rotate.json and are applied once,
    losslessly, during the webp conversion.
  </p>
  <div id="books"></div>
</main>

<script>
let M = null, filter = 'all';
const START = new Map();     // "folder/file" -> angle as loaded, to spot changes

const edited = (bk, im) => START.get(bk.folder + '/' + im.file) !== im.angle;

async function boot() {
  M = await (await fetch('orient.json?t=' + Date.now())).json();
  for (const bk of M.books)
    for (const im of bk.images) START.set(bk.folder + '/' + im.file, im.angle);
  render();
}

function render() {
  const host = document.getElementById('books');
  host.innerHTML = '';
  const frag = document.createDocumentFragment();
  for (const bk of M.books) {
    const shown = bk.images.filter(im =>
      filter === 'rotated' ? !!im.angle
      : filter === 'edited' ? edited(bk, im)
      : true);
    if (!shown.length) continue;
    const sec = document.createElement('section');
    sec.className = 'bk';
    const h = document.createElement('h2');
    h.textContent = bk.folder;
    sec.appendChild(h);
    const grid = document.createElement('div');
    grid.className = 'grid';
    shown.forEach(im => grid.appendChild(cell(bk, im)));
    sec.appendChild(grid);
    frag.appendChild(sec);
  }
  host.appendChild(frag);
  updateCount();
}

function cell(bk, im) {
  const wrap = document.createElement('div');
  wrap.className = 'cell';

  const box = document.createElement('div');
  box.className = 'box';

  const img = document.createElement('img');
  img.src = 'orient/' + bk.folder + '/' + im.thumb;
  img.loading = 'lazy'; img.decoding = 'async';
  box.appendChild(img);

  const ang = document.createElement('span');
  ang.className = 'ang';
  box.appendChild(ang);

  const paint = () => {
    box.className = 'box' + (im.angle ? ' r' + im.angle : '');
    ang.textContent = im.angle ? im.angle + '\u00b0' : '';
    ang.style.display = im.angle ? '' : 'none';
    wrap.classList.toggle('changed', edited(bk, im));
  };
  paint();

  const turn = () => { im.angle = (im.angle + 90) % 360; paint(); updateCount(); };

  // The whole tile is clickable, but an invisible affordance is not an affordance:
  // the button is what tells you the tile does anything at all.
  const rot = document.createElement('button');
  rot.className = 'rot';
  rot.textContent = '\u27f3';
  rot.title = 'rotate 90\u00b0 clockwise';
  rot.onclick = e => { e.stopPropagation(); turn(); };
  box.appendChild(rot);

  box.onclick = turn;

  const cap = document.createElement('div');
  cap.className = 'cap'; cap.textContent = im.file;
  wrap.appendChild(box); wrap.appendChild(cap);
  return wrap;
}

function updateCount() {
  let total = 0, rotated = 0, changed = 0;
  for (const bk of M.books) for (const im of bk.images) {
    total++;
    if (im.angle) rotated++;
    if (edited(bk, im)) changed++;
  }
  document.getElementById('count').textContent =
    M.books.length + ' books \u00b7 ' + total + ' images \u00b7 ' + rotated +
    ' rotated' + (changed ? ' \u00b7 ' + changed + ' edited' : '');
}

for (const [id, mode] of [['f-all', 'all'], ['f-rot', 'rotated'], ['f-ed', 'edited']]) {
  document.getElementById(id).onclick = () => {
    filter = mode;
    for (const other of ['f-all', 'f-rot', 'f-ed'])
      document.getElementById(other).classList.toggle('primary', other === id);
    render();
    window.scrollTo(0, 0);
  };
}

document.getElementById('save').onclick = async () => {
  const s = document.getElementById('status');
  s.textContent = 'saving\u2026';
  const res = await fetch('orient.json', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(M)
  });
  const out = await res.json();
  if (out.ok) {
    for (const bk of M.books)
      for (const im of bk.images) START.set(bk.folder + '/' + im.file, im.angle);
    s.textContent = 'saved ' + out.written + ' sidecar(s) ' + new Date().toLocaleTimeString();
    render();
  } else {
    s.textContent = 'not saved: ' + (out.error || 'unknown error');
  }
};

boot();
</script>
"""


def cmd_orient(args):
    """
    Set rotation by hand for books that are already in folders.

    Builds a thumbnail per frame, preloads any angles already in each folder's
    rotate.json, and opens a grid where a click turns an image 90° clockwise. Save
    writes the sidecars. Nothing is measured and nothing is guessed.

    Thumbnails are written AS SHOT and turned in the browser with CSS, so what you
    see is what gets saved. Pre-rotating them would put the display a step ahead of
    the data and make every correction destructive.

    --path points this at any directory of book folders, not just data/books/. Use it
    to repair a batch that has already been handed off: run it over
    vivarium-content/image-intake/, then re-run prep_images.py.

    Rotation is applied ONCE, losslessly, by prep_images.py during the webp
    conversion. Do NOT rotate originals in Preview or Finder: that re-encodes them
    lossily AND changes their bytes, which invalidates their SHA-256 in the ledger and
    permanently breaks duplicate detection for them.
    """
    P = data_paths(args.root)
    ensure_dirs(P)
    books_root = os.path.abspath(args.path) if args.path else P["books"]
    if not os.path.isdir(books_root):
        sys.exit(f"No such directory: {books_root}")
    orient_dir = os.path.join(P["work"], "orient")
    manifest_path = os.path.join(P["work"], "orient.json")

    def folder_number(name):
        m = re.match(r"(\d+)", name)
        return int(m.group(1)) if m else None

    if not args.review:
        folders = sorted(d for d in os.listdir(books_root)
                         if os.path.isdir(os.path.join(books_root, d)) and not d.startswith("."))
        if args.from_n is not None or args.upto is not None:
            lo = args.from_n if args.from_n is not None else 0
            hi = args.upto if args.upto is not None else 10 ** 9
            folders = [f for f in folders
                       if folder_number(f) is not None and lo <= folder_number(f) <= hi]
        if not folders:
            sys.exit(f"No book folders found in {books_root}.")

        if os.path.isdir(orient_dir):
            shutil.rmtree(orient_dir)
        os.makedirs(orient_dir, exist_ok=True)
        books = []
        total = rotated = 0
        print(f"building thumbnails for {len(folders)} folder(s) in {books_root} ...")

        for n, folder in enumerate(folders, 1):
            src_dir = os.path.join(books_root, folder)
            out_dir = os.path.join(orient_dir, folder)
            os.makedirs(out_dir, exist_ok=True)
            existing = load_json(os.path.join(src_dir, "rotate.json"), {})
            images = []

            for name in sorted(x for x in os.listdir(src_dir)
                               if x.lower().endswith(IMAGE_EXTS) and not x.startswith(".")):
                try:
                    with Image.open(os.path.join(src_dir, name)) as raw:
                        upright = ImageOps.exif_transpose(raw.copy())
                except Exception as e:
                    print(f"  ! {folder}/{name}: unreadable ({e})")
                    continue
                # AS SHOT. The browser turns it with CSS; see the docstring.
                thumb_name = os.path.splitext(name)[0] + ".jpg"
                downscale(upright.convert("RGB"), 320).save(
                    os.path.join(out_dir, thumb_name), "JPEG", quality=78)
                angle = int(existing.get(name, 0) or 0) % 360
                images.append({"file": name, "thumb": thumb_name, "angle": angle})
                total += 1
                if angle:
                    rotated += 1

            if images:
                books.append({"folder": folder, "images": images})
            if n % 25 == 0:
                print(f"  {n}/{len(folders)} folders ...")

        save_json(manifest_path, {"root": books_root, "books": books})
        print(f"\n  {len(books)} book(s), {total} image(s), "
              f"{rotated} already carrying an angle")
        print(f"\n  nothing written yet — review and save:")
        print(f"    python3 group_batch.py orient --review")
        if args.no_review:
            return

    if not os.path.exists(manifest_path):
        sys.exit("No thumbnails yet — run `orient` without --review first.")

    # On --review the destination comes from the manifest, so the UI cannot write
    # somewhere other than where its thumbnails were built from.
    if args.review:
        books_root = load_json(manifest_path, {}).get("root") or books_root

    work_dir = P["work"]
    books_dir = books_root

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=work_dir, **kw)

        def log_message(self, *a):
            pass

        def _json(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/index.html"):
                body = ORIENT_HTML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def do_POST(self):
            if urllib.parse.urlparse(self.path).path != "/orient.json":
                self._json(404, {"ok": False, "error": "unknown endpoint"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                incoming = json.loads(self.rfile.read(length))
            except Exception as e:
                self._json(400, {"ok": False, "error": f"bad payload: {e}"})
                return

            written = 0
            for bk in incoming.get("books", []):
                folder = bk.get("folder", "")
                dest = os.path.abspath(os.path.join(books_dir, folder))
                # only ever write inside data/books/, and only into a folder that exists
                if os.path.relpath(dest, os.path.abspath(books_dir)).startswith(".."):
                    continue
                if not os.path.isdir(dest):
                    continue
                angles = {im["file"]: int(im["angle"]) % 360
                          for im in bk.get("images", []) if int(im.get("angle", 0)) % 360}
                sidecar = os.path.join(dest, "rotate.json")
                if angles:
                    save_json(sidecar, angles)
                    written += 1
                elif os.path.exists(sidecar):
                    os.remove(sidecar)      # all angles cleared: no sidecar needed
            save_json(manifest_path, incoming)
            print(f"  saved — {written} rotate.json sidecar(s)")
            self._json(200, {"ok": True, "written": written})

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", args.port), Handler) as httpd:
        url = f"http://127.0.0.1:{args.port}/"
        print(f"orientation UI at {url}   (ctrl-C when done)")
        if not args.no_open:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped.")


def cmd_status(args):
    P = data_paths(args.root)
    ledger = load_json(P["ledger"], {"next": 1, "hashes": {}, "batches": []})
    records = load_json(P["records"], {})
    pending = len(scan_input(P["input"])) if os.path.isdir(P["input"]) else 0
    books = sorted(os.listdir(P["books"])) if os.path.isdir(P["books"]) else []
    print(f"  input       {pending} file(s) waiting")
    print(f"  books       {len(books)} folder(s); next number {ledger['next']:04d}")
    print(f"  records     {len(records)} entry(ies)")
    print(f"  batches     {len(ledger['batches'])} committed")
    archives = ledger.get("archives", [])
    if archives:
        total = sum(len(a["books"]) for a in archives)
        print(f"  archived    {total} book(s) in {len(archives)} archive(s)")
    if os.path.exists(P["grouping"]):
        g = load_json(P["grouping"], {})
        print(f"  UNCOMMITTED grouping: {len(g.get('books', []))} book(s) awaiting commit")
    if books:
        print(f"\nHand off with:\n  python3 scripts/ingest_batch.py \\\n"
              f"      --source \"{P['books']}\" \\\n"
              f"      --records \"{P['records']}\"")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.environ.get(
        "VBP_ROOT", os.path.dirname(os.path.abspath(__file__))))
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the data/ tree").set_defaults(func=cmd_init)

    g = sub.add_parser("group", help="propose a grouping (reads only)")
    g.add_argument("--force", action="store_true", help="discard an existing proposal")
    g.set_defaults(func=cmd_group)

    i = sub.add_parser("import", help="build a grouping from folders already one-per-book")
    i.add_argument("--force", action="store_true", help="discard an existing proposal")
    i.add_argument("--dry-run", action="store_true", help="survey only, write nothing")
    i.add_argument("--no-orientation", action="store_true",
                   help=argparse.SUPPRESS)      # no-op: nothing measures orientation
    i.set_defaults(func=cmd_import)

    r = sub.add_parser("review", help="drag-and-drop editor in a browser")
    r.add_argument("--port", type=int, default=8765)
    r.add_argument("--no-open", action="store_true")
    r.set_defaults(func=cmd_review)

    c = sub.add_parser("commit", help="execute the grouping (moves files)")
    c.add_argument("--dry-run", action="store_true")
    c.set_defaults(func=cmd_commit)

    a = sub.add_parser("archive", help="move handed-off books out of the staging pile")
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--name", help="archive folder name (default: timestamp)")
    a.add_argument("--from", dest="from_n", type=int,
                   help="only archive books numbered at or above this")
    a.add_argument("--upto", type=int,
                   help="only archive books numbered at or below this")
    a.set_defaults(func=cmd_archive)

    o = sub.add_parser("orient", help="set rotation by hand for books already in folders")
    o.add_argument("--review", action="store_true", help="skip thumbnailing, open the UI")
    o.add_argument("--no-review", action="store_true", help="build thumbnails only, don't open the UI")
    o.add_argument("--path", help="directory of book folders (default data/books/); "
                                  "point at vivarium-content/image-intake/ to repair a "
                                  "batch that has already been handed off")
    o.add_argument("--from", dest="from_n", type=int)
    o.add_argument("--upto", type=int)
    o.add_argument("--port", type=int, default=8766)
    o.add_argument("--no-open", action="store_true")
    o.set_defaults(func=cmd_orient)

    sub.add_parser("status", help="what is where").set_defaults(func=cmd_status)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
