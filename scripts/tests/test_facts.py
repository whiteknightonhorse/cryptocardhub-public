"""E-07a — the fact model: schema, publishable render, marker idempotency.

`disputed`/`expired` facts exist to be tracked, never to be printed — the
writer only ever sees `current` and `beta`. A `promo`-class or `beta`-status
row without `expires` is a defect that can never be caught later on its own
(Y63: this is exactly how "up to 15% on food" survived four months past its
own end date) — load must refuse it outright, not warn.

Every expected value below is a LITERAL (Y49/Y54: a gate that computes its
expectation by calling the code under test moves with the mutation and stays
green).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS))
import facts_lib as fl  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("facts_render", SCRIPTS / "facts-render.py")
fr = importlib.util.module_from_spec(_SPEC)
sys.modules["facts_render"] = fr
_SPEC.loader.exec_module(fr)  # type: ignore[union-attr]


def _base_fact(**overrides) -> dict:
    fact = {
        "id": "etherfi.test.x",
        "value": 1,
        "unit": "%",
        "applies_to": {},
        "source_url": "https://example.com",
        "source_quote": "quote",
        "verified_at": "2026-09-14",
        "expires": None,
        "status": "current",
        "class": "rate",
        "confidence": "issuer_help",
    }
    fact.update(overrides)
    return fact


def _write_facts(tmp_path: Path, facts: list[dict]) -> Path:
    p = tmp_path / "etherfi.yaml"
    p.write_text(yaml.safe_dump({"facts": facts}), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

def test_the_real_facts_file_loads_and_validates():
    facts = fl.load()
    assert len(facts) == 40
    ids = [f["id"] for f in facts]
    assert len(ids) == len(set(ids)), "duplicate ids"


def test_a_promo_class_fact_without_expires_fails_to_load(tmp_path):
    fact = _base_fact(id="etherfi.test.promo")
    fact["class"] = "promo"
    fact["expires"] = None
    path = _write_facts(tmp_path, [fact])
    with pytest.raises(fl.FactError, match="expires"):
        fl.load(path)


def test_a_beta_status_fact_without_expires_fails_to_load(tmp_path):
    facts = [_base_fact(id="etherfi.test.beta", status="beta", expires=None)]
    path = _write_facts(tmp_path, facts)
    with pytest.raises(fl.FactError, match="expires"):
        fl.load(path)


def test_a_beta_status_fact_with_expires_loads_fine(tmp_path):
    facts = [_base_fact(id="etherfi.test.beta", status="beta", expires="2026-12-13")]
    path = _write_facts(tmp_path, facts)
    assert fl.load(path)[0]["status"] == "beta"


def test_an_unknown_status_is_rejected(tmp_path):
    facts = [_base_fact(status="not_a_real_status")]
    path = _write_facts(tmp_path, facts)
    with pytest.raises(fl.FactError, match="status"):
        fl.load(path)


def test_a_missing_required_field_is_rejected(tmp_path):
    fact = _base_fact()
    del fact["source_url"]
    path = _write_facts(tmp_path, [fact])
    with pytest.raises(fl.FactError, match="missing required field"):
        fl.load(path)


def test_a_duplicate_id_is_rejected(tmp_path):
    path = _write_facts(tmp_path, [_base_fact(), _base_fact()])
    with pytest.raises(fl.FactError, match="duplicate"):
        fl.load(path)


def test_a_quote_over_200_chars_is_rejected(tmp_path):
    facts = [_base_fact(source_quote="x" * 201)]
    path = _write_facts(tmp_path, facts)
    with pytest.raises(fl.FactError, match="200"):
        fl.load(path)


# ---------------------------------------------------------------------------
# Render — publishable-only, banned strings, idempotency
# ---------------------------------------------------------------------------

def test_disputed_and_expired_facts_never_reach_the_render(tmp_path):
    facts = [
        _base_fact(id="etherfi.test.current", status="current"),
        _base_fact(id="etherfi.test.disputed", status="disputed"),
        _base_fact(id="etherfi.test.expired", status="expired"),
    ]
    rendered = fl.render(facts)
    assert "etherfi.test.current" in rendered
    assert "etherfi.test.disputed" not in rendered
    assert "etherfi.test.expired" not in rendered


def test_beta_facts_render_with_a_visible_beta_tag(tmp_path):
    facts = [_base_fact(id="etherfi.test.beta", status="beta", expires="2026-12-13")]
    rendered = fl.render(facts)
    assert "etherfi.test.beta" in rendered
    assert "(beta)" in rendered


def test_the_real_render_carries_none_of_the_banned_payout_strings():
    facts = fl.load()
    rendered = fl.render(facts)
    for banned in ("SCR", "rolling", "10 %–25 %", "10%-25%", "10-25%"):
        assert banned not in rendered, f"banned string leaked into render: {banned!r}"


def test_render_of_the_real_facts_plus_the_skill_rules_fits_the_7000_char_budget():
    facts = fl.load()
    rendered = fl.render(facts)
    skill_text = fr.SKILL_PATH.read_text(encoding="utf-8")
    head = fr._rules_head(skill_text)
    assert len(head) + len(rendered) <= 7000


def test_render_is_deterministic_across_calls():
    facts = fl.load()
    assert fl.render(facts) == fl.render(facts)


# ---------------------------------------------------------------------------
# The LIVE SKILL.md, on disk — the artifact the writer actually reads.
# Y52/Y53's lesson restated: an artifact is verified where it leaves for its
# consumer, not by a helper proving it *could* build the right string.
# ---------------------------------------------------------------------------

def test_the_live_skill_file_carries_the_markers_and_is_in_sync():
    facts = fl.load()
    rendered = fl.render(facts)
    skill_text = fr.SKILL_PATH.read_text(encoding="utf-8")
    assert fl.extract_marker_block(skill_text) is not None, (
        "SKILL.md has no facts:begin/end block — run `facts-render.py` (no flag)"
    )
    assert fl.is_in_sync(skill_text, rendered), (
        "SKILL.md's marker block has drifted from config/facts/etherfi.yaml's render"
    )


def test_the_live_skill_file_head_carries_none_of_the_corrected_money_claims():
    skill_text = fr.SKILL_PATH.read_text(encoding="utf-8")
    head = skill_text[: fr.HEAD_BUDGET]
    for banned in fr._PROSE_BANNED:
        assert banned not in head, f"stale/false claim still in SKILL.md[:7000]: {banned!r}"


def test_check_cli_fails_when_the_live_markers_go_missing(tmp_path, monkeypatch):
    real_text = fr.SKILL_PATH.read_text(encoding="utf-8")
    block = fl.extract_marker_block(real_text)
    assert block is not None
    stripped = real_text.replace(block, "", 1)
    fake_skill = tmp_path / "SKILL.md"
    fake_skill.write_text(stripped, encoding="utf-8")
    monkeypatch.setattr(fr, "SKILL_PATH", fake_skill)
    rc = fr.check(fl.DEFAULT_FACTS_PATH)
    assert rc == 1


def test_check_cli_fails_when_the_live_prose_reverts_to_a_banned_claim(tmp_path, monkeypatch):
    real_text = fr.SKILL_PATH.read_text(encoding="utf-8")
    # Must land inside SKILL.md[:7000]; the §1.2 row moved past that byte when the
    # affiliate facts grew the render (2026-09-29).
    reverted = real_text.replace(
        "Never call the physical-card fee refundable.", "10%-25% Never call the physical-card fee refundable.", 1
    )
    assert reverted != real_text, "fixture assumes the corrected phrase is still present"
    fake_skill = tmp_path / "SKILL.md"
    fake_skill.write_text(reverted, encoding="utf-8")
    monkeypatch.setattr(fr, "SKILL_PATH", fake_skill)
    rc = fr.check(fl.DEFAULT_FACTS_PATH)
    assert rc == 1


def test_check_cli_fails_when_banned_prose_reverts_beyond_the_writer_window(tmp_path, monkeypatch):
    real_text = fr.SKILL_PATH.read_text(encoding="utf-8")
    # A revert of the "Reward duration: up to 12 months" row lands past SKILL.md[:7000];
    # --check must still see it (the whole file is scanned for prose literals).
    reverted = real_text + "\n| Reward duration | **up to 12 months** |\n"
    assert reverted.index("**up to 12 months**") > fr.HEAD_BUDGET
    fake_skill = tmp_path / "SKILL.md"
    fake_skill.write_text(reverted, encoding="utf-8")
    monkeypatch.setattr(fr, "SKILL_PATH", fake_skill)
    assert fr.check(fl.DEFAULT_FACTS_PATH) == 1


# ---------------------------------------------------------------------------
# Marker sync / idempotency — a hand-edit inside the markers must be caught
# ---------------------------------------------------------------------------

def test_missing_markers_are_not_in_sync():
    assert fl.is_in_sync("no markers here at all", "<!-- facts:begin -->x<!-- facts:end -->") is False


def test_identical_marker_block_is_in_sync():
    rendered = "<!-- facts:begin -->\nsame\n<!-- facts:end -->"
    skill_text = f"before\n{rendered}\nafter"
    assert fl.is_in_sync(skill_text, rendered) is True


def test_a_hand_edit_inside_the_markers_breaks_sync():
    rendered = "<!-- facts:begin -->\ngenerated\n<!-- facts:end -->"
    hand_edited = "<!-- facts:begin -->\nsomeone typed something else\n<!-- facts:end -->"
    skill_text = f"before\n{hand_edited}\nafter"
    assert fl.is_in_sync(skill_text, rendered) is False


def test_apply_markers_inserts_after_the_anchor_when_none_exist():
    rendered = "<!-- facts:begin -->\nnew\n<!-- facts:end -->"
    skill_text = "intro\nANCHOR\ntail"
    out = fl.apply_markers(skill_text, rendered, "ANCHOR")
    assert rendered in out
    assert out.index("ANCHOR") < out.index(rendered)
    assert "tail" in out


def test_apply_markers_replaces_an_existing_block_in_place():
    old_block = "<!-- facts:begin -->\nold\n<!-- facts:end -->"
    new_block = "<!-- facts:begin -->\nnew\n<!-- facts:end -->"
    skill_text = f"intro\n{old_block}\ntail"
    out = fl.apply_markers(skill_text, new_block, "intro")
    assert new_block in out
    assert "old" not in out
    assert out.count("<!-- facts:begin -->") == 1


def test_apply_markers_raises_if_the_anchor_is_gone():
    with pytest.raises(fl.FactError, match="anchor"):
        fl.apply_markers("no anchor here", "<!-- facts:begin -->x<!-- facts:end -->", "MISSING")


# ---------------------------------------------------------------------------
# CLI (`facts-render.py --check`)
# ---------------------------------------------------------------------------

def test_check_cli_exits_zero_on_the_real_facts_file(capsys):
    rc = fr.check(fl.DEFAULT_FACTS_PATH)
    out = capsys.readouterr().out
    assert rc == 0
    assert "FAIL" not in out


def test_check_cli_exits_nonzero_on_a_broken_facts_file(tmp_path, capsys):
    facts = [_base_fact(status="beta", expires=None)]
    path = _write_facts(tmp_path, facts)
    rc = fr.check(path)
    assert rc == 1


# ---------------------------------------------------------------------------
# Disputed-value leak into prose (ruling-3, T-w1-07a-skill): a `disputed`
# fact's own number must never read as settled fact anywhere in SKILL.md,
# even outside the first 7000 chars — and the gate must derive this from the
# fact record itself, not a list someone remembered to update.
# ---------------------------------------------------------------------------

def test_disputed_numeric_tokens_only_covers_disputed_numeric_facts():
    facts = [
        _base_fact(id="etherfi.test.current", status="current", value=30000),
        _base_fact(id="etherfi.test.disputed_num", status="disputed", value=30000),
        _base_fact(id="etherfi.test.disputed_str", status="disputed", value="disputed: A vs B"),
    ]
    tokens = fl.disputed_numeric_tokens(facts)
    assert tokens == ["30,000"]


def test_disputed_numeric_tokens_disambiguates_a_rate_unit_with_the_suffix():
    # 50,000 is coincidentally also a legitimate, unrelated `current`
    # cashback-band boundary elsewhere in the real SKILL.md — a bare-number
    # ban would false-positive on that unrelated fact. A "/day" rate unit
    # must produce "50,000/day", not the ambiguous bare "50,000".
    facts = [_base_fact(id="etherfi.test.disputed_rate", status="disputed",
                         value=50000, unit="USD/day")]
    assert fl.disputed_numeric_tokens(facts) == ["50,000/day"]


def test_stale_numeric_tokens_covers_both_disputed_and_expired(tmp_path):
    """E-20's G35 (video H1 mode) must refuse EITHER kind of unsettled claim,
    not just disputed — `disputed_numeric_tokens` alone is the narrower,
    pre-existing SKILL.md check and must stay narrow (regression below)."""
    facts = [
        _base_fact(id="etherfi.test.current", status="current", value=3),
        _base_fact(id="etherfi.test.disputed", status="disputed", value=30000),
        _base_fact(id="etherfi.test.expired", status="expired", value=15,
                   expires="2026-02-01"),
    ]
    assert sorted(fl.stale_numeric_tokens(facts)) == ["15", "30,000"]


def test_disputed_numeric_tokens_still_excludes_expired_facts():
    """Regression: generalizing the shared helper must not widen the
    narrower, already-tested function."""
    facts = [_base_fact(id="etherfi.test.expired", status="expired", value=15,
                        expires="2026-02-01")]
    assert fl.disputed_numeric_tokens(facts) == []


def test_prose_leaks_disputed_values_does_not_false_positive_on_an_unrelated_current_fact():
    facts = [_base_fact(id="etherfi.test.disputed_rate", status="disputed",
                         value=50000, unit="USD/day")]
    # Same digits, different unit/context, no "/day" — must NOT be flagged.
    skill_text = "Pinnacle band: first $50,000 / €16,000, verified current fact."
    assert fl.prose_leaks_disputed_values(skill_text, facts) == []


def test_prose_leaks_disputed_values_ignores_the_generated_block():
    facts = [_base_fact(id="etherfi.test.disputed_num", status="disputed", value=30000)]
    rendered = fl.render(facts)  # disputed never renders, block is clean
    skill_text = f"prose before\n{rendered}\nprose after, no numbers here"
    assert fl.prose_leaks_disputed_values(skill_text, facts) == []


def test_prose_leaks_disputed_values_catches_the_number_outside_the_block():
    facts = [_base_fact(id="etherfi.test.disputed_num", status="disputed", value=30000)]
    skill_text = "the daily limit is $30,000/day, trust me"
    assert fl.prose_leaks_disputed_values(skill_text, facts) == ["30,000"]


def test_the_real_skill_file_leaks_no_disputed_daily_limit_number():
    facts = fl.load()
    skill_text = fr.SKILL_PATH.read_text(encoding="utf-8")
    leaks = fl.prose_leaks_disputed_values(skill_text, facts)
    assert leaks == [], f"disputed fact value(s) still asserted as fact in prose: {leaks}"


def test_check_cli_fails_when_a_disputed_daily_limit_number_reappears(tmp_path, monkeypatch):
    real_text = fr.SKILL_PATH.read_text(encoding="utf-8")
    reverted = real_text.replace(
        "| Daily spend limit — Core | **disputed this run** — not independently "
        "re-verified against `articles/330661` (see `etherfi.limit.daily_spend.core`); "
        "do not publish a number |",
        "| Daily spend limit — Core | **$30,000/day** |",
        1,
    )
    assert reverted != real_text, "fixture assumes the disputed-row phrasing is still present"
    fake_skill = tmp_path / "SKILL.md"
    fake_skill.write_text(reverted, encoding="utf-8")
    monkeypatch.setattr(fr, "SKILL_PATH", fake_skill)
    rc = fr.check(fl.DEFAULT_FACTS_PATH)
    assert rc == 1


def test_load_all_reads_every_brand_file_and_ids_are_unique():
    ids = [f["id"] for f in fl.load_all()]
    assert len(ids) == len(set(ids))
    assert sum(i.startswith("gemini.credit.") for i in ids) == 9
    assert {"etherfi", "gemini", "coinbase"} <= {i.split(".")[0] for i in ids}


def test_load_all_refuses_a_duplicate_id_across_files(tmp_path):
    rec = {"id": "x.dup", "value": 1, "unit": "%", "applies_to": {}, "source_url": "u",
           "source_quote": "q", "verified_at": "2026-10-19", "expires": None,
           "status": "current", "class": "rate", "confidence": "issuer_help"}
    for n in ("a", "b"):
        (tmp_path / f"{n}.yaml").write_text(yaml.safe_dump({"facts": [rec]}))
    with pytest.raises(fl.FactError, match="duplicate id"):
        fl.load_all(tmp_path)


def test_coinbase_facts_are_all_disputed_until_a_page_is_read():
    """Cloudflare 403 on every coinbase.com URL (2026-10-19): nothing may be `current`."""
    cb = fl.load(ROOT / "config" / "facts" / "coinbase.yaml")
    assert cb and all(f["status"] == "disputed" for f in cb)
