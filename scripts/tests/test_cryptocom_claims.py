"""G45 (T-0142-14) — Crypto.com on the 7 fallback pages: measured, then judged.

Two laws, proven with literal mutations (Y49: the expected value is a literal
in the test, never re-derived from the module under test):

  1. every config/facts/cryptocom.yaml record carries `expires` — mutation:
     strip it from one record -> validate() goes red.
  2. every availability fact whose value is a definite "available"/"not
     available" carries a non-empty source_url AND source_quote (T-0142-13's
     own rule, transplanted) — mutation: blank the url on a "available" fact
     -> validate() goes red. "not stated" needs neither.

Plus: the real report, run against the real 7 pages, reroutes exactly the
jurisdictions this run measured as not "available" (India, Turkey) and
leaves the five "available" ones alone — and the pages themselves are never
touched (T-0142-14's own scope line).
"""
from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import facts_lib as fl  # noqa: E402


def _spec_load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


cc = _spec_load("cryptocom_claims", SCRIPTS / "cryptocom_claims.py")

REAL_FACTS = cc.load_facts()
REAL_ETHERFI_FACTS = fl.load(cc.ETHERFI_FACTS_PATH)


def _base_fact(**overrides) -> dict:
    fact = {
        "id": "cryptocom.test.x",
        "value": "available",
        "unit": None,
        "applies_to": {"jurisdiction": "Testland"},
        "source_url": "https://example.com/eligibility",
        "source_quote": "Testland is eligible",
        "verified_at": "2026-09-24",
        "expires": "2026-12-23",
        "status": "current",
        "class": "availability",
        "confidence": "issuer_blog",
        "history": [],
        "promo_restricted": [],
    }
    fact.update(overrides)
    return fact


# ---------------------------------------------------------------------------
# The fact file itself
# ---------------------------------------------------------------------------

def test_the_real_cryptocom_facts_file_loads_and_validates():
    assert len(REAL_FACTS) >= 7
    ids = [f["id"] for f in REAL_FACTS]
    assert len(ids) == len(set(ids)), "duplicate ids"
    cc.validate(REAL_FACTS)  # must not raise


def test_every_fact_has_expires_literal_check():
    for f in REAL_FACTS:
        assert f.get("expires") is not None, f["id"]


# ---------------------------------------------------------------------------
# Mutation 1: a fact without `expires` goes red
# ---------------------------------------------------------------------------

def test_mutation_missing_expires_is_red():
    facts = [_base_fact(expires=None)]
    with pytest.raises(fl.FactError, match="expires"):
        cc.validate(facts)


# ---------------------------------------------------------------------------
# Mutation 2: a definite yes/no availability fact without a URL goes red
# ---------------------------------------------------------------------------

def test_mutation_available_fact_without_url_is_red():
    facts = [_base_fact(source_url="")]
    with pytest.raises(fl.FactError, match="source_url"):
        cc.validate(facts)


def test_mutation_available_fact_without_quote_is_red():
    facts = [_base_fact(source_quote="")]
    with pytest.raises(fl.FactError, match="source_quote"):
        cc.validate(facts)


def test_not_stated_fact_needs_neither_url_nor_quote():
    facts = [_base_fact(value="not stated", source_url="", source_quote="")]
    cc.validate(facts)  # must not raise


def test_mutation_invalid_availability_value_is_red():
    facts = [_base_fact(value="probably")]
    with pytest.raises(fl.FactError, match="probably"):
        cc.validate(facts)


# ---------------------------------------------------------------------------
# Availability / reroute logic — against the real measured facts
# ---------------------------------------------------------------------------

def test_availability_covers_all_seven_pages():
    avail = cc.availability_by_jurisdiction(REAL_FACTS)
    for slug, jurisdiction in cc.PAGE_JURISDICTIONS.items():
        assert jurisdiction in avail, f"{slug} ({jurisdiction}) has no availability fact"
        assert avail[jurisdiction]["value"] in cc.VALID_AVAILABILITY


def test_reroute_fires_only_for_india_and_turkey():
    reroutes = cc.reroute_slugs(REAL_FACTS)
    assert set(reroutes) == {"best-crypto-card-india", "best-crypto-card-turkey"}
    for slug in ("best-crypto-card-netherlands", "best-crypto-card-georgia",
                 "best-crypto-card-arizona", "best-crypto-card-nevada",
                 "best-crypto-card-washington"):
        assert slug not in reroutes


def test_mutation_flip_netherlands_to_not_available_adds_reroute():
    facts = copy.deepcopy(REAL_FACTS)
    for f in facts:
        if f["id"] == "cryptocom.availability.netherlands":
            f["value"] = "not available"
    reroutes = cc.reroute_slugs(facts)
    assert "best-crypto-card-netherlands" in reroutes


