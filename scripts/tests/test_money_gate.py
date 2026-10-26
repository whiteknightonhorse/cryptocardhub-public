"""E-11 / gate G36 — a number in money copy must come from a fact.

Fable's four acceptance literals (FABLE_DEFYCARD_PART2.md, wave 1, E-11) are
pinned verbatim below. Every expected value in this file is a LITERAL: Y49 and
Y54 both cost a round because a gate computed its expectation by calling the
code under test and moved with the mutation.

The wiring tests drive the REAL entry points (`pseo-generate.py`'s main(),
`page-updates.py`'s apply_updates()) and assert on the file on disk — Y53: a
gate that proves a helper can say "no" while the caller never asks it is a
unit test in a gate's costume.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import money_gate as mg  # noqa: E402


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pg = _load("pseo_generate", "scripts/pseo-generate.py")
pu = _load("page_updates", "scripts/page-updates.py")


# ---------------------------------------------------------------------------
# The four acceptance literals
# ---------------------------------------------------------------------------

def test_business_days_promise_is_refused():
    """«within 1–2 business days» — the payout cadence is `disputed` in the
    fact model, so neither number has anything behind it."""
    assert mg.unbacked("Referral bonuses are paid within 1–2 business days.") == \
        ["1 days", "2 days"]


def test_core_first_band_passes():
    """«3 % on the first $2,000» — both numbers are in rates.yaml's `levels`."""
    assert mg.unbacked("You earn 3 % on the first $2,000 of spend each month.") == []


def test_twenty_to_twentyfive_percent_passes():
    """«20–25 %» — 20 % is the tier-1..3 commission, 25 % is tier 4."""
    assert mg.unbacked("Physical card commission runs 20–25 % by tier.") == []


def test_ten_to_twentyfive_percent_is_refused():
    """«10–25 %» — the old unresolved range. 10 % is backed by nothing."""
    assert mg.unbacked("Physical card commission runs 10–25 % by tier.") == ["10%"]


# ---------------------------------------------------------------------------
# What may NOT back a number
# ---------------------------------------------------------------------------

def test_an_expired_promo_in_rates_yaml_backs_nothing():
    """`promos:` carries the dead "15%" (and its "10% for organic sign-ups").
    Reading the whole rates file instead of `levels` would re-open Y63."""
    assert mg.unbacked("Dining and groceries earn up to 15% back.") == ["15%"]


def test_a_disputed_fact_backs_nothing():
    """The daily-spend caps are `disputed` — tracked, never published, and
    never a licence to print the number either."""
    assert mg.unbacked("Core cardholders can spend $30,000 a day.") == ["$30000"]


def test_the_radius_refuses_another_tiers_number():
    """$50 is a real, current fee — for Core. Next to "VIP" it is a wrong number
    that a value-only check would wave through. (Was $100 next to Core until
    2026-09-29, when the $100 qualifying-referral spend became a tier-less fact.)"""
    assert mg.unbacked("The VIP physical card costs $50.") == ["$50"]


def test_the_radius_passes_the_tier_the_fact_belongs_to():
    assert mg.unbacked("The Luxe physical card costs $100.") == []
    assert mg.unbacked("The Core physical card costs $50.") == []


def test_the_parent_page_backs_its_own_numbers():
    """page-updates strengthens a page that exists; a number already printed
    there is a source. A brand-new page has no such branch."""
    section = "Cashback on this card is 7.5% for the top tier."
    assert mg.unbacked(section) == ["7.5%"]
    assert mg.unbacked(section, parent_text="The top tier pays 7.5% back.") == []


def test_json_escaped_dashes_do_not_invent_a_number():
    """A page file's frontmatter is JSON: "2\\u20133 days" must read as 2 and
    3, never as 20133 (measured: 24 of 60 sampled corpus pages)."""
    assert 20133.0 not in {v for v, _u, _s, _e in mg.tokens('"2\\u20133 days"')}


# ---------------------------------------------------------------------------
# Wiring 1 — pseo-generate.py: refused BEFORE the file exists
# ---------------------------------------------------------------------------

