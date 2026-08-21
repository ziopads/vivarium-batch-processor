# Vivarium batch processor

Turns a pile of phone photographs of books into catalogue records: grouped, named,
oriented, read, converted, uploaded, seeded.

The [Vivarium](https://github.com/ziopads/vivarium) app handles one book at a time
perfectly well. This is the bulk path — the back door for the afternoon when four
hundred books come in at once.

```
photographs ─ group ─ review ─ commit ─ enrich ─ handoff ─→ catalogue
```

Six commands. Everything between the first and last is either mechanical or a browser
window where you correct the machine's guesses.

## Install

```bash
pip3 install -r requirements.txt
npm install
python3 group_batch.py init
```

The catalogue app is assumed to be a sibling directory named `vivarium`. If it lives
elsewhere, set `VIVARIUM_APP`. Credentials are read from the app's env file rather than
copied here, so there is one copy of the Supabase service-role key on disk.

## The loop

```bash
# 1. copy photos into data/input/ — HEIC or JPEG, any mix, no sorting

python3 group_batch.py group        # propose a grouping. reads only.
python3 group_batch.py review       # correct it in a browser
python3 group_batch.py commit       # execute. moves files.

# 2. read the copyright pages — see ENRICHMENT-BRIEF.md

python3 scripts/handoff.py          # into the catalogue, in one gated run
```

Then shelve the new books in the app. See PROCESS.md for what each step does and where
the sharp edges are.

## What lives where

```
group_batch.py        photographs → grouped book folders + records
scripts/              book folders → webp → catalogue records → R2 → Supabase
  paths.py            every filesystem location, resolved once
  paths.mjs           the same, for the node steps
data/                 all operator material. gitignored wholesale.
  input/              drop zone; emptied by commit
  books/              the queue: what has been committed but not shipped
  archive/            shipped batches
  intake/  ready/     scratch, cleared after each hand-off
  _work/              ledger, grouping proposal, thumbnails
  records_source.json accumulates alongside books/
```

`init` recreates the whole `data/` tree, so a fresh clone has working code and none of
anyone else's books.

## Design rules

**Batches accumulate.** Drop a pile in, commit it, drop another; the second numbers on
from where the first stopped. Re-dropping photos already committed is caught by SHA-256
and reported rather than duplicated. That check is what makes accumulation safe.

**One representation per fact.** Orientation is set once, in the review UI, and baked
into the pixels at commit — no sidecar, no metadata, nothing downstream to remember it.
Folder names are assigned once and never rewritten. Both of these were learned the
expensive way; the comments in the code say how.

**Blank beats a guess.** An empty field is a known gap. A plausible wrong ISBN is
invisible damage.

**Duplicates are real.** Two copies of a title are two records. Nothing deduplicates.

**The queue is not an archive.** `data/books/` holds what still needs shipping.
`handoff.py` archives on success, because relying on the operator to remember is how a
batch gets ingested twice.

## Requirements

Python 3.9+ with Pillow and pillow-heif. Node 20+ for the Supabase and R2 steps. No
Tesseract — orientation detection was removed after it did more harm than good.