# ---------------------------------------------------------------------------
# The claim scanner — pattern + brand-mention + current-token backing
# ---------------------------------------------------------------------------

def test_scan_flags_a_stale_ruby_claim_with_no_current_token():
    text = "Crypto.com's Ruby tier ($0 stake) gives 2 % cashback with no lock-in."
    findings = cc.scan_page_claims("test", text, current_tokens=set())
    assert len(findings) == 1
    assert "ruby_tier" in findings[0]["keywords"]


def test_scan_does_not_flag_a_claim_backed_by_a_current_token():
    text = "Crypto.com's Ruby Steel tier gives 2% cashback on everyday spending."
    findings = cc.scan_page_claims("test", text, current_tokens={"2%"})
    assert findings == []


def test_scan_ignores_other_brands_pct_cashback_without_crypto_com_mention():
    text = "RedotPay pays up to 40 % cashback but is slower and riskier."
    findings = cc.scan_page_claims("test", text, current_tokens=set())
    assert findings == []


def test_scan_still_flags_bare_cro_stake_and_dollar4k_without_brand_mention():
    # CRO / $4k are Crypto.com's own vocabulary in this corpus — no brand
    # co-mention required for those two patterns.
    text = "If your $4k Jade stake drops 50%, you may downgrade to Ruby tier."
    findings = cc.scan_page_claims("test", text, current_tokens=set())
    assert len(findings) == 1
    assert "four_k_stake" in findings[0]["keywords"]


# ---------------------------------------------------------------------------
# G45 ruling-1 REJECT: a short fee token ("$4" from "$4.99/month") must not
# substring-match inside a longer stale number ("$400", "$4,000", "$4k") or
# a percentage inside a longer one ("0%"/"5%" inside "50%"/"15%"). Literal
# expected values per Y49 — never re-derived from the module under test.
# ---------------------------------------------------------------------------

def test_mutation_short_dollar_token_does_not_back_a_longer_stale_number():
    text = "Crypto.com's Jade tier (5% cashback at $400 CRO) beats the base tier."
    findings = cc.scan_page_claims("test", text, current_tokens={"$4"})
    assert len(findings) == 1
    assert findings[0]["keywords"] == "jade_tier,pct_cashback"


def test_mutation_short_dollar_token_does_not_back_a_four_k_stake_claim():
    text = "Consider Jade ($4,000 equivalent) for added benefits."
    findings = cc.scan_page_claims("test", text, current_tokens={"$4"})
    assert len(findings) == 1
    assert "four_k_stake" in findings[0]["keywords"]


def test_mutation_short_percent_token_does_not_back_a_longer_percentage():
    text = "Crypto.com's Obsidian tier pays 50% cashback on every purchase."
    findings = cc.scan_page_claims("test", text, current_tokens={"0%", "5%"})
    assert len(findings) == 1


def test_exact_token_still_backs_the_matching_sentence():
    text = "Crypto.com's PLUS tier costs $4.99/month for 3.5% cashback."
    findings = cc.scan_page_claims("test", text, current_tokens={"$4"})
    assert findings == []


# ---------------------------------------------------------------------------
# G45 ruling-2 REJECT #1: washington's own line 48 ("$400 CRO investment
# unlocks 5% cashback...") names no literal "Crypto.com" and was invisible —
# _CRYPTOCOM_MENTION didn't know "CRO" is that brand's own token. Literal
# text copied verbatim from best-crypto-card-washington.md.
# ---------------------------------------------------------------------------

def test_mutation_cro_only_sentence_counts_as_brand_mention():
    text = ("**Key metric:** $400 CRO investment unlocks 5% cashback for "
            "12 months if you maintain the tier.")
    findings = cc.scan_page_claims("test", text, current_tokens=set())
    assert len(findings) == 1
    assert findings[0]["keywords"] == "pct_cashback"


# ---------------------------------------------------------------------------
# G45 ruling-2 REJECT #2: a `\\n` (two literal characters, backslash+n) in
# the splitter regex never matches a real line break, so a bulleted list
# (lines don't end in .!?) merged into one oversized "sentence" and hid
# individual claims behind a 180-char snippet truncation. Fixed to `\n` (an
# actual newline). Two bullet lines, two separate claims -> two findings.
# ---------------------------------------------------------------------------

def test_mutation_missing_newline_split_merges_two_bullet_claims():
    text = ("**Key metrics:**\n"
            "- Crypto.com's Ruby tier gives 2% cashback\n"
            "- Crypto.com's Jade tier gives 5% cashback")
    findings = cc.scan_page_claims("test", text, current_tokens=set())
    assert len(findings) == 2


# ---------------------------------------------------------------------------
# End to end: the real report, over the real 7 pages, pages untouched
# ---------------------------------------------------------------------------

