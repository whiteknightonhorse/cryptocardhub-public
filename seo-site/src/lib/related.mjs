// Topical related-page scoring: one function, used by BucketSiblings and the
// hub→spoke blocks. Pure + deterministic (same build → same links).
// ponytail: O(n²) scoring, fine at ~580 pages; past ~5,000 precompute an
// inverted index (token → pages) instead of scoring every pair.
const STOP = new Set('a the for of in to is vs and or with card cards crypto 2026 best'.split(' '));

export function tokens(entry) {
  const d = entry.data;
  const text = [d.primary_keyword, ...(d.supporting_keywords || []), d.h1].join(' ').toLowerCase();
  return new Set(text.split(/[^a-z0-9]+/).filter((w) => w && !STOP.has(w)));
}

export function score(a, b) {
  let inter = 0;
  for (const t of a) if (b.has(t)) inter++;
  const union = a.size + b.size - inter;
  return union ? inter / union : 0;
}

export function seededShuffle(arr, seed) {
  let s = 0;
  for (const c of seed) s = (s * 31 + c.charCodeAt(0)) >>> 0;
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    s = (s * 1103515245 + 12345) >>> 0;
    const j = s % (i + 1);
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

const cache = new WeakMap();
const tok = (e) => cache.get(e) || (cache.set(e, tokens(e)), cache.get(e));
const key = (e) => `${e.data.bucket}/${e.data.slug}`;

/** All other pages with score > 0 (or ≥ min), best first; ties → same bucket, then slug asc. */
export function ranked(entry, all, min = 0, strict = true) {
  const t = tok(entry);
  return all
    .filter((e) => key(e) !== key(entry))
    .map((e) => ({ e, s: score(t, tok(e)) }))
    .filter(({ s }) => (strict ? s > min : s >= min))
    .sort((x, y) =>
      y.s - x.s ||
      (y.e.data.bucket === entry.data.bucket) - (x.e.data.bucket === entry.data.bucket) ||
      key(x.e).localeCompare(key(y.e)))
    .map(({ e }) => e);
}

function basePicks(entry, all, n) {
  const top = ranked(entry, all).slice(0, n);
  if (top.length >= n) return top;
  const have = new Set(top.map(key));
  const fill = seededShuffle(
    all.filter((e) => e.data.bucket === entry.data.bucket && key(e) !== key(entry) && !have.has(key(e))),
    entry.data.slug,
  );
  return top.concat(fill.slice(0, n - top.length));
}

// Pure top-n by score funnels links into a few generic pages and leaves niche
// ones (a single state, a single news item) with ~0 inbound. Repair pass: each
// page below MIN_IN inbound takes a slot from its best-scoring linkers, in
// place of that linker's weakest pick, only when the evicted page keeps > MIN_IN.
// Deterministic: fixed iteration order, no randomness beyond the seeded fill.
const MIN_IN = 3;
let graph = null, graphKey = '';
function buildGraph(input, n) {
  // getCollection order is not stable between builds: canonicalise first, so the
  // shuffle input and the repair pass see the same sequence every time.
  const all = [...input].sort((a, b) => key(a).localeCompare(key(b)));
  const k = `${n}:${all.map(key).join('|')}`;
  if (graph && graphKey === k) return graph;
  const picks = new Map(all.map((e) => [key(e), basePicks(e, all, n)]));
  const inb = new Map(all.map((e) => [key(e), 0]));
  for (const ps of picks.values()) for (const p of ps) inb.set(key(p), inb.get(key(p)) + 1);
  const order = [...all].sort((x, y) => inb.get(key(x)) - inb.get(key(y)) || key(x).localeCompare(key(y)));
  for (const need of order) {
    if (inb.get(key(need)) >= MIN_IN) continue;
    const t = tok(need);
    const donors = all
      .filter((d) => key(d) !== key(need) && !picks.get(key(d)).some((p) => key(p) === key(need)))
      .map((d) => ({ d, s: score(t, tok(d)), same: d.data.bucket === need.data.bucket }))
      .filter(({ s, same }) => s > 0 || same)
      .sort((x, y) => y.s - x.s || y.same - x.same || key(x.d).localeCompare(key(y.d)));
    for (const { d } of donors) {
      if (inb.get(key(need)) >= MIN_IN) break;
      const dt = tok(d);
      const ps = picks.get(key(d));
      let vi = -1, vs = Infinity;
      ps.forEach((p, i) => {
        if (inb.get(key(p)) <= MIN_IN) return;
        const sc = score(dt, tok(p));
        if (sc < vs) { vs = sc; vi = i; }
      });
      if (vi < 0) continue;
      inb.set(key(ps[vi]), inb.get(key(ps[vi])) - 1);
      ps[vi] = need;
      inb.set(key(need), inb.get(key(need)) + 1);
    }
  }
  graph = picks; graphKey = k;
  return picks;
}

export function pickRelated(entry, all, n) {
  return buildGraph(all, n).get(key(entry)) || [];
}
