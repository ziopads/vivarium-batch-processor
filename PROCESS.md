# Process

Six commands from photographs to a seeded catalogue. This is what each does, in order,
and where the sharp edges are. Current batch state lives in `STATE.md`.

---

## 1. Photograph

Per book, in order: cover, copyright page, then anything else worth keeping. Shoot one
book's frames close together and pause between books — the grouper uses that pause.

Filenames must stay in `IMG_####` sequence. That ordering is authoritative, **not**
EXIF: a re-exported or edited file carries a shifted timestamp and would be yanked out
of sequence, inventing spurious single-frame books. Timestamps are still read for gap
detection, but a frame whose timestamp disagrees with filename order is flagged rather
than reordered.

## 2. Drop in

Copy the photos into `data/input/`. HEIC or JPEG, any mix, no sorting, no renaming.

## 3. Group — reads only, moves nothing

```bash
python3 group_batch.py group
```

Hashes every file, decodes each frame once, computes page-vs-cover statistics, and
writes a proposal to `data/_work/grouping.json`. Photos already committed in a previous
batch are caught by hash and skipped.

Boundaries come from two signals: **timestamp gaps**, with the threshold derived per
batch from the median inter-frame gap, and **page-vs-cover statistics** — brightness,
saturation, ink coverage — where a boundary is a cover-like frame following a page-like
one. Agreement is marked `high`, one signal alone `low`.

The two distributions genuinely overlap at a fast shooting rhythm, so `low` can
dominate and the label is advisory. The review screen is the real check. The statistics
are also the part most likely to need tuning on your material: a plain letterpress
wrapper with no jacket art reads statistically as a page. Thresholds are named
constants at the top of `group_batch.py`.

Every frame is recorded at 0° and every thumbnail is written **as shot**. Nothing
measures orientation.

## 4. Review — correct the machine

```bash
python3 group_batch.py review        # http://localhost:8765
```

- **Drag a frame** onto another book to move it, or onto a card to insert before it
- **`1` / `2` / `3`** on each frame: title page, copyright page, rear. Click again to
  clear. Setting a role takes it from whichever frame held it
- **`⟳`** rotates a frame 90° clockwise; four clicks return it. The badge is the angle
  that will be baked. **This is the only place orientation is set in the pipeline**
- **`❯`** splits the book at that frame; **`↑ merge up`** folds it into the one above
- **`×`** drops a frame from the batch entirely
- **Type titles and authors** — the title becomes the folder slug

Save validates before writing and refuses on any error, so the file on disk is always
committable. The previous version is kept as `grouping.json.bak`. Ctrl-C stops the
server.

Nothing else is set here. Section, shelf, genres, subjects, places, owner and location
are all the app's — see *Shelving* below.

## 5. Commit — moves files

```bash
python3 group_batch.py commit --dry-run
python3 group_batch.py commit
```

Renames frames to `1`/`2`/`3`, moves each book into `data/books/NNNN-slug/`, appends to
`records_source.json`, empties `data/input/`. Numbering continues across batches.

**Every frame is written as upright JPEG.** Each is decoded once, turned by the angle
you set, and saved at quality 95. `data/books/` is therefore uniform and readable by
anything — no HEIC, no sidecar, no second representation of which way is up. A failed
conversion moves the file in its original format and prints the failure; nothing is
lost to a bad encode.

Re-encoding is safe for duplicate detection: the ledger hash is taken in `data/input/`
before anything moves, and no hash is ever recomputed from `data/books/`.

It also prints the books with no copyright-page frame. Those come back blank on
publisher, year and ISBN — that list is the re-photography worklist.

## 6. Enrich — in Cowork

Cowork reads the images off disk in `data/books/`. Nothing needs staging or converting
first.

> Follow ENRICHMENT-BRIEF.md in this repo

Fills publisher, place, year, edition, printing, ISBN, `isbn_status`, format, condition
and notes. Nothing else — no taxonomy, no owner, no vocabulary. Do five books first and
check them before releasing the rest.

## 7. Hand off

```bash
python3 scripts/handoff.py
```

One gated run. It pulls current items from Supabase, validates, stages, converts,
**then stops and shows you a plan** — which instance, how many books, what id range —
and waits for `yes`. Everything before that point is reversible. After you confirm it
creates the records, merges in the book data, bakes the gallery order, uploads to R2,
inserts the rows, archives the shipped books, and clears the scratch directories.

It stops at the first failure and tells you which step broke.

```
--stop-before-seed   everything except the Supabase insert
--no-archive         leave the shipped books in the queue
--keep-intake        leave data/intake/ and data/ready/ in place
--env-file X         default is ../vivarium/.env.vivarium
--yes                skip the confirmation
```

### What it runs, and why the order matters

The underlying scripts are unchanged and can each be run alone when something goes
wrong.

`sync_from_supabase.mjs` pulls live items into the app's `items.json` and reports the
true max id. **This has to run first**, because `apply_images.py` assigns ids from that
local file — if it is behind Supabase, new records get ids belonging to live rows.

`ingest_batch.py` copies each book folder into `data/intake/` with a `title.txt`, then
runs `prep_images.py` to write 1400px webp plus 420px thumbnails into `data/ready/`.
Folder names are preserved end to end: `0038-some-title` stays that through intake,
ready, and the apply log.

