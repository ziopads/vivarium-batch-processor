// Upload the app's public/items/**.webp to Cloudflare R2 under matching keys.
//
//   npm run upload                 # skips files already in R2
//   npm run upload -- --force      # re-upload everything
//
// Keys mirror the paths under public/: public/items/000042/01-cover.webp ->
// items/000042/01-cover.webp, so the image `src` stored on each item stays the same
// and only the render host changes.
//
// Reads from the APP, not from this repo: apply_images.py is what copies the webp
// out of data/ready/ into the app, and the app is what serves them locally.

import { S3Client, PutObjectCommand, HeadObjectCommand } from '@aws-sdk/client-s3';
import { readdirSync, statSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { PUBLIC, PUBLIC_ITEMS, requireApp } from './paths.mjs';

requireApp();

const { R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET } = process.env;
if (!R2_ACCOUNT_ID || !R2_ACCESS_KEY_ID || !R2_SECRET_ACCESS_KEY || !R2_BUCKET) {
  console.error('Set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET.');
  process.exit(1);
}

const s3 = new S3Client({
  region: 'auto',
  endpoint: `https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com`,
  credentials: { accessKeyId: R2_ACCESS_KEY_ID, secretAccessKey: R2_SECRET_ACCESS_KEY },
});

const ROOT = PUBLIC_ITEMS;

function* walk(dir) {
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return;
  }
  for (const name of entries) {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) yield* walk(full);
    else yield full;
  }
}

const files = [...walk(ROOT)].filter((f) => f.endsWith('.webp'));
const force = process.argv.includes('--force');
console.log(`Found ${files.length} webp files. Uploading to R2 bucket "${R2_BUCKET}"${force ? ' (force)' : ''}…`);

let uploaded = 0;
let skipped = 0;
for (const full of files) {
  const key = path.relative(PUBLIC, full).split(path.sep).join('/'); // items/000042/01-cover.webp
  if (!force) {
    try {
      await s3.send(new HeadObjectCommand({ Bucket: R2_BUCKET, Key: key }));
      skipped++;
      continue;
    } catch {
      /* not present — upload below */
    }
  }
  await s3.send(
    new PutObjectCommand({
      Bucket: R2_BUCKET,
      Key: key,
      Body: readFileSync(full),
      ContentType: 'image/webp',
    }),
  );
  uploaded++;
  if (uploaded % 100 === 0) console.log(`  ${uploaded} uploaded…`);
}

console.log(`Done — uploaded ${uploaded}, skipped ${skipped} (already present).`);