def test_real_report_written_and_pages_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "OUT_DIR", tmp_path)
    before = {slug: (cc.CONTENT / f"{slug}.md").read_text(encoding="utf-8")
              for slug in cc.PAGE_JURISDICTIONS}

    md_path, json_path = cc.write_outputs("2026-09-24")

    for slug, text in before.items():
        assert (cc.CONTENT / f"{slug}.md").read_text(encoding="utf-8") == text, \
            f"{slug} was modified — this task's own scope line forbids it"

    md_text = md_path.read_text(encoding="utf-8")
    assert "REROUTE best-crypto-card-india" in md_text
    assert "REROUTE best-crypto-card-turkey" in md_text
    assert "REROUTE best-crypto-card-netherlands" not in md_text
    assert "unbacked claim(s) found" in md_text

    import json as _json
    data = _json.loads(json_path.read_text(encoding="utf-8"))
    assert data["availability"]["India"] == "not stated"
    assert data["availability"]["Netherlands"] == "available"
    assert set(data["reroute"]) == {"best-crypto-card-india", "best-crypto-card-turkey"}
    assert data["g46_unbacked_claims"] == 0


# ---------------------------------------------------------------------------
# G46 (T-0142-16, ruling-6) — the same law generalized to every brand: a
# facts file backs a claim (cryptocom/etherfi/plasma), no facts file means
# ANY money figure next to that brand's name is unbacked by construction
# (redotpay/bybit/wirex). Literal expected values (Y49): never re-derived
# from the module under test.
# ---------------------------------------------------------------------------

def _brand_facts_stub():
    return {"cryptocom": REAL_FACTS, "etherfi": [], "plasma": []}


def test_g46_redotpay_percent_on_netherlands_is_unbacked():
    text = "RedotPay offers 3% cashback with no lock-in."
    findings = cc.scan_all_brand_claims("test", text, "Netherlands", _brand_facts_stub())
    assert len(findings) == 1
    assert findings[0]["brand"] == "redotpay/bybit/wirex"


def test_g46_backed_cryptocom_claim_on_georgia_is_clean():
    text = "Crypto.com's PLUS tier costs $4.99/month for 3.5% cashback."
    findings = cc.scan_all_brand_claims("test", text, "Georgia", _brand_facts_stub())
    assert findings == []


def test_g46_bybit_dollar_figure_is_unbacked_even_without_redotpay_wording():
    text = "Bybit Card pays up to $200 back on qualifying spend."
    findings = cc.scan_all_brand_claims("test", text, "Arizona", _brand_facts_stub())
    assert len(findings) == 1
    assert findings[0]["brand"] == "redotpay/bybit/wirex"


def test_g46_etherfi_percent_with_no_current_fact_for_jurisdiction_is_unbacked():
    # etherfi.yaml carries no jurisdiction-scoped rate/fee facts at all, so a
    # brand_facts stub with real etherfi facts still can't back a claim
    # scoped to a US state or EU country -- consistent with these 7 pages,
    # every one of which states ether.fi is NOT available there.
    text = "ether.fi Cash pays 3% cashback here too."
    findings = cc.scan_all_brand_claims(
        "test", text, "Washington", {"cryptocom": [], "etherfi": REAL_ETHERFI_FACTS, "plasma": []})
    assert len(findings) == 1
    assert findings[0]["brand"] == "etherfi"


def test_g46_sentence_with_no_money_figure_is_never_flagged():
    text = "RedotPay does not issue cards to US residents."
    findings = cc.scan_all_brand_claims("test", text, "Nevada", _brand_facts_stub())
    assert findings == []


def test_mutation_removing_no_file_brand_stops_flagging_redotpay(monkeypatch):
    """The law this task's own acceptance names: removing the (None, ...)
    entry for redotpay/bybit/wirex from BRAND_PATTERNS makes the RedotPay
    claim above stop being flagged — proving the None-branch, not the
    per-brand backing check, is what catches it."""
    patched = [(brand, rx) for brand, rx in cc.BRAND_PATTERNS if brand is not None]
    monkeypatch.setattr(cc, "BRAND_PATTERNS", patched)
    text = "RedotPay offers 3% cashback with no lock-in."
    findings = cc.scan_all_brand_claims("test", text, "Netherlands", _brand_facts_stub())
    assert findings == []


def test_g46_real_corpus_is_clean_across_all_seven_pages():
    findings = cc.scan_g46_all_pages()
    assert findings == [], findings


def test_g45_real_corpus_is_also_clean_across_all_seven_pages():
    facts = REAL_FACTS
    tokens = cc.current_tokens_by_jurisdiction(facts)
    total = []
    for slug, jurisdiction in cc.PAGE_JURISDICTIONS.items():
        text = (cc.CONTENT / f"{slug}.md").read_text(encoding="utf-8")
        total += cc.scan_page_claims(slug, text, tokens.get(jurisdiction, set()))
    assert total == [], total