`apply_images.py` reads every folder in `data/ready/`. A **non-numeric** name becomes a
NEW item: next id after the local max, images copied into the app's
`public/items/<id6>/`, skeleton record appended. A name that is **all digits** instead
appends its images to that existing item id. It prints which folder became which id.

`merge_records.py` matches those folders back to `records_source.json` and pushes in
the bibliographic data — publisher, year, edition, ISBN, format, condition, notes. It
pushes **nothing** the app owns: section, shelf, genres, subjects, places, owner and
location are absent from its field list, so re-running it never writes blanks over
shelving already done. It also points each item's `copyright` field at image 02.

`sync_images.py` exists because local and production build galleries differently:
locally the app scans the image folder on every read, but on Supabase there is no scan
— the gallery is exactly the `images[]` array on the row. This bakes the scan into
`items.json`. It leaves alone any item whose row references files not on local disk,
which covers photographs added through the app.

`images-to-r2.mjs` uploads the new webp. Production serves images from R2, not from
`public/items/`. `--force` re-uploads files already there, which is what you need after
correcting an image in place.

`seed-new-items.mjs` inserts the new rows. It requires `--min` or `--ids` and refuses
to guess a range. The driver passes the exact ids `apply_images.py` created, so a stray
record can't be swept in.

> **Why `seed-new-items.mjs` and not `migrate-to-supabase.mjs`.** The latter UPSERTS by
> id — it overwrites whatever row is already there. Since ids are assigned from the
> local file, a stale local file means new records take ids belonging to live rows and
> an upsert destroys them. That has happened. `seed-new-items.mjs` pre-checks every
> target id and aborts if any exists.

## 8. Shelve, in the app

Books arrive **unshelved**: `section`, `shelf`, `genres`, `subjects`, `places`, `owner`
and `location` are all empty. Nothing upstream writes them.

That is deliberate. Assigning taxonomy during enrichment meant deciding from two
photographs, one book at a time, with no sight of the shelf a book was joining. It
produced drift, it burned tokens reading a controlled vocabulary in full for every
batch, and the results were revised by hand afterwards anyway.

So the work left after a hand-off is: set section and shelf in `/manage` (filter by
both, bulk-assign), add genres and subjects where they earn their place, set owner and
location, and edit the vocabulary in `/admin/vocab` as the collection suggests new
shelves.

Nothing upstream reads these fields, so there is no deadline and no ordering
constraint. An unshelved book is a complete record with a sorting decision still open.

---

## Repair

### Orientation, after commit

```bash
python3 group_batch.py orient                      # data/books/
python3 group_batch.py orient --path data/intake   # a batch already staged
python3 group_batch.py orient --review             # reopen without rebuilding
```

A grid of every frame with its current angle preloaded. Click a tile — anywhere on it,
or the `⟳` in its corner — to turn it, and Save writes a `rotate.json` sidecar.
**`orient` is the only thing that writes one**, and `prep_images.py` is the only thing
that reads one; the normal path has no sidecars at all because `commit` bakes.

The **rotated** and **edited** filters narrow the grid to frames carrying an angle, or
to what you changed this session, which is how you check a long pass before saving. At
a thousand tiles the page is heavy; `--from` / `--upto` splits it, and the manifest is
rewritten on each rebuild, so save each range before starting the next.

To repair a batch that has already shipped: fix the angles in `data/intake/`, re-run
`prep_images.py`, then `npm run upload -- --force`.

### A hand-off that died partway

Each script runs alone. The failing step's message names it. `data/intake/` and
`data/ready/` are copies — the originals are in `data/books/` and the webp in the app's
`public/items/` — so clearing them costs nothing and `--reset-intake` does it.

---

## Rules that are easy to forget

- **Filename order wins over EXIF.** Re-exported photos carry shifted timestamps.
- **You set rotation at step 4, and `commit` bakes it into the pixels.** What a file
  looks like in Finder is what it is.
- **A thumbnail must never be pre-rotated.** `group` once wrote thumbnails already
  turned by a measured angle, and the review UI turned them again with CSS — so any
  frame carrying an angle displayed at *twice* that angle, and clicking it back to
  upright set the stored angle to zero. The correction looked right and erased the only
  record of what the frame needed. If you ever add a preview that rotates, check
  nothing upstream already did.
- **Duplicate representations of one fact are the recurring failure here.** Orientation
  once lived in four places; every rotation bug was two of them disagreeing.
- **Re-encoding an original does not break duplicate detection.** An older note claimed
  it did, on the grounds that the SHA-256 is the file's identity in the ledger. It
  isn't: hashes are taken in `data/input/` before anything moves, and nothing ever
  recomputes one from `data/books/`. That false constraint shaped the architecture for
  months.
- **`data/_work/`, `data/intake/` and `data/ready/` are scratch.** Nothing in them is
  the only copy of anything.
- **Taxonomy, owner and location are the app's.** This pipeline writes them blank and
  never reads them. `merge_records.py` deliberately omits them from its field list.
- **Blank beats a guess.** Transcribe ISBNs, never look them up.
- **Duplicates are real.** Two copies of a title are two records.
- **Gallery frames are deliberate.** Enrichment reads only `1`/`2`/`3`.
- **No `description` or `discussion`** — the scheduled write-up task owns those.
