"""G34 — the reader never sees the markup. Zero, not "few".

Bought on 2026-08-13: 544 of 782 built pages were serving raw markdown to
readers — 4,272 literal `**bold**` and 1,300 literal `[text](url)` — because a
placeholder is not a valid CommonMark destination, because frontmatter fields
were emitted without any markdown conversion, and because an injected HTML
block with no blank line around it swallowed the markdown that followed it.
Four separate causes, one symptom, and nothing was watching for the symptom.

THE LAW IS ZERO, and the thresholds are literals here (Y49):

  visible HTML text  (outside script/style/code/pre/textarea)
      0 x `**`                     -- emphasis that did not render
      0 x `[label](target)`        -- a link that did not render
      0 x `<<PLACEHOLDER>>`        -- a substitution that did not happen
      0 x `eth.fi`                 -- the writer's typo for ether.fi

  markdown sidecars  (dist/**/index.md, what the answer engines read)
      markdown is the NATIVE syntax there and is legal — Y56: one page, one
      law, but the FORM of the law follows the surface. Only the placeholders
      and the typo are checked.

If a literal `**` is ever genuinely wanted on a page, its place is inside
`<code>`, which this gate does not read — so the law stays zero rather than
acquiring an exception list.

Runs against dist/, like every gate here (Y50: measure the served artifact).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

DIST = Path(__file__).resolve().parent.parent / "dist"

# --- the literals -----------------------------------------------------------
# Zero for everything the pipeline controls.
MAX_PLACEHOLDER = 0
MAX_ETHFI_TYPO = 0

# ...and a NAMED list for anything that cannot be fixed yet, rather than a
# count. A count of "eight" hides which eight; a list fails on anything new and
# shows exactly what is owed. THE LIST MAY ONLY SHRINK —
# `test_the_known_list_is_not_stale` fails when an entry stops occurring, which
# forces its removal instead of letting it harden into folklore.
#
# IT IS EMPTY, AND THAT IS THE POINT. It held eight entries for one afternoon.
# Each had a cause that reading the template could not reveal, and each was
# fixed by hand once the RAW source bytes were read instead of the decoded
# string:
#
#   * emphasis inside a link LABEL splits the text node before the AST repair
#     can see the construct — the ceiling Fable named when specifying it;
#   * `[www.ether.fi](...)` — GFM autolinks the literal inside the label and
#     produces a link nested in a link;
#   * `~$20 x 12 = **~$240/year**` — two tildes pair into STRIKETHROUGH and the
#     resulting <del> swallows the emphasis between them;
#   * a snapshot bullet is split on its FIRST colon into key and value, cutting
#     a `**...**` pair in half, so the colon has to live inside the emphasis.
#
# And the reason three earlier attempts reported "0 applied": the literal-table
# runner inherited the pattern mode's page filter and was only ever shown pages
# that still contained the promo — which these no longer did. The strings were
# right; the file list was empty.
KNOWN = []
KNOWN_SET = {(p, f) for p, f in KNOWN}


def _unknown(hits):
    """Hits that are not on the named list. The list may only shrink."""
    return [(p, f) for p, f in hits if (p, f[:48]) not in KNOWN_SET]

_STRIP = re.compile(r"<(script|style|code|pre|textarea)\b[^>]*>.*?</\1>",
                    re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")
_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|#39|nbsp);")

LITERAL_BOLD = re.compile(r"\*\*[^*\n]{1,80}\*\*")
LITERAL_LINK = re.compile(r"\[[^\]\n]{1,120}\]\((?:https?://|/|<<)[^)\s]{0,160}\)")
PLACEHOLDER = re.compile(r"<<[A-Z][A-Z0-9_]*>>")
ETHFI_TYPO = re.compile(r"\beth\.fi\b", re.I)
DEAD_PLASMA_HOST = re.compile(r"help\.plasma\.org")
MAX_DEAD_PLASMA_HOST = 0


def _visible_text(html: str) -> str:
    """What a reader actually sees, with code and scripts removed."""
    body = _STRIP.sub(" ", html)
    text = _TAGS.sub(" ", body)
    return _ENTITY.sub(" ", text)


def _pages():
    if not DIST.is_dir():
        pytest.skip("dist/ not built")
    return sorted(DIST.rglob("index.html"))


def _sidecars():
    if not DIST.is_dir():
        pytest.skip("dist/ not built")
    return sorted(DIST.rglob("index.md"))


def _hits(rx, files, extract):
    out = []
    for f in files:
        text = extract(f.read_text(encoding="utf-8", errors="replace"))
        for m in rx.finditer(text):
            out.append((str(f.parent.relative_to(DIST)), m.group(0)[:70]))
    return out


def _raw_hits(rx, files):
    """Unlike `_hits`, reads the raw HTML/markdown bytes -- not the stripped
    visible text. A dead host lives inside an `href` attribute, which
    `_visible_text` discards as part of the tag; a reader's click still
    resolves against it even though the URL string is never shown as text."""
    out = []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in rx.finditer(text):
            out.append((str(f.parent.relative_to(DIST)), m.group(0)[:70]))
    return out


def _report(name, hits, limit):
    if len(hits) <= limit:
        return
    sample = "; ".join(f"{p}: {frag!r}" for p, frag in hits[:6])
    pytest.fail(f"{len(hits)} x {name} visible to readers (limit {limit}). {sample}")


def _report_new(name, hits):
    """Anything not on the named list is a regression and fails the deploy."""
    new = _unknown(hits)
    if not new:
        return
    sample = "; ".join(f"{p}: {frag!r}" for p, frag in new[:6])
    pytest.fail(f"{len(new)} NEW x {name} visible to readers "
                f"(the {len(KNOWN)} known ones are listed in this file). {sample}")


# --- the reader's copy ------------------------------------------------------

def test_no_literal_bold_reaches_the_reader():
    """`**Cashback:**` printed as characters means a field skipped the markdown
    renderer. It happened on 4,272 places across the site."""
    _report_new("literal ** bold", _hits(LITERAL_BOLD, _pages(), _visible_text))


def test_no_literal_markdown_link_reaches_the_reader():
    """A link the parser could not build is worse than no link: the reader sees
    the URL as punctuation, and the click never happens."""
    _report_new("literal markdown link", _hits(LITERAL_LINK, _pages(), _visible_text))


def test_no_unsubstituted_placeholder_reaches_the_reader():
    """`<<AFFILIATE_BUTTON>>` shipped to 424 pages because the deploy's final
    grep looked for `<<AFFILIATE_URL` only — a blacklist where an allow-list
    was needed."""
    _report("raw placeholder", _hits(PLACEHOLDER, _pages(), _visible_text),
            MAX_PLACEHOLDER)


def test_the_issuer_name_is_not_misspelled():
    """`eth.fi` is a writer typo for ether.fi. The pattern cannot match
    `ether.fi` — after `eth` there comes an `e`, not a dot."""
    assert not ETHFI_TYPO.search("ether.fi Cash is available")   # control
    _report("eth.fi typo", _hits(ETHFI_TYPO, _pages(), _visible_text),
            MAX_ETHFI_TYPO)


# --- the answer engines' copy ----------------------------------------------

def test_no_dead_plasma_host_reaches_the_reader():
    """T-0142-08 ruling-2: `help.plasma.org` is a reconstructed guess that has
    never resolved (NXDOMAIN both locally and at 1.1.1.1) -- 6 geo pages
    (india, turkey, arizona, nevada, georgia, washington) linked their Plasma
    One row to it with a "checked <date>" stamp under a link nobody had
    actually opened (T-0134's trap). The real host is
    intercom.help/plasma-6c43763cd799/en/articles/14703938. Checked against
    the raw HTML, not the stripped visible text, because the string a reader
    would never notice is the one baked into the href they click."""
    _report("dead help.plasma.org host", _raw_hits(DEAD_PLASMA_HOST, _pages()),
            MAX_DEAD_PLASMA_HOST)
    _report("dead help.plasma.org host in a sidecar",
            _raw_hits(DEAD_PLASMA_HOST, _sidecars()), MAX_DEAD_PLASMA_HOST)


def test_sidecars_keep_markdown_but_never_a_placeholder():
    """Markdown is the native syntax of a .md sidecar — bold and links there are
    correct and must NOT be 'fixed'. Only an unsubstituted placeholder or the
    typo is a defect."""
    _report("raw placeholder in a sidecar",
            _hits(PLACEHOLDER, _sidecars(), lambda s: s), MAX_PLACEHOLDER)
    _report("eth.fi typo in a sidecar",
            _hits(ETHFI_TYPO, _sidecars(), lambda s: s), MAX_ETHFI_TYPO)


def test_the_gate_can_actually_see_a_defect():
    """A gate that reports zero because it looks at nothing is the failure mode
    this whole round was about. Prove the extractor keeps prose and drops code."""
    html = ("<html><body><p>Rate is **3 %** and [see](/faq/x/).</p>"
            "<code>**not a defect**</code>"
            "<script>var x = '<<AFFILIATE_URL>>';</script></body></html>")
    text = _visible_text(html)
    assert LITERAL_BOLD.search(text), "extractor lost the visible bold"
    assert LITERAL_LINK.search(text), "extractor lost the visible link"
    assert "not a defect" not in text, "extractor kept <code> content"
    assert not PLACEHOLDER.search(text), "extractor kept <script> content"


def test_the_known_list_is_not_stale():
    """Every entry must still occur. When one stops, delete it — the list is a
    debt register, not a permanent exemption, and a stale entry would quietly
    widen the gate."""
    hits = {(p, f[:48]) for p, f in
            _hits(LITERAL_BOLD, _pages(), _visible_text)
            + _hits(LITERAL_LINK, _pages(), _visible_text)}
    gone = [e for e in KNOWN_SET if e not in hits]
    assert not gone, (
        f"{len(gone)} KNOWN entries no longer occur and must be removed from "
        f"the list: {gone[:4]}")


# --- E-17: Product/Offer schema on reviews, sourced from facts -------------
#
# FABLE_DEFYCARD_PART2.md section E, wave 3: bucket === 'reviews' pages whose
# resolved partner has a facts file get a Product/Offer block; every number
# in it must trace back to config/facts/<partner>.yaml, never aggregateRating
# (DefyCard collects no reviews of its own -- a fabricated rating is scaled
# content abuse, see seo-site/src/pages/[bucket]/[slug].astro's own comment
# on crypto-card-for-small-business.md, a pre-existing instance of exactly
# that, out of this task's scope to fix).
#
# The law is read straight out of config/facts/etherfi.yaml with yaml.safe_load
# -- never through facts_lib.py/facts.mjs, the code under test -- so a mutation
# to either loader cannot move both sides of the comparison (Y49/Y54's rule).

FACTS = DIST.parent.parent / "config" / "facts" / "etherfi.yaml"
CARD_PRICE_FACT_ID = "etherfi.fee.physical_card.core"


def _ld_json_blocks(path):
    html = path.read_text(encoding="utf-8")
    out = []
    for block in re.findall(r'type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            out.append(json.loads(block))
        except json.JSONDecodeError:
            continue
    return out


def _product_schemas(bucket, slug):
    path = DIST / bucket / slug / "index.html"
    if not path.exists():
        pytest.skip(f"{bucket}/{slug} not built")
    return [d for d in _ld_json_blocks(path)
            if isinstance(d, dict) and d.get("@type") == "Product"]


def _core_card_price_fact():
    data = yaml.safe_load(FACTS.read_text(encoding="utf-8"))
    fact = next(f for f in data["facts"] if f["id"] == CARD_PRICE_FACT_ID)
    assert fact["status"] == "current", "fixture fact is not publishable -- fix the fixture, not the test"
    return fact


def test_ether_fi_review_page_carries_a_product_offer_matching_facts():
    """The literal accepted by E-17: /reviews/ether-fi-cash-review/ ships a
    Product whose offers.price is the physical-card fee straight out of
    config/facts/etherfi.yaml -- not a hand-typed number that can drift from
    it (Y49/Y54: the law is the literal read from the fact file, not a value
    the renderer computes)."""
    fact = _core_card_price_fact()
    schemas = _product_schemas("reviews", "ether-fi-cash-review")
    assert len(schemas) == 1, f"expected exactly 1 Product block, found {len(schemas)}"
    product = schemas[0]
    assert product["offers"]["price"] == fact["value"], (product["offers"]["price"], fact["value"])
    assert product["offers"]["priceCurrency"] == fact["unit"]
    assert "aggregateRating" not in product


def test_every_ether_fi_review_variant_gets_the_schema():
    """Not just the one slug the acceptance literal names -- every review page
    that is actually about ether.fi Cash (ether-fi-cash-review-2026, -uk)."""
    fact = _core_card_price_fact()
    for slug in ("ether-fi-cash-review", "ether-fi-cash-review-2026", "ether-fi-cash-review-uk"):
        schemas = _product_schemas("reviews", slug)
        assert len(schemas) == 1, f"{slug}: expected 1 Product block, found {len(schemas)}"
        assert schemas[0]["offers"]["price"] == fact["value"], slug


def test_competitor_review_pages_get_no_product_schema():
    """`primary_partner` defaults to ether.fi on every /reviews/ page that
    doesn't set it explicitly (PROGRESS.md#T-w3-17-product-offer-schema-reviews
    -- true for all 28 review pages in this corpus today), so trusting that
    default alone would stamp an "ether.fi Cash" Product onto a RedotPay
    review. None of these partners have a config/facts/<slug>.yaml, and the
    acceptance criterion is explicit: no facts, no schema."""
    checked = 0
    for slug in ("redotpay-review", "crypto-com-card-review", "binance-card-review",
                 "coinbase-card-review", "nexo-card-review-2026", "bybit-card-review-2026",
                 "gnosis-pay-review", "metamask-card-review", "wirex-card-review"):
        path = DIST / "reviews" / slug / "index.html"
        if not path.exists():
            continue
        checked += 1
        schemas = _product_schemas("reviews", slug)
        assert schemas == [], f"{slug}: got a Product schema, expected none (no facts for this partner)"
    assert checked >= 5, f"only {checked} competitor review pages found to check"


def test_no_product_schema_ever_carries_an_aggregate_rating():
    """The acceptance literal, checked across every Product block the build
    produces, not just the one page named in the plan."""
    found = 0
    for path in _pages():
        for d in _ld_json_blocks(path):
            if isinstance(d, dict) and d.get("@type") == "Product":
                found += 1
                assert "aggregateRating" not in d, str(path.relative_to(DIST))
    assert found >= 3, f"only found {found} Product blocks across the build -- fixture drifted"


# CCH-PIN-00: og:image must be absolute (Pinterest Save / Rich Pins read it).
def test_og_image_is_absolute_on_every_page():
    bad = []
    for f in _pages():
        m = re.search(r'<meta property="og:image" content="([^"]*)"', f.read_text(errors="ignore"))
        if m and not m.group(1).startswith("https://defycard.com/assets/"):
            bad.append((str(f.parent.relative_to(DIST)), m.group(1)[:80]))
    assert not bad, bad[:10]


# --- CCH-KW-05: hot keywords on existing pages ------------------------------
# Expected values are LITERALS (Y49): never read back from the source files.
# A title moved without its schema headline is the defect this pins.
KW05_TITLES = {
    ("reviews", "coinbase-card-review"):
        "Coinbase Card Review 2026: the Coinbase debit card's fees & cashback",
    ("hub", "best-crypto-credit-card"):
        "Best Crypto Credit Card 2026: Crypto Rewards Credit Cards Ranked",
    ("reviews", "crypto-com-card-review"):
        "Crypto.com Card Review 2026: Cashback Rates, Fees & Alternatives",
}


@pytest.mark.parametrize("key", sorted(KW05_TITLES))
def test_kw05_title_and_jsonld_headline_agree(key):
    import html as _html
    bucket, slug = key
    path = DIST / bucket / slug / "index.html"
    if not path.exists():
        pytest.skip(f"{bucket}/{slug} not built")
    text = path.read_text(encoding="utf-8")
    title = _html.unescape(re.search(r"<title>(.*?)</title>", text, re.S).group(1))
    assert title == KW05_TITLES[key]
    headlines = [d.get("headline") for d in _ld_json_blocks(path)
                 if isinstance(d, dict) and d.get("@type") == "Article"]
    assert headlines == [KW05_TITLES[key]]


def test_kw05_reviews_index_h1():
    path = DIST / "reviews" / "index.html"
    if not path.exists():
        pytest.skip("reviews index not built")
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", path.read_text(encoding="utf-8"), re.S).group(1)
    assert h1.strip() == "Crypto card reviews"


# --- CCH-KW-02: one section, one heading -----------------------------------

_H2 = re.compile(r"<h2\b[^>]*>(.*?)</h2>", re.S | re.I)


def _h2_key(raw: str) -> str:
    t = re.sub(r"\s+", " ", _TAGS.sub(" ", raw)).strip().lower().rstrip(" :.?!")
    t = t.removeprefix("the ")
    return "faq" if t == "frequently asked questions" else t


def test_no_page_renders_the_same_h2_twice():
    bad = []
    for f in _pages():
        seen, dup = set(), set()
        for m in _H2.finditer(f.read_text(encoding="utf-8", errors="replace")):
            k = _h2_key(m.group(1))
            (dup if k in seen else seen).add(k)
        if dup:
            bad.append((str(f.parent.relative_to(DIST)), sorted(dup)))
    assert not bad, f"{len(bad)} pages repeat an <h2>: {bad[:6]}"


# CCH-SEO-10: entity plumbing. sameAs reaches the Organization; no author is
# invented (entity.author null => no byline anywhere); authorSchema is pure and
# unit-tested by node because a one-page rebuild with a fake author is too heavy.
def test_cch_seo_10_organization_sameas_and_no_byline_without_author():
    home = DIST / "index.html"
    if not home.is_file():
        pytest.skip("dist/ not built")
    orgs = [n for d in _ld_json_blocks(home) for n in (d.get("@graph", []) if isinstance(d, dict) else [])
            if n.get("@type") == "Organization"]
    assert orgs and "https://www.youtube.com/@defycard" in orgs[0]["sameAs"]
    assert "https://github.com/defycard-com" in orgs[0]["sameAs"]
    for path in _pages():
        assert 'class="byline"' not in path.read_text(encoding="utf-8"), str(path.relative_to(DIST))


def test_cch_seo_10_author_schema_two_literal_cases():
    import subprocess
    lib = Path(__file__).resolve().parent.parent / "src" / "lib" / "entity.mjs"
    js = (
        f"import {{authorSchema}} from '{lib}';"
        "const org={'@type':'Organization',name:'DefyCard'};"
        "console.log(JSON.stringify([authorSchema({author:null},org),"
        "authorSchema({author:{name:'Jane Roe',url:'https://example.com/jane',sameAs:['https://x.com/jane']}},org)]))"
    )
    out = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == [
        {"@type": "Organization", "name": "DefyCard"},
        {"@type": "Person", "name": "Jane Roe", "url": "https://example.com/jane",
         "sameAs": ["https://x.com/jane"]},
    ]
