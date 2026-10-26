"""G20 — every internal link resolves, in both serialisations.

Why: the writer prompt handed the model three example targets that did not
exist and told it Astro would drop dead links harmlessly. That was true of
`related_clusters` (RelatedLinks.astro filters them through the collection)
and false of body_markdown, which becomes a raw <a href>. 117 dead targets
and 218 dead links reached production, and crawlers walked them for weeks.

Reads the BUILT dist, because the defect lives between markdown and HTML and
neither source nor logs can see it: nginx undercounts (referrer-less crawls)
and overcounts (scanners forge Referer). Checks the md sidecars too — one page
is one law whatever the serialisation.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
REDIRECTS = ROOT / "redirects.map"

# The five paths already in crawler memory. Literals here on purpose: a gate
# that reads the law out of the file it is judging cannot fail (Y49/Y54).
EXPECTED_REDIRECTS = {
    "/compare/ether-fi-vs-crypto-com/": "/compare/ether-fi-cash-vs-crypto-com/",
    "/reviews/ether-fi-cash/": "/reviews/ether-fi-cash-review/",
    "/how-to/get-a-crypto-card/": "/how-to/how-to-get-a-crypto-card/",
    "/reviews/crypto-com-card/": "/reviews/crypto-com-card-review/",
    "/reviews/bybit-card/": "/reviews/bybit-card-review/",
    # CCH-SEO-03 — same-topic targets, each opened and confirmed.
    "/compare/multi-currency-crypto-cards/": "/for/crypto-card-for-multi-currency/",
    "/compare/best-crypto-cards-2026/": "/hub/best-crypto-card-2026/",
    "/education/what-is-self-custody/": "/learn/what-is-a-self-custody-card/",
    "/learn/non-custodial-vs-custodial/": "/learn/custodial-vs-non-custodial-card/",
    "/help/crypto-card-spending-limits/": "/how-to/how-to-set-spending-limit-crypto-card/",
    "/how-to/reduce-fx-fees/": "/for/crypto-card-with-no-fx-fees/",
    "/how-to/crypto-card-cashback-explained/": "/learn/how-does-crypto-cashback-work/",
    "/how-to/crypto-card-for-hotels/": "/for/crypto-card-for-hotels/",
    "/how-to/crypto-card-kyc-requirements/": "/learn/what-is-kyc-for-crypto-cards/",
    "/how-to/crypto-card-kyc/": "/learn/what-is-kyc-for-crypto-cards/",
    "/how-to/crypto-card-regulations/": "/learn/are-crypto-cards-regulated/",
    "/how-to/gnosis-pay-alternatives/": "/help/gnosis-pay-alternative/",
    "/how-to/non-custodial-cards-explained/": "/learn/what-is-a-non-custodial-crypto-card/",
    "/how-to/non-custodial-security/": "/faq/is-non-custodial-card-safer/",
    "/hub/reviews/crypto-com-card/": "/reviews/crypto-com-card-review/",
    "/learn/mica-compliant-crypto-cards/": "/learn/mica-compliant-vs-non-compliant-crypto-card/",
    "/learn/self-custody-vs-custodial/": "/learn/self-custody-card-vs-exchange-card/",
    "/reviews/coinbase-card/": "/reviews/coinbase-card-review/",
    "/how-to/apply-ether-fi-card/": "/how-to/how-to-set-up-ether-fi-cash/",
    "/education/crypto-card-guide/": "/learn/what-is-a-crypto-card/",
    "/reviews/nuri-card/": "/help/nuri-card-alternative/",
    # CCH-SEO-09 — near-duplicate consolidation, operator decision 2026-10-20.
    "/learn/self-custody-spending-vs-exchange-spending/": "/learn/self-custody-card-vs-exchange-card/",
    "/how-to/how-to-use-crypto-card-for-travel/": "/how-to/how-to-use-crypto-card-abroad/",
    "/how-to/how-to-use-crypto-card-on-uber/": "/for/crypto-card-for-uber/",
    "/learn/what-is-account-abstraction/": "/learn/what-is-a-smart-account-crypto-card/",
    "/faq/is-ether-fi-cash-a-debit-card/": "/faq/is-ether-fi-cash-a-credit-card/",
}

# CCH-SEO-03 — no same-topic live page: 410, never a 301 into a wrong topic.
EXPECTED_GONE = {
    "/compare/ether-fi-cash-vs-redotpay/",
    "/videos/fPMl-F4ecIg/",
    "/videos/571-AMmO5e8/",
    "/videos/eTPznhHYf8I/",
}

_HREF = re.compile(r'href="(/[^"#?]*)"')
_MD_LINK = re.compile(r"\]\(<?(/[a-z0-9\-/]*/)>?\)")


def _parse_redirects() -> dict[str, str]:
    if not REDIRECTS.exists():
        pytest.fail(f"{REDIRECTS} missing — the 301s live in the repo, not only in nginx")
    out: dict[str, str] = {}
    for line in REDIRECTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) == 2:
            out[parts[0]] = parts[1]
    return out


def _split_rows() -> tuple[dict[str, str], set[str]]:
    rows = _parse_redirects()
    return ({k: v for k, v in rows.items() if v != "410"},
            {k for k, v in rows.items() if v == "410"})


def _served_paths() -> set[str]:
    if not DIST.is_dir():
        pytest.fail(f"{DIST} missing — build first: cd seo-site && pnpm build")
    out = set()
    for p in DIST.rglob("index.html"):
        rel = "/" + str(p.relative_to(DIST)).removesuffix("index.html")
        out.add(rel)
    for p in DIST.rglob("*.html"):
        if p.name != "index.html":
            out.add("/" + str(p.relative_to(DIST)))
    return out


def _is_asset(path: str) -> bool:
    return bool(re.search(r"\.(png|jpe?g|svg|ico|css|js|xml|txt|webmanifest|json|md|webp|avif)$", path))


# --- the law ----------------------------------------------------------------

def test_redirects_map_matches_the_expected_pairs():
    assert _split_rows()[0] == EXPECTED_REDIRECTS


def test_gone_rows_match_the_expected_set():
    assert _split_rows()[1] == EXPECTED_GONE


def test_no_redirect_source_is_a_served_page():
    """An exact `location =` shadows a real page: the day a video goes public
    again its page would hide behind a 410. Mutation: add a /compare/ row -> red."""
    shadowed = sorted(set(_parse_redirects()) & _served_paths())
    assert not shadowed, f"redirect source(s) that are live pages in dist: {shadowed}"


def test_every_redirect_target_exists():
    served = _served_paths()
    for src, dst in _split_rows()[0].items():
        assert dst in served, f"{src} redirects to {dst}, which is not built — a 301 into a 404"


def test_no_html_page_links_to_a_path_that_does_not_exist():
    served, redirects = _served_paths(), _parse_redirects()
    dead: dict[str, str] = {}
    for p in DIST.rglob("*.html"):
        src = "/" + str(p.relative_to(DIST)).removesuffix("index.html")
        for m in _HREF.finditer(p.read_text(encoding="utf-8", errors="replace")):
            t = m.group(1)
            if _is_asset(t) or t in served or t in redirects:
                continue
            dead.setdefault(t, src)
    assert not dead, (
        f"{len(dead)} dead internal link target(s) in dist, e.g. "
        + ", ".join(f"{t} (from {s})" for t, s in list(dead.items())[:5])
    )


def test_no_markdown_sidecar_links_to_a_path_that_does_not_exist():
    """Same law, other serialisation — the AI-facing surface must not dead-end."""
    served, redirects = _served_paths(), _parse_redirects()
    dead: dict[str, str] = {}
    for p in DIST.rglob("index.md"):
        src = "/" + str(p.relative_to(DIST)).removesuffix("index.md")
        for m in _MD_LINK.finditer(p.read_text(encoding="utf-8", errors="replace")):
            t = m.group(1)
            if t in served or t in redirects:
                continue
            dead.setdefault(t, src)
    assert not dead, (
        f"{len(dead)} dead link target(s) in md sidecars, e.g. "
        + ", ".join(f"{t} (from {s})" for t, s in list(dead.items())[:5])
    )


# --- CCH-SEO-04: visible breadcrumbs + every bucket index linked -------------
# Literals on purpose: the law must not be read out of the layout it polices.
BUCKET_INDEXES = ["/hub/", "/compare/", "/reviews/", "/how-to/", "/for/", "/geo/",
                  "/tools/", "/learn/", "/help/", "/news/", "/faq/", "/videos/"]


def _breadcrumb_nav(html: str) -> str:
    m = re.search(r'<nav[^>]*aria-label="Breadcrumb"[^>]*>(.*?)</nav>', html, re.S)
    return m.group(1) if m else ""


def test_every_article_links_its_bucket_index_in_a_visible_breadcrumb():
    pages = [p for p in DIST.glob("*/*/index.html") if p.parent.parent.name + "/" in
             {b.strip("/") + "/" for b in BUCKET_INDEXES[:11]}]
    assert pages
    bad = []
    for p in pages:
        nav = _breadcrumb_nav(p.read_text(encoding="utf-8"))
        if f'href="/{p.parent.parent.name}/"' not in nav or "<ol" not in nav:
            bad.append(str(p.relative_to(DIST)))
    assert not bad, f"{len(bad)} article(s) without bucket link in breadcrumb: {bad[:5]}"


def test_every_page_links_every_bucket_index():
    art = next(DIST.glob("faq/*/index.html"))
    for page in (DIST / "index.html", art, DIST / "faq" / "index.html"):
        html = page.read_text(encoding="utf-8")
        missing = [h for h in BUCKET_INDEXES + ["/geo/availability-matrix/"] if f'href="{h}"' not in html]
        assert not missing, f"{page.relative_to(DIST)} lacks {missing}"


def test_no_page_links_sitemap_index_xml():
    bad = [str(p.relative_to(DIST)) for p in DIST.rglob("*.html")
           if 'href="/sitemap-index.xml"' in p.read_text(encoding="utf-8")]
    assert not bad, bad[:5]


# --- CCH-SEO-05: topical siblings + hub->spoke ------------------------------

def _inlink_counts() -> dict[str, int]:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "inlink_histogram", ROOT.parent / "scripts" / "inlink-histogram.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.histogram(DIST)


def test_hub_pages_list_their_cluster():
    html = (DIST / "hub" / "best-crypto-card-2026" / "index.html").read_text(encoding="utf-8")
    m = re.search(r'<section class="[^"]*hub-cluster[^"]*".*?</section>', html, re.S)
    assert m, "hub page has no 'In this guide cluster' section"
    assert "In this guide cluster" in m.group(0)
    links = {h for h in _HREF.findall(m.group(0)) if h.count("/") == 3
             and h != "/hub/best-crypto-card-2026/"}
    assert len(links) >= 10, f"only {len(links)} cluster links"


def test_min_inbound_content_links():
    """Before CCH-SEO-05: min 0, median 5, 111 of 580 pages under 3."""
    low = {u: n for u, n in _inlink_counts().items() if n < 3}
    assert not low, f"{len(low)} content page(s) with <3 inbound content links, e.g. {sorted(low.items())[:5]}"


def test_pick_related_independent_of_input_order(tmp_path):
    """getCollection order is not stable between builds; links must be."""
    import json, random, subprocess
    words = [f"w{i}" for i in range(400)]  # sparse overlap → shuffle-fill + repair paths run
    rnd = random.Random(1)
    entries = [
        {"data": {"bucket": rnd.choice(["geo", "faq", "hub"]), "slug": f"p{i:03d}",
                  "primary_keyword": " ".join(rnd.sample(words, 2)),
                  "supporting_keywords": [" ".join(rnd.sample(words, 2))], "h1": " ".join(rnd.sample(words, 2))}}
        for i in range(120)
    ]
    f = tmp_path / "e.json"
    f.write_text(json.dumps(entries))
    js = """
import {readFileSync} from 'node:fs';
const all = JSON.parse(readFileSync(process.argv[1],'utf8'));
const key = e => e.data.bucket+'/'+e.data.slug;
const out = [];
for (const [i, order] of [all, [...all].reverse(), [...all.slice(30), ...all.slice(0, 30)]].entries()) {
  const m = await import(process.argv[2] + '?v' + i);
  out.push(JSON.stringify(order.map(e => [key(e), m.pickRelated(e, order, 5).map(key)]).sort()));
}
console.log(new Set(out).size);
"""
    r = subprocess.run(["node", "--input-type=module", "-e", js, str(f), (ROOT / "src/lib/related.mjs").as_uri()],
                       capture_output=True, text=True)
    assert r.stdout.strip() == "1", r.stderr or r.stdout
