# Enrichment brief — Vivarium book batch

Point Cowork at this repo and say: *follow ENRICHMENT-BRIEF.md*.

This is a self-contained brief. Cowork sessions do not inherit context from web
chat, so everything needed is here.

---

## What exists

- `data/books/NNNN-slug/` — **read these.** One folder per book. Inside:
  - `1.jpg` — the title/author page (cover if legible, else title page)
  - `2.jpg` — the copyright page (publisher, year, edition, printing, ISBN)
  - `3.jpg` — present only when the ISBN/barcode lives elsewhere (usually rear cover)
  - any other `IMG_####.jpg` — **deliberate gallery images. Do not read them.**
    They exist to be displayed on the item page. Reading them wastes tokens.

  Every file is JPEG and upright: `commit` converts and rotates as it writes, so what
  is on disk is what was approved. There is no scratch copy and no sidecar.

- `data/records_source.json` — one entry per book, keyed by folder name. Some
  batches arrive with `title` and `author` typed during the grouping review; others
  arrive with everything blank except `itemType`. Either is normal. Read what's there
  and fill the bibliographic fields; leave the rest.

## The task

For each entry in `records_source.json`, read that book's `1.jpg` and `2.jpg` from
`data/books/` and fill in the blank fields. Edit `records_source.json` in place.
**Do not change the keys** — they are folder names and the whole pipeline joins on
them.

If a book has no folder, skip it and say so at the end.

### Fields you fill from the copyright page

`publisher`, `placeOfPublication`, `year`, `edition`, `printing`, `isbn`,
`isbn_status`, `format`, and `notes`.

- **`isbn_status`** is a controlled field accompanying `isbn`:
  - `present` — an ISBN is printed on the book and you transcribed it
  - `none` — confirmed no ISBN exists (typically pre-~1970 books)
  - `unverified` — probably has one, but it isn't on the photographed pages
  - `illegible` — visible but unreadable
- **Transcribe ISBNs only.** Never look one up externally and never infer one from a
  reference. Edition- and printing-specific ISBNs are easy to get plausibly wrong,
  and a wrong-but-plausible ISBN is worse than a blank one. Candidate ISBNs from
  other sources go in `notes`, never in `isbn`.
- **`format`** — Hardcover, Paperback, and so on.
- **`notes`** — number line, ex-library stamps, provenance marks, dust jacket state,
  anything a cataloguer would want recorded.

**Blank beats a guess** for every one of these. An empty field is a known gap; a
confident wrong value is invisible damage.

### Fields you assign as taxonomy

**None. Leave `section`, `shelf`, `genres`, `subjects` and `places` exactly as you
found them, empty.** Do not read `vocab.json`, do not propose vocabulary, do not
mention that these fields are blank. Shelving is done by hand in the app afterwards,
with the whole collection in view — assigning it book-by-book from two photographs
produces drift and costs tokens for a result that gets overwritten.

The same goes for `owner` and `location`: they are blank and stay blank. The app sets
them.

### Titles and authors

These were typed during the grouping review from ~500px thumbnails, so they are
provisional. When the full-resolution page disagrees, **overwrite them silently** —
the higher-resolution read wins. Do not try to merge the two readings.

If two adjacent entries turn out to be the same book, that means one book was split
in half during review. Flag it; do not merge the records yourself, because the image
folders would also need merging.

### What you do NOT do

- **No `description` and no `discussion`.** Those belong to the standing scheduled
  write-up task, which runs later against the seeded catalogue. Leave them out
  entirely.
- **No pruning.** Every photographed book is an intended keeper. Duplicate titles are
  real distinct physical copies, each its own record — do not deduplicate.
- **No changes to `data/books/`.** Read the images; don't move, rename, rotate or
  convert anything.
- **No reporting on image formats, orientation, or file types.** Everything is
  upright JPEG by the time you see it. If something isn't, note that one book and
  move on.

## Condition

Assume good/fair unless a photograph shows otherwise. If it does — a split hinge,
heavy foxing, a chipped jacket — record it in `condition` honestly. Flattering
condition notes are worse than none.

## When you're done

Report:
- how many entries you filled
- which books have `isbn_status: unverified` (that's the worklist for re-photography)
- any suspected split-book pairs
- anything you left blank because the page didn't show it
- any book in `records_source.json` with no folder on disk

Do not report on file formats, image conversion, orientation, taxonomy or empty
vocabulary fields — those are all handled elsewhere by design.

Then stop. The ingest into Vivarium is a separate, gated step.
