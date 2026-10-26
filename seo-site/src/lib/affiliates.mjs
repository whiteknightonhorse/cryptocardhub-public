/**
 * affiliates.mjs — single build-time resolver for every partner URL on the site.
 *
 * WHY (Y50 P0-1): before this file, `<<AFFILIATE_URL>>`, the layout sticky CTA
 * and the article CTA each hard-coded ether.fi. A geo page for a jurisdiction
 * where ether.fi is NOT available therefore offered exactly one external card
 * link — to a product the reader cannot get. This module makes the partner a
 * property of the page, sourced from config/affiliates.yaml + config/availability.yaml.
 *
 * Consumers: remark-affiliate-url.mjs, pages/[bucket]/[slug].astro,
 * layouts/BaseLayout.astro (click tracker host list).
 *
 * SECURITY: URL validation is unchanged from remark-affiliate-url.mjs — reject
 * non-http(s) schemes and any char that could escape an HTML attribute or the
 * deploy.sh sed delimiter. A bad value aborts the build; there is no silent
 * fallback, because a silently-wrong referral URL loses money invisibly.
 */
import fs from 'node:fs';
import path from 'node:path';
import { parse as parseYaml } from 'yaml';

const ROOT = path.resolve(import.meta.dirname, '../../..');

/** Minimal `KEY=value` reader for the repo-root .env.
 *  Astro/Vite only loads .env from the Astro project dir; ours lives one level
 *  up and is shared with the Python pipelines. deploy.sh `set -a; . .env`
 *  already exports these, so this only matters for bare `pnpm build`/`astro dev`.
 *  ponytail: no quoting/multiline support — .env here is flat KEY=value. */
function loadRootEnv() {
  const file = path.join(ROOT, '.env');
  if (!fs.existsSync(file)) return {};
  const out = {};
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    const m = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line);
    if (m) out[m[1]] = m[2].trim().replace(/^["']|["']$/g, '');
  }
  return out;
}

function validateUrl(url, slug) {
  if (typeof url !== 'string' || !url) {
    throw new Error(`[affiliates] partner "${slug}" has an empty URL`);
  }
  if (!url.startsWith('https://') && !url.startsWith('http://')) {
    throw new Error(`[affiliates] partner "${slug}" URL scheme not http(s): ${url.slice(0, 40)}`);
  }
  if (/[\x00-\x20"<>|\\&]/.test(url)) {
    throw new Error(`[affiliates] partner "${slug}" URL contains forbidden chars`);
  }
  try {
    new URL(url);
  } catch {
    throw new Error(`[affiliates] partner "${slug}" URL not parseable: ${url.slice(0, 40)}`);
  }
}

function build() {
  const env = { ...loadRootEnv(), ...process.env };
  const cfg = parseYaml(fs.readFileSync(path.join(ROOT, 'config/affiliates.yaml'), 'utf8'));
  const avail = parseYaml(fs.readFileSync(path.join(ROOT, 'config/availability.yaml'), 'utf8'));

  const partners = {};
  for (const [slug, p] of Object.entries(cfg.affiliates || {})) {
    // "${VAR}" -> env value. If the referral URL is unset we fall back to the
    // program's PUBLIC product page: the reader still reaches a card they can
    // actually get, we just earn nothing. Falling back to the DEFAULT partner
    // instead would re-create the exact bug this module exists to fix.
    const ref = String(p.url || '').replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (_, v) => env[v] ?? '');
    const isReferral = !!ref && !ref.includes('${');
    const url = isReferral ? ref : p.url_fallback;
    if (!url) continue;
    validateUrl(url, slug);
    if (!isReferral) {
      console.warn(
        `[affiliates] WARNING: "${slug}" has no referral URL (${p.url}) — using the ` +
        `public page ${url}. These clicks earn 0 commission.`
      );
    }
    partners[slug] = { slug, name: p.name, cta: p.cta, url, host: new URL(url).hostname, isReferral };
  }

  const fallback = avail?.etherfi?.fallback_partner || cfg.default;
  if (!partners[cfg.default]) {
    throw new Error(`[affiliates] default partner "${cfg.default}" did not resolve — check .env`);
  }
  if (!partners[fallback]) {
    throw new Error(
      `[affiliates] fallback partner "${fallback}" did not resolve — geo pages where ` +
      `ether.fi is unavailable would fall back to ether.fi. Check .env for its URL var.`
    );
  }
  return { partners, cfg, avail, fallback };
}

const { partners, cfg, avail, fallback } = build();

