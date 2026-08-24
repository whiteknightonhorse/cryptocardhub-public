/**
 * availability.mjs — AEO-1. The ONLY place the availability matrix gets facts.
 *
 * Every country name, every Available/Not available verdict and every date on
 * /geo/availability-matrix/ comes out of config/availability.yaml through this
 * module. Nothing is hard-coded in the template — a fact that is written in a
 * template cannot be re-verified, cannot be dated, and cannot be corrected by
 * whoever owns the data.
 *
 * Two rules, enforced here rather than in the page:
 *   1. A card with no `verified` date is NOT published (`publishedCards`).
 *      A narrow honest table beats a wide imaginary one.
 *   2. A card whose `verified` date is older than STALE_AFTER_DAYS is still
 *      published, but flagged. The page shows the age either way; it never
 *      shows a fresh-looking date that no check produced.
 *
 * Consumers: pages/geo/availability-matrix.astro, lib/freshness.mjs,
 * scripts/build-markdown.js, tests/test_availability_matrix.py (over dist/).
 */
import fs from 'node:fs';
import path from 'node:path';
import { parse as parseYaml } from 'yaml';

const ROOT = path.resolve(import.meta.dirname, '../../..');

/** Past this many days without a check the matrix shows an overdue badge.
 *  Mirrored as a literal in the gate and in scripts/weekly-report.py. */
export const STALE_AFTER_DAYS = 60;

/** Exact phrase the overdue badge renders. The gate greps for it in dist/. */
export const STALE_BADGE_PREFIX = 'data from';
export const STALE_BADGE_SUFFIX = 'check overdue';

export const AVAILABILITY = parseYaml(
  fs.readFileSync(path.join(ROOT, 'config/availability.yaml'), 'utf8'),
);

/** Cards that may be shown: rule 1. Order follows the YAML. */
export function publishedCards(cards = AVAILABILITY.cards || {}) {
  return Object.entries(cards)
    .filter(([, c]) => !!c && !!c.verified)
    .map(([key, c]) => ({ key, ...c }));
}

/** Whole-day age of a `verified` date. `today` is injectable so the gate can
 *  fixture a 61-day-old card without waiting 61 days. */
export function verifiedAgeDays(verified, today = new Date()) {
  const d = Date.parse(`${verified}T00:00:00Z`);
  if (Number.isNaN(d)) return Infinity;
  const t = Date.parse(`${today.toISOString().slice(0, 10)}T00:00:00Z`);
  return Math.floor((t - d) / 86400000);
}

export function isStale(verified, today = new Date()) {
  return verifiedAgeDays(verified, today) > STALE_AFTER_DAYS;
}

/**
 * The one line every card column shows about its own age.
 *
 * Deliberately NOT a branch in the .astro template. If the template said
 * `{isStale(...) && <badge/>}`, deleting that one expression would silently
 * turn an overdue table into a confident-looking one, and the only test that
 * could catch it would need a 110-second site rebuild against a doctored YAML.
 * With the branch here, the template renders whatever string it is handed:
 * the fast unit test covers the rule, and the dist/ gate covers the wiring.
 */
export function availabilityNote(card, today = new Date()) {
  const age = verifiedAgeDays(card.verified, today);
  if (age > STALE_AFTER_DAYS) {
    return `${STALE_BADGE_PREFIX} ${card.verified} (${age} days old) — ${STALE_BADGE_SUFFIX}`;
  }
  return `Last verified: ${card.verified} (${age} days ago)`;
}

/**
 * The union of every jurisdiction any published card has an opinion about,
 * sorted. Countries and US states are kept in separate tables — "Georgia" is
 * both a country and a US state and merging them is how a page starts lying.
 */
export function jurisdictions(cards) {
  const countries = new Set();
  const states = new Set();
  for (const c of cards) {
    for (const x of c.countries || []) countries.add(x);
    for (const x of c.us_states_blocked || []) states.add(x);
  }
  return {
    countries: [...countries].sort(),
    usStates: [...states].sort(),
  };
}

/**
 * Verdict for one (card, jurisdiction) cell.
 *   blocklist  -> listed means NOT available
 *   allowlist  -> listed means available
 * A card whose mode we do not understand returns null and the cell renders as
 * "not stated" rather than guessing in the issuer's favour.
 */
export function cellFor(card, name, kind = 'country') {
  const list = kind === 'state' ? (card.us_states_blocked || []) : (card.countries || []);
  const listed = list.includes(name);
  if (kind === 'state') return listed ? false : true;   // states are always a blocklist
  if (card.mode === 'blocklist') return !listed;
  if (card.mode === 'allowlist') return listed;
  return null;
}

/** slug of the honest "not available here" page for a country, when we have one.
 *  Read from unavailable_slugs so the matrix links to the real alternative
 *  instead of dead-ending the reader on a red cell. */
export function geoSlugFor(name) {
  const want = `best-crypto-card-${name.toLowerCase().replace(/\s+/g, '-')}`;
  return (AVAILABILITY.unavailable_slugs || []).includes(want) ? want : null;
}
