// @ts-check
import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import { remarkAffiliateUrl } from './remark-affiliate-url.mjs';
import { readdirSync } from 'node:fs';
import { lastmodFor } from './src/lib/freshness.mjs';

// CCH-SEO-32: ids from the content collection, not a digit regex — /videos/2/ is pagination.
const VIDEO_IDS = new Set(readdirSync('./src/content/videos').filter((f) => f.endsWith('.json')).map((f) => f.slice(0, -5)));

export default defineConfig({
  site: 'https://defycard.com',
  trailingSlash: 'always',
  build: {
    format: 'directory',
  },
  markdown: {
    remarkPlugins: [remarkAffiliateUrl],
  },
  integrations: [
    sitemap({
      changefreq: 'weekly',
      priority: 0.7,
      // AEO-5: NO global `lastmod`. It used to be `new Date()`, which stamped
      // all 758 URLs with the build time and announced a site-wide change on
      // every deploy — the freshness signal was not just missing, it was
      // inverted. serialize() now dates each URL from its own content, and
      // drops the field entirely where no honest date exists.
      serialize: (item) => {
        const d = lastmodFor(item.url);
        if (d) item.lastmod = `${d}T00:00:00.000Z`;
        else delete item.lastmod;
        return item;
      },
      // Exclude noindex utility pages (/search/) and any private/admin routes.
      filter: (page) =>
        !page.includes('/admin') &&
        !page.includes('/_') &&
        !/\/search\/?$/.test(page) &&
        !/\/404\/?$/.test(page) &&
        !VIDEO_IDS.has((page.match(/\/videos\/([^/]+)\/?$/) || [])[1]),
    }),
  ],
});
