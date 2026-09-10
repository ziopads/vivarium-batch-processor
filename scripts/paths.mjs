// paths.mjs — the node half of scripts/paths.py. Same locations, same rules.
//
// Resolved from this file's own position rather than process.cwd(), so it stops
// mattering which directory you run from. That used to matter a great deal: these
// scripts read `public/` and `data/items.json` relative to the working directory and
// only worked from the app root.
//
// The app is assumed to be a sibling directory named `vivarium`. Override with
// VIVARIUM_APP.

import path from 'node:path';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export const HERE = path.dirname(fileURLToPath(import.meta.url));
export const REPO = path.dirname(HERE);

export const DATA = path.join(REPO, 'data');
export const INTAKE = path.join(DATA, 'intake');
export const READY = path.join(DATA, 'ready');
export const RECORDS_SOURCE = path.join(DATA, 'records_source.json');

export const APP =
  process.env.VIVARIUM_APP || path.join(path.dirname(REPO), 'vivarium');

/**
 * Was the app directory named, or guessed?
 *
 * The default is a sibling called `vivarium`, which was correct when one
 * checkout served every instance. It is not correct now: each instance has its
 * own clone (vivarium, vivarium-tamplin, vivarium-sirsinate), and passing
 * --env-file changes which DATABASE a script reads without changing which TREE
 * it writes to. On 10 September 2026 a sync pointed at the Tamplin database
 * aimed 221 paintings at the library's tree; only the id-ahead guard in
 * sync_from_supabase.mjs stopped it.
 *
 * Callers that WRITE into the app should require this to be true. Reading is
 * harmless either way.
 */
export const APP_EXPLICIT = Boolean(process.env.VIVARIUM_APP);
export const APP_DATA = path.join(APP, 'data');
export const ITEMS = path.join(APP_DATA, 'items.json');
export const PUBLIC = path.join(APP, 'public');
export const PUBLIC_ITEMS = path.join(PUBLIC, 'items');

// Every write step depends on the app being where we think it is. Finding out at
// step zero beats finding out after a Supabase pull.
export function requireApp() {
  if (!existsSync(ITEMS)) {
    console.error(
      `Cannot find the Vivarium app.\n` +
        `  expected: ${ITEMS}\n` +
        `  set VIVARIUM_APP to the app directory if it lives elsewhere.`,
    );
    process.exit(1);
  }
}

/**
 * Refuse to write into a guessed tree when the environment names a specific
 * database.
 *
 * `--env-file` selects the database. VIVARIUM_APP selects the tree. Nothing
 * connects them, so the dangerous combination is an env file for one instance
 * and a defaulted app path pointing at another's clone. Naming the tree makes
 * the pairing deliberate.
 *
 * This does not catch every mismatch — a wrong VIVARIUM_APP is still wrong, and
 * no check here can know which database an env file belongs to. It catches the
 * one that actually happened.
 */
export function requireExplicitApp(what = 'write') {
  if (APP_EXPLICIT) return;
  console.error(
    `REFUSING TO ${what.toUpperCase()}: VIVARIUM_APP is not set, so the app directory was\n` +
      `guessed as:\n  ${APP}\n\n` +
      `--env-file chooses the DATABASE; VIVARIUM_APP chooses the TREE. With one\n` +
      `clone per instance they have to be named together, or a pull from one\n` +
      `instance lands in another's working copy.\n\n` +
      `Set it in that clone's .env.local, e.g.\n` +
      `  VIVARIUM_APP=/Users/you/Desktop/_PROJECTS/vivarium-tamplin\n` +
      `or pass it inline:\n` +
      `  VIVARIUM_APP=../vivarium-tamplin node --env-file=... scripts/<script>.mjs`,
  );
  process.exit(1);
}
