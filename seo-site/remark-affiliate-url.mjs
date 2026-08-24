/**
 * remark-affiliate-url -- Astro markdown plugin that resolves affiliate
 * placeholders at build time.
 *
 *   <<AFFILIATE_URL>>          -> the PAGE's primary partner
 *   <<AFFILIATE_URL:etherfi>>  -> that partner explicitly
 *
 * Y50 P0-1: the partner is now per-page, not a single global ether.fi URL.
 * A geo page for a jurisdiction where ether.fi is unavailable resolves the
 * bare placeholder to the fallback partner; the tagged form keeps ether.fi
 * where the surrounding sentence is genuinely about ether.fi. Partner data,
 * URL validation and .env loading all live in src/lib/affiliates.mjs.
 *
 * PRECEDENCE (council round 4 documentation requirement):
 *   1. THIS PLUGIN is the canonical substitution path. It runs at Astro
 *      markdown processing time, before HTML emission. Failure (invalid URL)
 *      -> build aborts. This is the only path that protects `astro dev`,
 *      `astro preview`, staging, and any non-deploy environment.
 *   2. deploy.sh post-build sed is a DEFENSIVE SAFETY NET (idempotent),
 *      catches anything the plugin missed (raw HTML strings, sitemap.xml,
 *      etc.). It runs AFTER Astro emits HTML and is a no-op on properly
 *      processed content.
 *
 * If both paths fire on the same content, the result is identical (sed
 * replacing the already-replaced string is a no-op since the placeholder
 * is gone). No precedence ambiguity.
 *
 * Per write-article skill 1.5: single source of truth = env var.
 *
 * URL VALIDATION (council round 4 SECURITY/BLOCK):
 *   - Reject non-http(s) schemes (defeats javascript:/data: stored-XSS).
 *   - Reject control chars, quotes, brackets, |, \, &.
 *   - Reject unparseable URLs via new URL() throwing.
 *
 * SSRF: this plugin does NOT fetch the URL at build time. It only writes
 * the URL string into HTML attributes. No network I/O.
 *
 * rel="sponsored noopener": NOT enforced by this plugin. Source markdown
 * already contains rel attribution from pseo-generate.py. The plugin only
 * substitutes the URL string in href= attributes; it never touches rel=.
 */
import { visit } from 'unist-util-visit';
import { resolveAffiliate, primaryPartnerFor } from './src/lib/affiliates.mjs';

const MARKER = '<<AFFILIATE_URL';

export function remarkAffiliateUrl() {
  return (tree, file) => {
    // Astro exposes the parsed frontmatter here; without it we cannot know the
    // page's partner, so fall back to the default program (ether.fi).
    const fm = file?.data?.astro?.frontmatter || {};
    const partner = primaryPartnerFor(fm);

    // ---- repair pass: links CommonMark could not parse ------------------
    // Must run BEFORE the substitution below. Once the URL has been written
    // into node.value there is no longer a placeholder to key on, and the
    // literal text is indistinguishable from prose the writer meant.
    const BROKEN = /\[([^\]\n]+)\]\(\s*(<<AFFILIATE_URL(?::[a-z0-9_-]+)?>>)\s*\)/g;
    visit(tree, 'text', (node, index, parent) => {
      if (!parent || index === null || index === undefined) return;
      if (typeof node.value !== 'string' || !node.value.includes('](' + MARKER)) return;
      const parts = [];
      let last = 0;
      let m;
      BROKEN.lastIndex = 0;
      while ((m = BROKEN.exec(node.value)) !== null) {
        if (m.index > last) {
          parts.push({ type: 'text', value: node.value.slice(last, m.index) });
        }
        parts.push({
          type: 'link',
          url: resolveAffiliate(m[2], partner),
          children: [{ type: 'text', value: m[1] }],
          // Affiliate links carry attribution; the CTA button already does.
          data: { hProperties: { rel: 'sponsored noopener', target: '_blank' } },
        });
        last = m.index + m[0].length;
      }
      if (!parts.length) return;
      if (last < node.value.length) {
        parts.push({ type: 'text', value: node.value.slice(last) });
      }
      parent.children.splice(index, 1, ...parts);
      return index + parts.length;   // continue after what we inserted
    });

    visit(tree, (node) => {
      // Markdown link nodes: replace in URL field
      if (node.url && typeof node.url === 'string' && node.url.includes(MARKER)) {
        node.url = resolveAffiliate(node.url, partner);
      }
      // Text nodes / inline HTML / raw values
      if (node.value && typeof node.value === 'string' && node.value.includes(MARKER)) {
        node.value = resolveAffiliate(node.value, partner);
      }
    });
  };
}
