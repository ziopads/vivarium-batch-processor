# Enrichment brief — Vivarium book batch

Point Cowork at this repo and say: *follow ENRICHMENT-BRIEF.md*.

This is a self-contained brief. Cowork sessions do not inherit context from web
chat, so everything needed is here. It is also the only instruction document —
do not write a separate rules file for subagents and do not summarise this one
for them. Hand them this file. A summary written from a sampled record is what
once overwrote four records' `owner` values.

---

## What exists

- `data/_work/enrich-read/NNNN-slug/` — **read these.** Downscaled copies, about
  1600px on the long edge, one folder per book:
  - `1.jpg` — the cover or title page
  - `2.jpg` — the copyright page
  - `3.jpg` — present only on some books, usually the rear cover with the barcode

  These are the reading copies and they are the only images you open. They are
  generated from `data/books/` after rotation has been baked, so they are upright
  and current.

- `data/books/NNNN-slug/` — the full-size originals, 4–8 MB a frame, plus any
  `IMG_####.jpg` gallery images. **Do not read anything in here.** The gallery
  images exist to be displayed on the item page. The originals are twenty times
  the bytes of the reading copies for the same text.

- `data/records_source.json` — one entry per book, keyed by folder name. Some
  entries already carry `title`, `author` and `isbn` from the barcode and lookup
  steps described below. The rest are blank and are your scope.

## Before you start

Two scripts have already run against `data/records_source.json`:

- `decode_barcodes.py` read rear-cover barcodes and filled `isbn` where it could
- `resolve_isbns.py` looked those ISBNs up and filled `title` and `author`

So a large share of records arrive already identified. **Skip any record that
already has a non-empty `title`.** Do not re-read it, do not verify it, do not
improve it. Those titles came from a checksum-validated barcode and a
bibliographic database, and a cover photograph is not a better source.

Your scope is the records still holding an empty `title`. Count them first and
say how many there are.

## The task

For each record with an empty `title`, identify the book: **title, author, ISBN.**
Everything else is secondary.

Work in this order and stop as soon as you have what you need:

1. **`3.jpg`**, if present. The barcode decoder already failed here, so read the
   printed ISBN digits if they are legible.
2. **`2.jpg`**, the copyright page. ISBN if there is one, and the publication
   details while you are on the page.
3. **`1.jpg`**, the cover or title page. Title and author, plus anything else
   legible.

Most of your records will need `1.jpg`, because a record reaching you at all
usually means no ISBN was recoverable. Open the earlier frames anyway — an ISBN
the decoder missed is still worth having.

**Do not change the keys.** They are folder names and the whole pipeline joins on
them. If a book has no folder, skip it and say so at the end.

### Fields you owe

`title`, `author`, `isbn`, `isbn_status`.

Blank only when the frames genuinely do not show it.

**Transcribe ISBNs, never look one up.** Not from memory, not from a reference,
not from recognising the book. A wrong-but-plausible ISBN resolves to a wrong
book later and looks correct while doing it. Candidate numbers from anywhere
other than the page go in `notes` and never in `isbn`.

The same holds for `title` and `author`. Recognising a book is not reading it.
Both come off the photographed page or they stay blank.

**`isbn_status`** is a controlled field:
  - `present` — printed on the book and you transcribed it
  - `none` — confirmed no ISBN exists, typically pre-1970 books
  - `unverified` — probably has one, but not on the photographed frames
  - `illegible` — visible but unreadable

Every record arrives holding `unverified`. **That is a default, not a finding.**
If you leave it there, leave it there deliberately.

**SBNs are not ISBNs.** A number labelled `SBN` or `Standard Book Number`, or a
bare nine-digit number with no group prefix, goes in `notes` with `isbn` left
empty and `isbn_status` set to `unverified`. Do not reconstruct the missing
digit.

When a copyright page prints two or three ISBNs for different bindings or
co-publishers and the frames do not show which one this copy is, put all of them
in `notes`, leave `isbn` empty, and set `unverified`.

**Ephemera** — gallery guides, museum brochures, pamphlets with no title page —
get a best-effort `title` and the issuing institution in `author`.