_UNBACKED_BODY = (
    "## Payouts\n\nReferral bonuses are paid within 1–2 business days.\n\n"
    "<<AFFILIATE_BUTTON>>\n"
)
_PAYLOAD = {
    "title": "Referral payouts",
    "meta_description": "How referral payouts work.",
    "h1": "Referral payouts",
    "intro_paragraph": "Payouts land fast. Read on for the detail.",
    "snapshot_bullets": ["Fast payouts"],
    "long_context_paragraph": "Some context.",
    "what_to_watch": ["Payout timing"],
    "bottom_line": ["Refer friends"],
    "faq": [{"q": "When do I get paid?", "a": "Soon."}],
    "related_clusters": [],
    "body_markdown": _UNBACKED_BODY,
    "schema_article": {},
    "schema_faq": {},
}


def _one_cluster():
    row = pg.KeywordRow(code="Z1", bucket="F", bucket_name="How-to",
                        keyword="money gate wiring probe")
    support = [pg.KeywordRow(code="Z2", bucket="F", bucket_name="How-to", keyword="probe two"),
               pg.KeywordRow(code="Z3", bucket="F", bucket_name="How-to", keyword="probe three")]
    return pg.Cluster(primary=row, supporting=support, pattern="P-HowTo", url_segment="how-to")


