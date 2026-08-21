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
