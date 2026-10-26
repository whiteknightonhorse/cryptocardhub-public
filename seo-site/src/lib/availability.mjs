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

/** slug -> {name, kind}: the join table between our URLs and the issuer's
 *  jurisdiction names. Not derivable — `best-crypto-card-georgia` is the US
 *  state, `best-crypto-card-uk` is "United Kingdom". */
export const SLUG_JURISDICTIONS = AVAILABILITY.slug_jurisdictions || {};

/**
 * The union of every jurisdiction any published card has an opinion about,
 * sorted. Countries and US states are kept in separate tables — "Georgia" is
 * both a country and a US state and merging them is how a page starts lying.
 *
 * Three YAML-sourced sets, and the third is load-bearing. Under an allow-list
 * the rows are the countries the issuer NAMES, so India — absent from the
 * issuer's page, and the single highest-converting object on this site — would
 * silently drop out of the matrix along with its link to the page that names
 * what an Indian reader CAN get. That is the Y50 money bug re-opening through
 * a shape change. So every jurisdiction we publish an honest "not available
 * here" page about stays a row, and it stays a row because the YAML says so,
 * not because the template does.
 */
export function jurisdictions(cards) {
  const countries = new Set();
  const states = new Set();
  for (const c of cards) {
    for (const x of c.countries || []) countries.add(x);
    for (const x of c.countries_explicit_no || []) countries.add(x);
    for (const x of c.countries_document_blocked || []) countries.add(x);
    for (const x of c.us_states || []) states.add(x);
    for (const x of c.us_states_explicit_no || []) states.add(x);
    for (const x of c.us_states_blocked || []) states.add(x);   // blocklist cards
  }
  for (const { name, kind } of Object.values(SLUG_JURISDICTIONS)) {
    (kind === 'us_state' ? states : countries).add(name);
  }
  return {
    countries: [...countries].sort(),
    usStates: [...states].sort(),
  };
}

/**
 * Verdict for one (card, jurisdiction) cell.
 *   blocklist  -> listed means NOT available, absent means available
 *   allowlist  -> listed means available, ABSENT MEANS NOT (default-deny)
 * A card whose mode we do not understand returns null and the cell renders as
 * "not stated" rather than guessing in the issuer's favour.
 *
 * `kind` picks the list, never the rule. The state branch used to hard-code
 * "states are always a blocklist"; the issuer publishes an allow-list of 31 US
 * jurisdictions now, and under that hard-coded rule Mississippi read as
 * "Available".
 *
 * COUNTRY BRANCH, T-0142-08: `countries: null` and `countries: []` are two
 * different facts, not two spellings of the same one. `null` means the issuer
 * publishes no global allow-list at all (Plasma One: "a growing number of
 * countries ... determined by our card issuing partner") — a country we have
 * no opinion on reads "Not stated", never "Not available". `[]` (or a real
 * list) is default-deny as usual: a name absent from a STATED list is refused.
 * `countries_explicit_no` / `countries_document_blocked` are the issuer's own
 * named refusals and win over both — a country the issuer explicitly blocks
 * is "Not available" even while the general allow-list stays unpublished.
 */
export function cellFor(card, name, kind = 'country') {
  const allow = card.mode === 'allowlist';
  if (kind === 'state') {
    const list = (allow ? card.us_states : card.us_states_blocked) || [];
    const listed = list.includes(name);
    if (card.mode === 'blocklist') return !listed;
    if (allow) return listed;
    return null;
  }
  const blocked = [card.countries_explicit_no, card.countries_document_blocked]
    .some((l) => Array.isArray(l) && l.includes(name));
  if (blocked) return false;
  if (card.mode === 'blocklist') return !(card.countries || []).includes(name);
  if (allow) {
    if (card.countries === null || card.countries === undefined) return null;
    return card.countries.includes(name);
  }
  return null;
}

/** slug of the honest "not available here" page for a jurisdiction, when we
 *  have one, so the matrix links the real alternative instead of dead-ending
 *  the reader on a red cell. Read from the explicit map, not re-slugified:
 *  a second name->slug implementation is the defect this project has already
 *  paid for four times. */
export function geoSlugFor(name) {
  const published = new Set(AVAILABILITY.unavailable_slugs || []);
  for (const [slug, j] of Object.entries(SLUG_JURISDICTIONS)) {
    if (j.name === name && published.has(slug)) return slug;
  }
  return null;
}

/** True when any published card states availability as an allow-list. Drives
 *  the one sentence the page says about absent jurisdictions — that sentence
 *  is a fact about the issuer's format and must not live in the template. */
export function defaultDeny(cards) {
  return cards.some((c) => c.mode === 'allowlist');
}
