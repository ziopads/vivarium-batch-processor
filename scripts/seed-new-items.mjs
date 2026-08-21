// INSERT-ONLY seed of new item records into Supabase. Never overwrites: if ANY target id
// already exists in Supabase, it ABORTS and lists them (so you re-key, don't clobber).
//
//   npm run seed -- --min 1302
//   npm run seed -- --ids 1302,1303,1304
//
// SAFE-BY-DESIGN. Pair with sync_from_supabase.mjs (run that first so local ids are assigned
// above Supabase's true max). Does NOT touch existing rows or the vocab table.
import { createClient } from '@supabase/supabase-js';
import { readFileSync } from 'node:fs';
import { ITEMS, requireApp } from './paths.mjs';

requireApp();

const url = process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL;
const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
if (!url || !key) { console.error('Set SUPABASE_URL (or NEXT_PUBLIC_SUPABASE_URL) and SUPABASE_SERVICE_ROLE_KEY.'); process.exit(1); }
const supabase = createClient(url, key, { auth: { persistSession: false } });

const argv = process.argv.slice(2);
const getArg = (n) => { const i = argv.indexOf(n); return i >= 0 ? argv[i + 1] : null; };
const idsArg = getArg('--ids');
const minId = getArg('--min') ? Number(getArg('--min')) : null;

const items = JSON.parse(readFileSync(ITEMS, 'utf8'));

const COLUMN_KEYS = new Set([
  'id','itemType','title','author','year','section','shelf','genres','subjects','places',
  'visibility','owner','signed','maine','cover','copyright','image','images','description','discussion',
]);
function toRow(it) {
  const attributes = {};
  for (const [k, v] of Object.entries(it)) if (!COLUMN_KEYS.has(k)) attributes[k] = v;
  return {
    id: it.id, item_type: it.itemType || 'Book', title: it.title || '', author: it.author || '',
    year: it.year || '', section: it.section || null, shelf: it.shelf || null,
    genres: it.genres || [], subjects: it.subjects || [], places: it.places || [],
    visibility: it.visibility === 'restricted' ? 'restricted' : 'public',
    owner: it.owner || null, signed: !!it.signed, maine: !!it.maine,
    cover: it.cover || null, copyright: it.copyright || null, image: it.image ?? null,
    images: it.images || [], description: it.description || '', discussion: it.discussion || null,
    attributes,
  };
}

let picked;
if (idsArg) { const set = new Set(idsArg.split(',').map(Number)); picked = items.filter((i) => set.has(i.id)); }
else if (minId != null) picked = items.filter((i) => i.id >= minId);
else { console.error('Specify --min <id> or --ids a,b,c. (Refusing to guess a range.)'); process.exit(1); }

if (!picked.length) { console.log('No matching records in items.json — nothing to do.'); process.exit(0); }
const ids = picked.map((i) => i.id).sort((a, b) => a - b);

// --- SAFETY CHECK: abort if any target id already exists in Supabase ---
const existing = [];
for (let i = 0; i < ids.length; i += 500) {
  const { data, error } = await supabase.from('items').select('id').in('id', ids.slice(i, i + 500));
  if (error) { console.error('pre-check select failed:', error.message); process.exit(1); }
  existing.push(...data.map((r) => r.id));
}
if (existing.length) {
  console.error(`ABORT: ${existing.length} of the target ids already exist in Supabase:`);
  console.error('  ' + existing.sort((a, b) => a - b).join(', '));
  console.error('These would be OVERWRITTEN. Re-key the new records to ids above the current max');
  console.error('(run sync_from_supabase.mjs to find it), then re-run. Nothing was written.');
  process.exit(1);
}

console.log(`Inserting ${picked.length} NEW record(s): ids ${ids[0]}..${ids[ids.length - 1]} (none pre-exist — safe)`);
const rows = picked.map(toRow);
for (let i = 0; i < rows.length; i += 500) {
  const { error } = await supabase.from('items').insert(rows.slice(i, i + 500));
  if (error) { console.error('insert failed:', error.message); process.exit(1); }
  console.log(`  ${Math.min(i + 500, rows.length)}/${rows.length}`);
}
console.log(`Done — inserted ${rows.length} new item(s). Existing rows and vocab untouched.`);