def _wire(monkeypatch, tmp_path, payload):
    content = tmp_path / "content"
    content.mkdir()
    monkeypatch.setattr(pg, "CONTENT_DIR", content)
    monkeypatch.setattr(pg, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(pg, "FAQ_LEDGER", tmp_path / "faq-cohorts.json")  # bank-refused.json derives from it
    monkeypatch.setattr(pg, "build_queue", lambda *a, **k: [_one_cluster()])
    monkeypatch.setattr(pg, "call_claude_cli",
                        lambda **k: (json.dumps(payload), {"output_tokens": 1}, 0.0))
    monkeypatch.setattr(pg, "fetch_images_for_article", lambda p: [])
    monkeypatch.setattr(pg.telegram_lib, "alert", lambda *a, **k: True)
    monkeypatch.setattr(pg.no_progress_guard, "check", lambda *a, **k: False)
    monkeypatch.delenv("PSEO_ENABLED", raising=False)
    return content


def test_pseo_generate_writes_no_file_when_a_number_is_unbacked(monkeypatch, tmp_path, capsys):
    content = _wire(monkeypatch, tmp_path, _PAYLOAD)
    rc = pg.main(["--limit", "1"])
    out = capsys.readouterr().out
    assert list(content.glob("*.md")) == [], "a refused page was written to disk"
    assert '"gate": "G36"' in out
    assert "unbacked_number:1 days" in out
    assert rc == 1


def test_pseo_generate_writes_the_page_when_every_number_is_backed(monkeypatch, tmp_path):
    """Control: the same wiring, one number changed to a verified one. Without
    it, "no file" proves nothing — the harness might be writing nothing at all.
    """
    payload = dict(_PAYLOAD)
    payload["body_markdown"] = (
        "## Payouts\n\nCashback unlocks after 7 days.\n\n<<AFFILIATE_BUTTON>>\n")
    content = _wire(monkeypatch, tmp_path, payload)
    rc = pg.main(["--limit", "1"])
    assert [p.name for p in content.glob("*.md")] == ["money-gate-wiring-probe.md"]
    assert rc == 0


# ---------------------------------------------------------------------------
# Wiring 2 — page-updates.py: refused BEFORE the page is rewritten
# ---------------------------------------------------------------------------

_FM = ('---\ntitle: "Probe"\nslug: "probe-page"\n'
       'generated_at: "2026-05-08T22:02:19.436485+00:00"\n---\n')
_BODY = "## Probe\n\nThis page already exists and says nothing about money.\n\n## FAQ\n\nQ.\n"
_BAD_SECTION = (
    "### When Do Referral Bonuses Arrive?\n\n"
    "Referral bonuses are paid within 1–2 business days, straight to your wallet. "
    "Commission runs 10–25 % depending on the tier you reach this quarter."
)


def test_page_updates_refuses_and_leaves_the_page_byte_for_byte(tmp_path, monkeypatch):
    content = tmp_path / "content"
    content.mkdir()
    page = content / "probe-page.md"
    page.write_text(_FM + _BODY, encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"

    monkeypatch.setattr(pu.pg, "CONTENT_DIR", content)
    monkeypatch.setattr(pu.pg, "FROZEN_SLUGS", frozenset())
    monkeypatch.setattr(pu, "LEDGER", ledger)
    monkeypatch.setattr(pu.telegram_lib, "alert", lambda *a, **k: True)
    monkeypatch.setattr(pu.pc, "parse_update_candidates", lambda path=None: [
        {"query": "when do referral bonuses arrive", "impressions": 90, "clicks": 1,
         "path": "/help/probe-page/", "page_impressions": 80, "page_clicks": 0,
         "position": 7.0},
    ])

    result = pu.apply_updates(dry_run=False, writer=lambda q, body: _BAD_SECTION,
                              now=datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert result["applied"] == 0
    assert page.read_text(encoding="utf-8") == _FM + _BODY
    row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert row["event"] == "refused"
    assert row["reason"] == "unbacked_number:1 days"
    assert row["gate"] == "G36"


# ---------------------------------------------------------------------------
# CCH-KW-04: facts are per brand, read from every config/facts/*.yaml
# ---------------------------------------------------------------------------

_GEMINI_4 = [{
    "id": "t.gemini.4", "value": 4, "unit": "%", "status": "current",
    "applies_to": {"brand": "gemini", "product": "credit"},
}]


def test_a_gemini_fact_does_not_back_a_coinbase_sentence():
    """The brand named in the RADIUS window is a different member of the `brand`
    dimension — the Gemini 4 % must not license "Coinbase pays 4 % back"."""
    # 7 % appears nowhere else; Coinbase is a member only through a DISPUTED fact.
    facts = [dict(_GEMINI_4[0], value=7),
             {"id": "t.cb", "value": "n/a", "unit": None, "status": "disputed",
              "applies_to": {"brand": "coinbase", "product": "one"}}]
    assert mg.unbacked("Coinbase pays 7 % back on gas.", facts=facts) == ["7%"]
    assert mg.unbacked("Gemini pays 7 % back on gas.", facts=facts) == []


def test_gate_reads_every_facts_file_not_only_etherfi():
    """Literals: the Gemini 300 USD monthly cap lives only in config/facts/gemini.yaml.
    Mutation: fact_rows() back on facts_lib.load() -> this goes red."""
    assert mg.unbacked("Gemini's 4% category is capped at $300 of spend a month.") == []
    assert "$300" in mg.allowed_tokens()
    assert mg.unbacked("Coinbase One Card caps rewards at $300 a month.") == ["$300"]


def _pg():
    import importlib.util, sys
    from pathlib import Path
    s = importlib.util.spec_from_file_location("pg_kw04", Path(__file__).parents[1] / "pseo-generate.py")
    m = importlib.util.module_from_spec(s); sys.modules["pg_kw04"] = m; s.loader.exec_module(m)
    return m


def test_writer_prompt_carries_gemini_facts_literal():
    p = _pg().build_cached_system_prompt()
    assert "Gas, EV & Transit 4%" in p and "WebBank" in p


def _coinbase_queue(monkeypatch, facts):
    pg = _pg()
    monkeypatch.setattr(pg.facts_lib, "load_all", lambda: facts)
    clusters = pg.build_clusters(pg.parse_keyword_bank(pg.SKILL_KEYWORDS))
    return [pg.slugify(c.primary.keyword) for c in pg.build_queue(clusters, False, False)]


def test_coinbase_one_card_queued_only_with_a_current_product_one_fact(monkeypatch):
    assert "coinbase-one-card" not in _coinbase_queue(monkeypatch, [])
    fact = {"status": "current", "applies_to": {"brand": "coinbase", "product": "one"}}
    assert "coinbase-one-card" in _coinbase_queue(monkeypatch, [fact])
