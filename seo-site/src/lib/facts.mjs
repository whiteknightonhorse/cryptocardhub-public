/**
 * facts.mjs — read-only JS mirror of scripts/facts_lib.py's PUBLISHABLE_STATUS
 * filter, for the one thing Python cannot hand to an Astro build: JSON-LD
 * baked into a page at render time.
 *
 * Does not re-implement schema validation (id/class/confidence/expires) —
 * that law lives in one place, scripts/facts_lib.py + scripts/facts-render.py
 * --check, which already gates the file this reads. This only re-applies the
 * narrow, load-bearing rule that a `disputed`/`expired` fact must never reach
 * a render (Y50/Y56): status not in PUBLISHABLE_STATUS -> filtered out here too.
 *
 * One facts file per partner, named after the partner's affiliates.yaml slug
 * (config/facts/<slug>.yaml). A partner with no such file has no facts, and
 * therefore never gets fact-sourced schema — E-17's own acceptance criterion.
 */
import fs from 'node:fs';
import path from 'node:path';
import { parse as parseYaml } from 'yaml';

// NOT `path.resolve(import.meta.dirname, ...)` (the pattern affiliates.mjs
// uses): this module has exactly one consumer ([bucket]/[slug].astro), so
// Astro's SSG build inlines it into that page's compiled chunk at
// dist/pages/**.astro.mjs, and `import.meta.dirname` at runtime then points
// into dist/, not src/lib/ — three levels up lands on `seo-site`, not the
// repo root, and fs.existsSync() on the wrong path fails CLOSED (silently
// "no facts") rather than erroring, which is worse (found empirically:
// `config/facts/etherfi.yaml` resolved to `seo-site/config/facts/etherfi.yaml`
// and the whole feature no-opped with no error, on every page, every build).
// `astro build`/`astro dev` always run with cwd = the project dir containing
// astro.config.mjs (seo-site/), so cwd is the one location bundling cannot
// move.
const ROOT = path.resolve(process.cwd(), '..');

const PUBLISHABLE_STATUS = new Set(['current', 'beta']);

const cache = new Map();

/** Publishable (current/beta) facts for a partner slug, or null if the
 *  partner has no facts file at all. */
export function factsForPartner(slug) {
  if (cache.has(slug)) return cache.get(slug);
  const file = path.join(ROOT, 'config', 'facts', `${slug}.yaml`);
  let result = null;
  if (fs.existsSync(file)) {
    const data = parseYaml(fs.readFileSync(file, 'utf8')) || {};
    result = (data.facts || []).filter((f) => PUBLISHABLE_STATUS.has(f.status));
  }
  cache.set(slug, result);
  return result;
}

/** A single fact record by id out of an already-loaded (publishable-only)
 *  list, or null if absent/not publishable. */
export function factById(facts, id) {
  return (facts || []).find((f) => f.id === id) || null;
}
