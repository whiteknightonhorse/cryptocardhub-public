/**
 * freshness.mjs — AEO-5. One answer to "when did this page last actually change?"
 *
 * THE BUG THIS REPLACES
 * The sitemap stamped every one of 758 URLs with `lastmod = new Date()`, i.e.
 * the moment the build ran. Every deploy therefore announced that the whole
 * site had just changed. A crawler that is told everything changed learns to
 * believe nothing did, and Perplexity names staleness as its single biggest
 * reason not to cite a source — so the signal was not merely absent, it was
 * inverted.
 *
 * THE RULE
 *   pSEO page  -> `generated_at` from its own frontmatter, bumped to the
 *                 availability `verified` date only for pages whose facts come
 *                 from config/availability.yaml (the ether.fi-unavailable geo
 *                 pages and the matrix). A re-check of the facts IS a change;
 *                 a redeploy is not.
 *   video page -> the video's `published_at`.
 *   anything else -> NO lastmod at all.
 *
 * That last line is the important one. There is no honest content date for a
 * hand-written page here, and an invented one is exactly the lie we are
 * removing. Omitting the field is allowed by the sitemap protocol; lying in it
 * is what taught the crawler to ignore us.
 */
import fs from 'node:fs';
import path from 'node:path';
import { AVAILABILITY } from './availability.mjs';

/**
 * Locate src/content/ without trusting the module's own location.
 *
 * Measured, not assumed: inside the Astro SSR bundle `import.meta.dirname` for
 * this file is `seo-site/dist/pages/_bucket_`, not `seo-site/src/lib`. A
 * module-relative path therefore resolved to a directory that does not exist,
 * `existsSync` said no, the map came back empty, and every dateModified
 * silently fell back to datePublished — a wrong date that looked exactly like
 * a right one. Walking up from cwd finds the same directory in the config
 * loader, the SSR bundle, the post-build script and the test runner alike.
 *
 * And it THROWS when it cannot. The whole defect above was one skipped
 * `if (existsSync(...))`: a freshness module that silently has no data is
 * worse than one that stops the build.
 */
function contentRoot() {
  let dir = process.cwd();
  for (let i = 0; i < 6; i += 1) {
    for (const rel of ['src/content', 'seo-site/src/content']) {
      const c = path.join(dir, rel);
      if (fs.existsSync(path.join(c, 'pseo'))) return c;
    }
    const up = path.dirname(dir);
    if (up === dir) break;
    dir = up;
  }
  throw new Error(
    `[freshness] could not locate src/content/pseo from cwd=${process.cwd()}. `
    + 'Refusing to build a sitemap with no content dates rather than stamping '
    + 'every URL with the build time again.');
}

const CONTENT = contentRoot();
const PSEO_DIR = path.join(CONTENT, 'pseo');
const VIDEO_DIR = path.join(CONTENT, 'videos');

/** Pages whose factual claims are sourced from config/availability.yaml. A
 *  verified re-check of that file is a real content change for them. */
const AVAILABILITY_BACKED = new Set([
  ...(AVAILABILITY.unavailable_slugs || []),
]);
const MATRIX_PATH = '/geo/availability-matrix/';

function ymd(value) {
  if (!value) return null;
  const s = String(value);
  const m = /^(\d{4}-\d{2}-\d{2})/.exec(s);
  return m ? m[1] : null;
}

/** Cheap frontmatter scan — we need exactly three scalar fields, not a parser. */
function scanFrontmatter(file) {
  const raw = fs.readFileSync(file, 'utf8');
  const end = raw.indexOf('\n---\n', 4);
  const head = end < 0 ? raw : raw.slice(0, end);
  const pick = (key) => {
    const m = new RegExp(`^${key}:\\s*(.+)$`, 'm').exec(head);
    if (!m) return null;
    try { return JSON.parse(m[1]); } catch { return m[1].trim().replace(/^["']|["']$/g, ''); }
  };
  return { bucket: pick('bucket'), slug: pick('slug'), generated_at: pick('generated_at') };
}

function buildMap() {
  const map = new Map();
  const verified = ymd(AVAILABILITY.cards?.etherfi?.verified)
                || ymd(AVAILABILITY.source?.verified);

  for (const name of fs.readdirSync(PSEO_DIR)) {
    if (!name.endsWith('.md')) continue;
    const fm = scanFrontmatter(path.join(PSEO_DIR, name));
    if (!fm.bucket || !fm.slug) continue;
    let date = ymd(fm.generated_at);
    if (verified && AVAILABILITY_BACKED.has(fm.slug) && verified > (date || '')) {
      date = verified;   // the facts were re-checked; that IS a change
    }
    if (date) map.set(`/${fm.bucket}/${fm.slug}/`, date);
  }

  if (fs.existsSync(VIDEO_DIR)) {
    for (const name of fs.readdirSync(VIDEO_DIR)) {
      if (!name.endsWith('.json')) continue;
      try {
        const v = JSON.parse(fs.readFileSync(path.join(VIDEO_DIR, name), 'utf8'));
        const date = ymd(v.published_at);
        if (v.id && date) map.set(`/videos/${v.id}/`, date);
      } catch { /* a malformed video record just has no lastmod */ }
    }
  }

  if (verified) map.set(MATRIX_PATH, verified);
  return map;
}

const MAP = buildMap();

/** ISO yyyy-mm-dd, or null when we have no honest date for this URL. */
export function lastmodFor(urlOrPath) {
  let p = urlOrPath;
  try { p = new URL(urlOrPath).pathname; } catch { /* already a path */ }
  if (!p.endsWith('/')) p += '/';
  return MAP.get(p) || null;
}

/** Article schema `dateModified`. Same principle: the published date unless the
 *  page's facts were genuinely re-verified later. Never the build time. */
export function dateModifiedFor(bucket, slug, datePublished) {
  return lastmodFor(`/${bucket}/${slug}/`) || ymd(datePublished) || null;
}

export const LASTMOD_URL_COUNT = MAP.size;
