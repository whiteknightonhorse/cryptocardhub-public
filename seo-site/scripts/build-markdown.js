/**
 * build-markdown.mjs -- post-build step that emits a parallel index.md
 * next to every dist/<bucket>/<slug>/index.html.
 *
 * Source: seo-site/src/content/pseo/<slug>.md (already has the prose + body
 * markdown produced by pseo-generate.py). We strip the YAML frontmatter,
 * substitute <<AFFILIATE_URL>> the same way the remark plugin does for HTML,
 * and write a clean Markdown file that nginx serves on Accept: text/markdown.
 *
 * Also emits dist/index.md for the homepage (hand-written summary).
 *
 * Per write-article skill sec 1.5: AFFILIATE_URL stays a single env-var
 * source of truth -- substituted at every render path that consumes a
 * <<AFFILIATE_URL>> placeholder.
 */
import { readFile, writeFile, mkdir, readdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  publishedCards, jurisdictions, cellFor, availabilityNote,
} from '../src/lib/availability.mjs';
import { resolveAffiliate, primaryPartnerFor } from '../src/lib/affiliates.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');
const PSEO_SRC = join(ROOT, 'src', 'content', 'pseo');
const DIST = join(ROOT, 'dist');

// Y56: the sidecar resolves affiliate links through the SAME resolver as HTML
// (src/lib/affiliates.mjs). Before this it substituted one hardcoded ether.fi
// URL for the bare placeholder unconditionally and did not know the tagged
// <<AFFILIATE_URL:partner>> form at all — so 4 geo pages whose card is
// unavailable (india, nevada, arizona, georgia) shipped 10 ether.fi referrals
// into the markdown channel while their HTML correctly routed to crypto.com.
// The deploy guard caught only 2 of them, and only by accident: it detects an
// UNRESOLVED placeholder, while the money error is a WRONGLY RESOLVED one.
//
// The builder is strictly sequential (one article at a time, awaited), so the
// current page's partner is held here rather than threaded through 7 call
// sites. If this ever builds pages concurrently, pass it as a parameter.
let currentPrimary;

// Strip YAML frontmatter; return the body markdown only
function stripFrontmatter(md) {
  if (!md.startsWith('---\n')) return md;
  const end = md.indexOf('\n---\n', 4);
  if (end < 0) return md;
  return md.slice(end + 5);
}

// Parse minimal frontmatter we need: title, description, h1, bucket, slug,
// snapshot_bullets[], faq[], related_clusters[]
function parseFrontmatter(md) {
  if (!md.startsWith('---\n')) return {};
  const end = md.indexOf('\n---\n', 4);
  if (end < 0) return {};
  const body = md.slice(4, end);
  const out = {};
  // Use a tolerant parser since the generator emits JSON-encoded values.
  const re = /^([a-z_]+):\s*(.*)$/gm;
  let m;
  while ((m = re.exec(body)) !== null) {
    const k = m[1];
    let v = m[2];
    try { v = JSON.parse(v); } catch { /* leave as raw */ }
    out[k] = v;
  }
  return out;
}

function substitute(s) {
  if (typeof s !== 'string') return s;
  return resolveAffiliate(s, currentPrimary);
}

function bulletLine(b) {
  return '- ' + substitute(String(b));
}

async function buildArticleMarkdown(srcPath, slug) {
  const raw = await readFile(srcPath, 'utf8');
  const fm = parseFrontmatter(raw);
  // Stated once, before any substitution: explicit primary_partner wins, then
  // ether.fi-unavailable slugs fall back, then the default. Same rule as HTML.
  currentPrimary = primaryPartnerFor({ ...fm, slug: fm.slug || slug });
  const body = stripFrontmatter(raw).trim();

  const lines = [];
  lines.push(`# ${fm.h1 || fm.title || slug}`);
  lines.push('');
  if (fm.description) {
    lines.push(`> ${fm.description}`);
    lines.push('');
  }
  if (fm.intro_paragraph) {
    lines.push(substitute(String(fm.intro_paragraph)));
    lines.push('');
  }
  if (Array.isArray(fm.snapshot_bullets) && fm.snapshot_bullets.length) {
    lines.push('## At a glance');
    lines.push('');
    for (const b of fm.snapshot_bullets) lines.push(bulletLine(b));
    lines.push('');
  }
  // The body markdown already contains proper H2/H3 + inline images +
  // affiliate-button placeholders. Drop the embedded <figure> blocks
  // for cleaner agent-ingestion (alt + url alone is enough), and
  // collapse the affiliate-button HTML into a plain markdown link.
  let stripped = substitute(body);
  // Replace inline <figure> blocks with simple ![alt](src)
  stripped = stripped.replace(
    /<figure class="article-inline-image">[\s\S]*?<img[^>]*src="([^"]+)"[^>]*alt="([^"]*)"[\s\S]*?<\/figure>/g,
    (_, src, alt) => `\n![${alt}](${src})\n`,
  );
  // Replace affiliate-button paragraphs with a markdown link
  stripped = stripped.replace(
    /<p class="article-cta">[\s\S]*?<a[^>]*href="([^"]+)"[^>]*>([^<]+)<\/a>[\s\S]*?<\/p>/g,
    (_, href, text) => `\n**[${text.trim()}](${href})**\n`,
  );
  lines.push(stripped);
  lines.push('');
  if (Array.isArray(fm.what_to_watch) && fm.what_to_watch.length) {
    lines.push('## What to watch');
    lines.push('');
    for (const b of fm.what_to_watch) lines.push(bulletLine(b));
    lines.push('');
  }
  if (Array.isArray(fm.bottom_line) && fm.bottom_line.length) {
    lines.push('## Bottom line');
    lines.push('');
    for (const b of fm.bottom_line) lines.push(bulletLine(b));
    lines.push('');
  }
  if (Array.isArray(fm.faq) && fm.faq.length) {
    lines.push('## FAQ');
    lines.push('');
    for (const item of fm.faq) {
      lines.push(`### ${item.q}`);
      lines.push('');
      lines.push(substitute(String(item.a)));
      lines.push('');
    }
  }
  if (Array.isArray(fm.related_clusters) && fm.related_clusters.length) {
    lines.push('## Related guides');
    lines.push('');
    for (const c of fm.related_clusters) {
      // related_clusters can be slug or bucket/slug
      lines.push(`- https://defycard.com/${c}/`);
    }
    lines.push('');
  }
  return { bucket: fm.bucket || 'hub', slug: fm.slug || slug, body: lines.join('\n') };
}

