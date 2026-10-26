// /pinterest.xml — pin feed. The manifest (written by scripts/pinterest-feed.py)
// is the ONLY source: image paths, urls and dates are never recomputed here.
import rss from '@astrojs/rss';
import fs from 'node:fs';
import path from 'node:path';

const SITE = 'https://defycard.com';
const esc = (s) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

// Walk up from cwd (see freshness.mjs: module-relative paths break in the SSR bundle).
function seoRoot() {
  let d = process.cwd();
  for (let i = 0; i < 5; i++) {
    if (fs.existsSync(path.join(d, 'src', 'data', 'pinterest-manifest.json'))) return d;
    d = path.dirname(d);
  }
  throw new Error('pinterest-manifest.json not found');
}

export async function GET() {
  const root = seoRoot();
  const manifest = JSON.parse(
    fs.readFileSync(path.join(root, 'src', 'data', 'pinterest-manifest.json'), 'utf-8')
  );
  const entries = Object.entries(manifest).map(([slug, e]) => ({ slug, ...e }));

  const seen = new Set();
  for (const e of entries) {
    if (!e.url.startsWith(`${SITE}/`)) throw new Error(`pinterest feed: bad url ${e.url}`);
    if (seen.has(e.url)) throw new Error(`pinterest feed: duplicate url ${e.url}`);
    seen.add(e.url);
    if (!fs.existsSync(path.join(root, 'public', e.image)))
      throw new Error(`pinterest feed: missing image ${e.image}`);
  }

  entries.sort((a, b) => (a.added === b.added ? a.slug.localeCompare(b.slug) : a.added < b.added ? 1 : -1));
  const items = entries.slice(0, 50).map((e) => {
    const abs = SITE + e.image;
    const desc = `${e.description} (affiliate links on page)`;
    return {
      title: e.title.slice(0, 100),
      link: e.url,
      pubDate: new Date(e.added),
      description: desc,
      content: `<img src="${abs}" alt=""/><p>${esc(desc)}</p>`,
      enclosure: { url: abs, length: e.bytes, type: 'image/jpeg' },
      customData: `<media:content url="${abs}" medium="image" type="image/jpeg"/>`,
    };
  });
  return rss({
    title: 'DefyCard pins',
    description: 'DefyCard articles as pins.',
    site: SITE,
    xmlns: { media: 'http://search.yahoo.com/mrss/' },
    items,
  });
}
