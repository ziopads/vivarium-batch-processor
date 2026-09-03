// Pull ALL items from Supabase down into the app's data/items.json so local mirrors
// live. READ-ONLY against Supabase (SELECT only). Backs up the existing file first.
// Run this BEFORE apply_images.py so new ids are assigned above Supabase's true max id
// (prevents the id-collision that overwrites live records).
//
//   npm run sync
//
import { createClient } from '@supabase/supabase-js';
import { readFileSync, writeFileSync, copyFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import { APP, ITEMS, requireApp } from './paths.mjs';

requireApp();

const url = process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL;
const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
if (!url || !key) { console.error('Set SUPABASE_URL (or NEXT_PUBLIC_SUPABASE_URL) and SUPABASE_SERVICE_ROLE_KEY.'); process.exit(1); }
const supabase = createClient(url, key, { auth: { persistSession: false } });

// Reverse of the app's row->Item mapping (mirrors lib/data.ts rowToItem) so the local
// file round-trips cleanly back through the seed scripts (attributes preserved).
function rowToItem(row) {
  const attrs = row.attributes || {};
  return {
    publisher: '', placeOfPublication: '', edition: '', printing: '', isbn: '',
    format: '', blurb: '', inscription: '', condition: '', location: '', notes: '',
    ...attrs,
    id: row.id,
    itemType: row.item_type || 'Book',
    title: row.title || '',
    author: row.author || '',
    year: row.year || '',
    section: row.section || '',
    shelf: row.shelf || '',
    genres: row.genres || [],
    subjects: row.subjects || [],
    places: row.places || [],
    visibility: row.visibility || 'public',
    owner: row.owner || '',
    signed: !!row.signed,
    maine: !!row.maine,
    cover: row.cover || undefined,
    copyright: row.copyright || undefined,
    image: row.image ?? null,
    images: row.images || [],
    description: row.description || '',
    discussion: row.discussion || undefined,
  };
}

const PAGE = 1000;
let rows = [];
for (let from = 0; ; from += PAGE) {
  const { data, error } = await supabase.from('items').select('*').order('id').range(from, from + PAGE - 1);
  if (error) { console.error('select failed:', error.message); process.exit(1); }
  rows = rows.concat(data);
  if (data.length < PAGE) break;
}
if (!rows.length) { console.error('Supabase returned 0 items — refusing to overwrite local. Check credentials.'); process.exit(1); }

const items = rows.map(rowToItem);
const maxId = items.reduce((m, i) => Math.max(m, i.id), 0);
// Honour LOCAL_DATA_FILE, mirroring lib/data.ts. Without this the script always wrote
// items.json, so syncing a second instance would pull its records straight over the
// first one's local file while the second's data file sat untouched. Resolved against
// the APP, since that is where both files live.
const dest = process.env.LOCAL_DATA_FILE
  ? path.resolve(APP, process.env.LOCAL_DATA_FILE)
  : ITEMS;
// A downward sync overwrites the local file wholesale. Any local id ABOVE Supabase's
// max is a record that exists here and not there — work created locally and not yet
// seeded. Discarding it silently is how 577 enriched books came within one command of
// being lost on 2 September 2026: something ran this mid-hand-off, between
// apply_images.py and the seed, and the seed then found nothing to insert and exited
// cleanly. Nothing in the run reported a problem.
//
// Refuse rather than clobber. --force overrides.
const force = process.argv.includes('--force');
if (existsSync(dest)) {
  let local = [];
  try { local = JSON.parse(readFileSync(dest, 'utf8')); } catch { local = []; }
  const ahead = (Array.isArray(local) ? local : [])
    .map((i) => Number(i?.id))
    .filter((id) => Number.isFinite(id) && id > maxId)
    .sort((a, b) => a - b);
  if (ahead.length && !force) {
    console.error(`ABORT: ${dest}`);
    console.error(`  holds ${ahead.length} record(s) with ids above Supabase's max (${maxId}):`);
    console.error(`  ${ahead[0]}..${ahead[ahead.length - 1]}`);
    console.error('');
    console.error('These exist locally and NOT in Supabase — unshipped work. Pulling now would');
    console.error('discard them. Seed them first:');
    console.error(`  node --env-file=<env> scripts/seed-new-items.mjs --min ${maxId + 1}`);
    console.error('');
    console.error('Then re-run this. Pass --force only if you mean to throw them away.');
    process.exit(1);
  }
  copyFileSync(dest, dest + '.syncdownbak');
}
writeFileSync(dest, JSON.stringify(items, null, 1), 'utf8');
console.log(`Pulled ${items.length} items from Supabase -> ${dest}`);
console.log(`True max id = ${maxId}. New records should start at ${maxId + 1}. (local backup: items.json.syncdownbak)`);