export const PARTNERS = partners;
export const DEFAULT_PARTNER = cfg.default;
export const FALLBACK_PARTNER = fallback;

/** Hosts of every resolvable partner — drives the register_click tracker.
 *  Derived, never hand-listed: adding a program to affiliates.yaml tracks it. */
export const PARTNER_HOSTS = Object.values(partners).map((p) => p.host);

/** slug -> host, so the click event can name WHICH partner was clicked. */
export const PARTNER_HOST_MAP = Object.fromEntries(
  Object.values(partners).map((p) => [p.host, p.slug])
);

/** pSEO slugs whose jurisdiction cannot get ether.fi (config/availability.yaml). */
export const UNAVAILABLE_SLUGS = new Set(avail?.unavailable_slugs || []);

/**
 * T-0142-15: where `primary_partner === 'none'` — no card this site tracks
 * can currently be issued to the page's readers (e.g. india, turkey). The
 * generic CTAs route here instead of to any partner, so the page never sends
 * a reader to a card they cannot get.
 */
export const NO_CARD_CTA = '/geo/availability-matrix/';

export function partnerUrl(slug) {
  const p = PARTNERS[slug];
  if (!p) throw new Error(`[affiliates] unknown partner "${slug}" — not in config/affiliates.yaml`);
  return p.url;
}

/**
 * The partner a page should send its generic CTAs to.
 * Explicit frontmatter wins; otherwise ether.fi-unavailable geo slugs route to
 * the fallback partner and everything else keeps the default.
 */
export function primaryPartnerFor(entryData = {}) {
  if (entryData.primary_partner) return entryData.primary_partner;
  if (UNAVAILABLE_SLUGS.has(entryData.slug)) return FALLBACK_PARTNER;
  return DEFAULT_PARTNER;
}

const BARE = '<<AFFILIATE_URL>>';
const TAGGED = /<<AFFILIATE_URL:([a-z0-9_-]+)>>/gi;

/**
 * Resolve both placeholder forms in a string.
 *   <<AFFILIATE_URL>>        -> the page's primary partner
 *   <<AFFILIATE_URL:etherfi>> -> that partner explicitly
 * The tagged form exists so a page routed to Crypto.com can still link ether.fi
 * where the surrounding sentence is genuinely about ether.fi (e.g. the
 * Switzerland section of the India page) instead of silently mislabelling it.
 */
export function resolveAffiliate(text, primary = DEFAULT_PARTNER) {
  if (typeof text !== 'string' || !text) return text;
  const tagged = text.replace(TAGGED, (_, slug) => partnerUrl(slug.toLowerCase()));
  // Lazy: only resolve the bare placeholder's URL if it is actually present.
  // `.join(partnerUrl(primary))` used to run unconditionally as a `.join()`
  // argument, so ANY call with primary === 'none' (a page with no obtainable
  // card, T-0142-15) threw even when the text carried no placeholder at all.
  if (!tagged.includes(BARE)) return tagged;
  const url = primary === 'none' ? NO_CARD_CTA : partnerUrl(primary);
  return tagged.split(BARE).join(url);
}

/**
 * mdField -- render the INLINE markdown that pSEO frontmatter fields carry.
 *
 * Scope is deliberately tiny: **bold** and [text](url). These fields are not
 * documents, they are labelled one-liners, and the generator's schema asks for
 * exactly these two constructs. Anything else stays literal, visibly, so a new
 * construct shows up in the dist gate instead of silently half-rendering.
 *
 * Escaping first, then markdown: the field content is model output and reaches
 * the page through set:html.
 */
export function mdField(text) {
  if (typeof text !== 'string' || !text) return text;
  const esc = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
  return esc
    // [label](href) -- href allow-listed: internal path, http(s), mailto, #
    // One level of nesting is allowed inside the destination so that a
    // rejected href like `javascript:alert(1)` is consumed WHOLE. Matching
    // only up to the first `)` left a stray bracket in the sentence — the very
    // class of artifact this function exists to remove.
    .replace(/\[([^\]\n]+)\]\(((?:[^()\s]|\([^()]*\))*)\)/g, (whole, label, href) => {
      const ok = /^(https?:\/\/|\/|mailto:|#)/.test(href);
      if (!ok) return label;          // never a link to nowhere
      const ext = /^https?:\/\//.test(href);
      const rel = ext ? ' rel="sponsored noopener" target="_blank"' : '';
      return `<a href="${href}"${rel}>${label}</a>`;
    })
    // **bold**
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
}