async function buildHomepageMarkdown() {
  const articles = (await readdir(PSEO_SRC, { withFileTypes: true }))
    .filter((d) => d.isFile() && d.name.endsWith('.md'))
    .map((d) => d.name.replace(/\.md$/, ''));

  const lines = [];
  lines.push('# DefyCard');
  lines.push('');
  lines.push('> Independent comparison hub for crypto debit and credit cards.');
  lines.push('');
  lines.push('See <https://defycard.com/llms.txt> for the full LLM brief.');
  lines.push('');
  lines.push('## Browse by topic');
  lines.push('');
  for (const b of ['hub', 'compare', 'reviews', 'how-to', 'for', 'tools', 'learn', 'help', 'faq']) {
    lines.push(`- /${b}/ -- bucket index`);
  }
  lines.push('');
  lines.push(`## Latest articles (${articles.length})`);
  lines.push('');
  for (const slug of articles) {
    lines.push(`- https://defycard.com/hub/${slug}/`);
  }
  lines.push('');
  lines.push(`Last built: ${new Date().toISOString()}`);
  return lines.join('\n');
}

/**
 * AEO-5: rewrite the <!--INVENTORY--> block of dist/llms.txt with counts that
 * are actually true right now.
 *
 * The old file promised a "30-day data refresh cycle" that nothing in this
 * repo performed, and carried a hand-typed "Last updated: 2026-05-05". Either
 * the cycle exists or the promise does not; the only re-check that genuinely
 * runs is the availability one, so that is the only one still claimed — and it
 * is claimed with the date the check actually produced.
 */
async function buildLlmsInventory() {
  const file = join(DIST, 'llms.txt');
  if (!existsSync(file)) throw new Error('dist/llms.txt missing -- public/ not copied?');
  const raw = await readFile(file, 'utf8');
  const open = '<!--INVENTORY-->';
  const close = '<!--/INVENTORY-->';
  const i = raw.indexOf(open);
  const j = raw.indexOf(close);
  if (i < 0 || j < 0) {
    // Fail loudly: shipping the template text would republish a promise with
    // no cycle behind it, which is the exact defect AEO-5 removes.
    throw new Error('llms.txt has no INVENTORY markers -- refusing to ship a stale claim');
  }

  const names = (await readdir(PSEO_SRC, { withFileTypes: true }))
    .filter((d) => d.isFile() && d.name.endsWith('.md'))
    .map((d) => d.name);
  const byBucket = new Map();
  for (const name of names) {
    const fm = parseFrontmatter(await readFile(join(PSEO_SRC, name), 'utf8'));
    const b = fm.bucket || 'unknown';
    byBucket.set(b, (byBucket.get(b) || 0) + 1);
  }

  const cards = publishedCards();
  const lines = [
    open,
    `Total published articles: ${names.length}`,
    '',
    'By bucket:',
    ...[...byBucket.entries()].sort((a, b) => b[1] - a[1])
      .map(([b, n]) => `- /${b}/ — ${n}`),
    '',
    'Availability lists re-checked against the issuer:',
    ...(cards.length
      ? cards.map((c) => `- ${c.name} — ${availabilityNote(c)} — ${c.source?.url || 'no source recorded'}`)
      : ['- none verified yet; the matrix publishes nothing until one is']),
    '',
    `Built: ${new Date().toISOString().slice(0, 10)}`,
    '(This block is generated; the counts above are read off the content directory at build time.)',
    close,
  ];
  await writeFile(file, raw.slice(0, i) + lines.join('\n') + raw.slice(j + close.length), 'utf8');
  console.log(`[build-markdown] llms.txt inventory: ${names.length} articles, ${byBucket.size} buckets`);
}

