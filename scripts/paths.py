"""
paths.py — every filesystem location the pipeline uses, resolved once.

WHY THIS EXISTS
    Each script used to carry its own copy of
        ROOT = os.environ.get("VIV_ROOT", "/Users/bjameshaskins/Desktop/_PROJECTS")
    and build paths from it. Six copies of one fact, a personal home directory baked
    into a shared repo, and a pipeline that only worked if you ran it from exactly the
    right directory. Locations are derived from this file's own position instead, so
    the working directory stops mattering.

LAYOUT
    This repo owns everything from photographs to seeded records:

        <repo>/scripts/           this file, and the pipeline
        <repo>/data/input/        drop zone
        <repo>/data/books/        grouped book folders (the queue)
        <repo>/data/archive/      shipped batches
        <repo>/data/intake/       staging copy, scratch
        <repo>/data/ready/        webp derivatives, scratch
        <repo>/data/_work/        ledger, grouping proposal, thumbnails

    The app owns the catalogue itself, and this pipeline writes two things into it:

        <app>/data/items.json     the records
        <app>/public/items/       the webp that get uploaded to R2

    Taxonomy, owner and location are the app's alone — nothing here reads or writes
    them, which is why the vocabulary file does not appear above.

    The app is assumed to be a sibling directory named `vivarium`. Override with
    VIVARIUM_APP if it lives elsewhere.
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

DATA = os.path.join(REPO, "data")
INPUT = os.path.join(DATA, "input")
BOOKS = os.path.join(DATA, "books")
ARCHIVE = os.path.join(DATA, "archive")
INTAKE = os.path.join(DATA, "intake")
READY = os.path.join(DATA, "ready")
WORK = os.path.join(DATA, "_work")

RECORDS_SOURCE = os.path.join(DATA, "records_source.json")

APP = os.environ.get("VIVARIUM_APP") or os.path.join(os.path.dirname(REPO), "vivarium")
ITEMS = os.path.join(APP, "data", "items.json")
PUBLIC_ITEMS = os.path.join(APP, "public", "items")

# The service-role key and R2 credentials stay in the app, in one copy. The node
# steps are invoked with --env-file pointing here rather than duplicating secrets.
ENV_FILE = os.path.join(APP, ".env.vivarium")


def require_app():
    """
    Fail early and legibly when the catalogue isn't where we think it is. Every write
    step depends on it, so finding out at step five is worse than finding out at step
    zero.
    """
    if not os.path.isfile(ITEMS):
        raise SystemExit(
            f"Cannot find the Vivarium app.\n"
            f"  expected: {ITEMS}\n"
            f"  set VIVARIUM_APP to the app directory if it lives elsewhere."
        )