### Fields you record if the page shows them

`publisher`, `placeOfPublication`, `year`, `edition`, `printing`, `format`,
`condition`, `notes`, `signed`, `inscription`.

Take these from a frame you already have open. Do not open another frame to go
looking for them, and do not spend time reasoning about them. A blank here costs
nothing; the shelving pass and the write-up task both come later.

Keep `notes` short. One line per real anomaly — a misprinted ISBN, a number line
contradicting its own edition statement, an ex-library stamp. Not a description
of the book.

`condition` only when a photograph shows actual damage. Assume good otherwise.
Flattering condition notes are worse than none.

### `itemType` and `format`

`itemType` stays `"Book"` on every record without exception, including magazines,
pamphlets, exhibition brochures and the occasional phonograph record. What the
object actually is goes in `format` — Magazine, Pamphlet, Phonograph record,
Board book, and so on — and is named in `notes`.

### Fields you leave alone

**`section`, `shelf`, `genres`, `subjects`, `places`** — leave exactly as found,
empty. Do not read `vocab.json`, do not propose vocabulary, do not mention that
these are blank. Shelving is done by hand in the app afterwards with the whole
collection in view.

**`owner` and `location`** are carried through untouched. Do not read them, do
not write them, do not report on them, and do not treat whatever value they hold
as evidence about any other record. Their values vary by book and the pipeline
does not own them.

**`description` and `discussion`** — leave out entirely. They belong to a later
scheduled write-up task.

### What you do not do

- **No pruning.** Every photographed book is an intended keeper. Duplicate titles
  are real distinct physical copies, each its own record.
- **No merging.** If two adjacent entries look like the same book split in half
  during review, flag it and move on. The image folders would need merging too.
- **No changes to `data/books/` or `data/_work/enrich-read/`.** Read images;
  move, rename, rotate and convert nothing.
- **No reporting on image formats, orientation or file types.** If a frame is
  upside down, read it rotated, note that one book, and move on.

## Running it in parallel

Concurrent writes to `records_source.json` corrupt it. So:

1. Split the scope into groups of about ten books per subagent.
2. **Each subagent writes only to its own `out/batch-NN.json`** containing just
   its own records, and returns a short prose summary. No subagent opens
   `records_source.json` for writing, ever.
3. The parent session merges the batch files and makes **one** write.
4. Work in rounds of roughly a hundred books — merge, write, re-read the file
   from disk, then start the next round. Do not hold the whole batch in flight.

### Before each write, run the merge script

Do not write `records_source.json` yourself and do not write your own assertion
code. The repo has it:

```
python3 scripts/merge_enriched.py --batch-dir out --dry-run
python3 scripts/merge_enriched.py --batch-dir out
```

It reads every `*.json` in the batch directory, checks them against the file on
disk, and makes one atomic write. It refuses to write if any of the following is
false, and repairs nothing:

- every record's field list identical to the baseline's and in the same order
- every field's JSON type unchanged
- key order unchanged, and every key present in the baseline
- no key in two batch files
- all five taxonomy fields still empty
- `isbn_status` within the controlled set
- `isbn` and `isbn_status` mutually consistent both ways
- no empty titles
- no `description` or `discussion` field
- the baseline round-trips byte-for-byte through the file's serialization
- every out-of-scope record unchanged, checked again after the write

`itemType`, `owner` and `location` are restored from the baseline before any of
those checks run, so whatever your batch file holds for them is discarded.

If it fails, regenerate the batch file it names. Do not hand-edit the JSON to get
past the check — the check is the reason the data has survived this long.

Clear the batch directory between rounds.

## When you're done

Report:
- how many entries you filled
- which books have `isbn_status: unverified` — that is the re-photography worklist
- any suspected split-book pairs
- anything left blank because the page did not show it
- any book in `records_source.json` with no folder on disk

Do not report on file formats, image conversion, orientation, taxonomy or empty
vocabulary fields — all handled elsewhere by design.

Then stop. The ingest into Vivarium is a separate, gated step.
