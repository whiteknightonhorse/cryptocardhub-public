// @ts-check
import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import { remarkAffiliateUrl } from './remark-affiliate-url.mjs';
import { lastmodFor } from './src/lib/freshness.mjs';

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
        !/\/search\/?$/.test(page),
    }),
  ],
});