/**
 * AEO-1: markdown sidecar for /geo/availability-matrix/.
 *
 * The matrix is an Astro page, not a content-collection entry, so the loop
 * below never sees it — and it is precisely the page an assistant most wants
 * as plain text. Built from the same YAML as the HTML, so the two cannot
 * disagree.
 */
async function buildMatrixMarkdown() {
  const cards = publishedCards();
  const { countries, usStates } = jurisdictions(cards);
  const L = ['# Crypto card availability by country', ''];
  L.push('> Which crypto cards can actually be issued in your country or US state, '
       + 'with the date each list was last checked against the issuer.', '');
  if (!cards.length) {
    L.push('No card list has been verified against its issuer yet, so this table is empty.');
  } else {
    L.push('## Sources and last check', '');
    for (const c of cards) {
      L.push(`- **${c.name}** — ${availabilityNote(c)} — ${c.source?.url || 'no source recorded'}`);
    }
    L.push('', '## By country', '');
    L.push(`| Country | ${cards.map((c) => c.name).join(' | ')} |`);
    L.push(`|---|${cards.map(() => '---').join('|')}|`);
    for (const name of countries) {
      const cells = cards.map((c) => {
        const v = cellFor(c, name, 'country');
        return v === null ? 'Not stated' : (v ? 'Available' : 'Not available');
      });
      L.push(`| ${name} | ${cells.join(' | ')} |`);
    }
    if (usStates.length) {
      L.push('', '## By US state', '');
      L.push('States not listed are supported by every card in this table.', '');
      L.push(`| US state | ${cards.map((c) => c.name).join(' | ')} |`);
      L.push(`|---|${cards.map(() => '---').join('|')}|`);
      for (const name of usStates) {
        const cells = cards.map((c) => (cellFor(c, name, 'state') ? 'Available' : 'Not available'));
        L.push(`| ${name} | ${cells.join(' | ')} |`);
      }
    }
  }
  L.push('', 'Corrections are applied by a human editor, never automatically.', '');
  const outDir = join(DIST, 'geo', 'availability-matrix');
  await mkdir(outDir, { recursive: true });
  await writeFile(join(outDir, 'index.md'), L.join('\n'), 'utf8');
  console.log(`[build-markdown] availability matrix sidecar: ${cards.length} card(s), `
            + `${countries.length} countries, ${usStates.length} US states`);
}

/**
 * AEO-6: serve the sitemap where the crawlers actually ask for it.
 *
 * ClaudeBot requested /sitemap.xml 243 times in 15 days and got a 404 every
 * time; the working index lives at /sitemap-index.xml. Copying the index to
 * the conventional name costs one file and removes the whole class of problem.
 * Must run AFTER build-video-sitemap.py rewrites the index, or the copy would
 * be missing the video sub-sitemap -- deploy.sh orders it that way.
 */
async function buildSitemapAlias() {
  const src = join(DIST, 'sitemap-index.xml');
  if (!existsSync(src)) throw new Error('dist/sitemap-index.xml missing -- Astro build incomplete');
  const xml = await readFile(src, 'utf8');
  if (!xml.includes('<sitemapindex')) throw new Error('sitemap-index.xml is not a <sitemapindex>');
  await writeFile(join(DIST, 'sitemap.xml'), xml, 'utf8');
  const n = (xml.match(/<sitemap>/g) || []).length;
  console.log(`[build-markdown] /sitemap.xml alias written (${n} sub-sitemaps)`);
}

async function main() {
  console.log('[build-markdown] affiliate links resolved per-page via affiliates.mjs');
  if (!existsSync(DIST)) {
    console.error('[build-markdown] dist/ not found -- run pnpm build first');
    process.exit(2);
  }

  // Walk content/pseo/*.md; emit dist/<bucket>/<slug>/index.md
  const entries = await readdir(PSEO_SRC, { withFileTypes: true });
  let count = 0;
  for (const e of entries) {
    if (!e.isFile() || !e.name.endsWith('.md')) continue;
    const srcPath = join(PSEO_SRC, e.name);
    const slug = e.name.replace(/\.md$/, '');
    const built = await buildArticleMarkdown(srcPath, slug);
    const outDir = join(DIST, built.bucket, built.slug);
    await mkdir(outDir, { recursive: true });
    await writeFile(join(outDir, 'index.md'), built.body, 'utf8');
    count++;
  }

  // Homepage
  const home = await buildHomepageMarkdown();
  await writeFile(join(DIST, 'index.md'), home, 'utf8');

  await buildMatrixMarkdown();
  await buildLlmsInventory();
  await buildSitemapAlias();

  console.log(`[build-markdown] wrote ${count} article markdown + 1 homepage`);
}

main().catch((e) => {
  console.error('[build-markdown] fail:', e.message);
  process.exit(1);
});
