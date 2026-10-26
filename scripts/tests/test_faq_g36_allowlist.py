"""CCH-FAQ-G36-0929 — one money allow-list, gate-aware ledger, attempt cap,
verdict denominator, selector premise filter. Every expected value is a LITERAL
(Y49/Y54): none is computed by the code under test."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import money_gate as mg  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pg = _load("pseo_generate", "scripts/pseo-generate.py")
fc = _load("faq_children", "scripts/faq-children.py")
fv = _load("faq_verdict", "scripts/faq-verdict.py")


# --- edit 1: one allow-list -------------------------------------------------

def _writer_safe_section() -> str:
    """Everything the writer is told it MAY say about affiliate money: from the
    affiliate block to the next section. Backtick spans are the BANNED strings
    (quoted so the writer can recognise them) and are not claims."""
    p = pg.build_cached_system_prompt()
    sec = p[p.index("## Affiliate-program facts"):p.index("## Competitor & marketing context")]
    return re.sub(r"`[^`]*`", "", sec)


def test_full_prompt_has_no_unbacked_money_token():
    """The ruling's gate on the WHOLE rendered prompt. Only the one carve-out: the
    BANNED strings quoted in backticks inside AFFILIATE_FACTS_BLOCK (`10-25 %`,
    `within 1-2 business days`) are named so the writer avoids them, not claims."""
    p = pg.build_cached_system_prompt()
    blk = pg.AFFILIATE_FACTS_BLOCK
    assert blk in p
    p = p.replace(blk, re.sub(r"`[^`]*`", "", blk))
    assert mg.unbacked(p) == []


def test_skill_excerpts_are_redacted_not_dropped():
    p = pg.build_cached_system_prompt()
    assert "[see allow-list]" in p
    assert "## EtherFI facts" in p


def test_every_money_token_the_writer_is_told_is_safe_passes_the_gate():
    sec = _writer_safe_section()
    assert "Money numbers you may print" in sec
    assert mg.unbacked(sec) == []


def test_the_prompt_prints_the_gates_own_list():
    sec = _writer_safe_section()
    for tok in ("$100", "20%", "25%", "7 days", "$5", "$10", "$15", "90 days", "30 days"):
        assert tok in sec
    listed = sec.split("\n")[-3].split(", ")   # the allow-list line, token-exact
    # ruling-3 (CCH-FAQ-G36-0929, evidence taskloop/briefs/CCH-FAQ-G36-0929/): the Affiliate
    # Program rates 0.1/0.2/0.25/0.3 % are current facts; only referral-T&C figures stay out.
    for tok in ("0.1%", "0.2%", "0.25%", "0.3%"):
        assert tok in listed
    for tok in ("12 months", "$1000"):
        assert tok not in listed


def test_allowed_tokens_literals():
    got = mg.allowed_tokens()
    assert {"$100", "$50", "20%", "25%", "7 days"} <= set(got)
    assert {"$10", "$15", "$20", "$25", "90 days", "30 days"} <= set(got)
    # ruling-3 (CCH-FAQ-G36-0929, evidence taskloop/briefs/CCH-FAQ-G36-0929/): the Affiliate
    # Program rates 0.1/0.2/0.25/0.3 % are current facts; only referral-T&C figures stay out.
    assert {"0.1%", "0.2%", "0.25%", "0.3%"} <= set(got)
    assert not {"12 months", "$1000", "15%"} & set(got)


# --- affiliate-facts task (ruling-2, Task A) ----------------------------------

def test_affiliate_fact_tokens_are_allowed_and_disputed_ones_are_not():
    got = set(mg.allowed_tokens())
    assert {"$10", "$15", "$20", "$25", "20%", "$100", "90 days", "30 days"} <= got
    # ruling-3 (CCH-FAQ-G36-0929, evidence taskloop/briefs/CCH-FAQ-G36-0929/): the Affiliate
    # Program rates 0.1/0.2/0.25/0.3 % are current facts; only referral-T&C figures stay out.
    assert {"0.1%", "0.2%", "0.25%", "0.3%"} <= got
    assert not {"12 months", "$1000"} & got
    assert mg.unbacked("referrers earn 1 % for up to 12 months") == ["12 months"]
    assert mg.unbacked("the maximum referral reward is $1,000 per referred user") == ["$1000"]


def test_referral_cashback_is_tier_scoped():
    assert mg.unbacked("Tier 4 affiliates earn 0.3 % cashback on referral spending") == []
    assert mg.unbacked("Tier 1 affiliates earn 0.3 % cashback on referral spending") == ["0.3%"]


def test_second_degree_revshare_is_tier_scoped():
    assert mg.unbacked("Tier 4 affiliates earn 20 % second-degree revshare") == []
    # ruling-2 expected ["20%"] for Tier 1, but Tier 1 physical-card commission
    # is 20 % too, so 20 % is legitimately backed there; the radius is proven
    # with 25 %, which only Tier 4 has.
    assert mg.unbacked("Tier 1 affiliates earn 25 % back") == ["25%"]


def test_section_1_2_excerpt_keeps_the_ten_dollar_bonus():
    row = "| New Member Bonus | **$10–$20** (depends on tier) |"
    assert pg._redact_unbacked(row) == row
    assert "[see allow-list]" in pg._redact_unbacked("Fee $9999 applies")


# --- edit 2: ledger records the gate; selector honours it -------------------

def _ledger(tmp_path, monkeypatch, items_by_cohort):
    led = tmp_path / "faq-cohorts.json"
    led.write_text(json.dumps({"cohorts": [
        {"opened": "2026-09-01", "items": items} for items in items_by_cohort]}))
    monkeypatch.setattr(pg, "FAQ_LEDGER", led)
    monkeypatch.setattr(fc, "LEDGER", led)
    return led


def _item(slug, refused=None):
    it = {"question": slug + "?", "parent": "/p/", "parent_fetches": 1, "slug": slug}
    if refused:
        it["refused"] = refused
    return it


def test_mark_faq_refused_stores_gate_and_sha_and_only_in_its_own_cohort(tmp_path, monkeypatch):
    led = _ledger(tmp_path, monkeypatch, [[_item("q1")], [_item("q1")]])
    pg.mark_faq_refused("q1", {"reasons": ["unbacked_number:0.3%"]}, 1, "G36", "abc123")
    c = json.loads(led.read_text())["cohorts"]
    assert "refused" not in c[0]["items"][0]
    assert c[1]["items"][0]["refused"]["gate"] == "G36"
    assert c[1]["items"][0]["refused"]["writer_sha"] == "abc123"


def _select(tmp_path, monkeypatch, refused, sha):
    _ledger(tmp_path, monkeypatch, [[_item("what-is-x", refused)]])
    monkeypatch.setattr(fc, "latest_targets", lambda: {"hot": [{"path": "/p/", "fetches": 1}]})
    monkeypatch.setattr(fc, "questions_of", lambda path: ["What is the ether.fi thing X?"])
    monkeypatch.setattr(fc, "slugify", lambda q: "what-is-x")
    monkeypatch.setattr(fc, "CONTENT", tmp_path / "nocontent")
    return [r["slug"] for r in fc.select(5, sha)]


def test_delta_refusal_is_closed_for_good(tmp_path, monkeypatch):
    r = {"gate": "delta", "writer_sha": "old", "reasons": ["delta: only 2 new facts"]}
    assert _select(tmp_path, monkeypatch, r, "new") == []


def test_g36_refusal_is_not_retried_on_the_same_writer(tmp_path, monkeypatch):
    r = {"gate": "G36", "writer_sha": "same", "reasons": ["unbacked_number:0.3%"]}
    assert _select(tmp_path, monkeypatch, r, "same") == []


def test_g36_refusal_is_retried_once_the_writer_changed(tmp_path, monkeypatch):
    r = {"gate": "G36", "writer_sha": "old", "reasons": ["unbacked_number:0.3%"]}
    assert _select(tmp_path, monkeypatch, r, "new") == ["what-is-x"]


def test_legacy_g36_row_without_gate_is_retried_only_on_a_known_writer(tmp_path, monkeypatch):
    r = {"reasons": ["unbacked_number:0.3%"]}
    assert _select(tmp_path, monkeypatch, r, "new") == ["what-is-x"]
    assert _select(tmp_path, monkeypatch, r, None) == []


# --- edit 3: attempt cap + G36 breaker, real main() -------------------------

_UNBACKED = {
    "title": "T", "meta_description": "m", "h1": "h", "intro_paragraph": "i",
    "snapshot_bullets": ["b"], "long_context_paragraph": "c", "what_to_watch": ["w"],
    "bottom_line": ["b"], "faq": [{"q": "q?", "a": "a"}], "related_clusters": [],
    "body_markdown": "Paid within 1–2 business days.\n\n<<AFFILIATE_BUTTON>>\n",
    "schema_article": {}, "schema_faq": {},
}


def test_fourth_run_after_three_g36_refusals_never_reaches_the_writer(monkeypatch, tmp_path, capsys):
    items = [_item(f"q-number-{i}") for i in range(6)]
    for i, it in enumerate(items):
        it["question"] = f"Question number {'abcdef'[i]} about referral programs?"
    _ledger(tmp_path, monkeypatch, [items])
    content = tmp_path / "content"
    content.mkdir()
    calls = {"n": 0}

    def writer(**k):
        calls["n"] += 1
        return json.dumps(_UNBACKED), {"output_tokens": 1}, 0.0

    monkeypatch.setattr(pg, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(pg, "CONTENT_DIR", content)
    monkeypatch.setattr(pg, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(pg, "call_claude_cli", writer)
    monkeypatch.setattr(pg, "faq_delta_gate", lambda r: {"passed": True})
    monkeypatch.setattr(pg, "fetch_images_for_article", lambda p: [])
    monkeypatch.setattr(pg.telegram_lib, "alert", lambda *a, **k: True)
    monkeypatch.setattr(pg.no_progress_guard, "check", lambda *a, **k: False)
    monkeypatch.delenv("PSEO_ENABLED", raising=False)

    for _ in range(3):
        pg.main(["--faq-children", "--limit", "1"])
    assert calls["n"] == 3
    capsys.readouterr()
    assert pg.main(["--faq-children", "--limit", "1"]) == 0
    assert calls["n"] == 3, "the fourth call reached the paid writer"
    assert '"reason": "g36_breaker"' in capsys.readouterr().out
    assert list(content.glob("*.md")) == []


def test_attempt_cap_is_five(monkeypatch, tmp_path):
    monkeypatch.setattr(pg, "STATE_DIR", tmp_path)
    monkeypatch.setattr(pg.telegram_lib, "alert", lambda *a, **k: True)
    for _ in range(5):
        assert pg.faq_breaker() is None
        pg.faq_note_attempt("delta")      # delta refusals never trip the G36 breaker
    assert pg.faq_breaker() == "attempt_cap"


# --- edit 4: verdict denominator --------------------------------------------

def test_verdict_counts_only_published_children(monkeypatch):
    opened = date(2026, 8, 3)
    items = [{"slug": f"k{i}", "parent": "/faq/p/"} for i in range(20)]
    for it in items[:4]:
        it["published"] = {"on": "2026-08-05", "path": "x"}
    rows = [
        {"week": "w1", "since": "2026-07-20", "until": "2026-07-26", "days_covered": 7,
         "verified_by_page": {"/faq/p/": 10}},
        {"week": "w2", "since": "2026-08-10", "until": "2026-08-16", "days_covered": 7,
         "verified_by_page": {"/faq/p/": 10, "/faq/k0/": 3, "/faq/k1/": 2}},
    ]
    monkeypatch.setattr(fv, "weeks", lambda: rows)
    v = fv.judge({"opened": opened.isoformat(), "items": items}, date(2026, 8, 20), False)
    assert v["children_total"] == 4
    assert v["children_fetched"] == 2
    assert v["children_share"] == 0.5
    assert v["status"] == "pass"


def test_verdict_with_no_published_child_is_unmeasurable():
    v = fv.judge({"opened": "2026-08-03", "items": [{"slug": "a", "parent": "/p/"}]},
                 date(2026, 8, 20), False)
    assert v["status"] == "unmeasurable"


# --- edit 5: selector premise filter ----------------------------------------

def test_dollar_forty_is_a_false_premise():
    assert fc.premise_problem("Is the $40 Core tier deposit a fee or collateral?")


def test_off_issuer_questions_are_refused_at_selection():
    assert fc.off_issuer("Do I pay taxes when I spend crypto on a debit card?") == "off_issuer"
    assert fc.off_issuer("Is Coinbase Card cheaper than a bank card?") == "off_issuer"
    assert fc.off_issuer("Do I pay tax on ether.fi Cash cashback in my country?") == "off_issuer"
    assert fc.off_issuer("What is the difference between ether.fi Cash and Crypto.com Card for European users?") == "off_issuer"
    assert fc.off_issuer("Is the ether.fi referral program available in my country?") is None
    assert fc.off_issuer("How long does KYC verification take on ether.fi?") is None


# --- edit 2, manual CLI path (ruling-2) ---------------------------------------

def test_cli_paths_pass_the_writer_sha(monkeypatch):
    seen = {}
    monkeypatch.setattr(fc, "_writer_sha", lambda: "sha-literal-1")
    monkeypatch.setattr(fc, "select", lambda size, sha=None: seen.__setitem__("select", sha) or [])
    monkeypatch.setattr(fc, "open_next_cohort",
                        lambda **kw: seen.__setitem__("open", kw.get("writer_sha")) or {"event": "cohort_refused", "reason": "stub"})
    for argv in (["faq-children.py"], ["faq-children.py", "--write"]):
        monkeypatch.setattr(sys, "argv", argv)
        try:
            fc.main()
        except SystemExit:
            pass
    assert seen == {"select": "sha-literal-1", "open": "sha-literal-1"}


def test_real_writer_sha_is_16_hex():
    assert re.fullmatch(r"[0-9a-f]{16}", fc._writer_sha())


# --- CCH-FOLLOWUPS-0929 selector-siblings: guard in the writer loop -----------

def test_retryable_selector_is_closed_for_good():
    assert fc._retryable({"gate": "selector"}, "any-sha") is False


def test_open_cohort_row_with_competitor_question_never_reaches_the_writer(monkeypatch, tmp_path, capsys):
    q = "What is the difference between ether.fi Cash and Crypto.com Card for European users?"
    it = _item("crypto-com-eu")
    it["question"] = q
    led = _ledger(tmp_path, monkeypatch, [[it]])
    content = tmp_path / "content"
    content.mkdir()

    def writer(**k):
        raise AssertionError("writer must not be called for a selector-refused row")

    monkeypatch.setattr(pg, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(pg, "CONTENT_DIR", content)
    monkeypatch.setattr(pg, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(pg, "call_claude_cli", writer)
    monkeypatch.setattr(pg.telegram_lib, "alert", lambda *a, **k: True)
    monkeypatch.setattr(pg.no_progress_guard, "check", lambda *a, **k: False)
    monkeypatch.delenv("PSEO_ENABLED", raising=False)

    pg.main(["--faq-children", "--limit", "1"])
    ref = json.loads(led.read_text())["cohorts"][0]["items"][0]["refused"]
    assert ref["gate"] == "selector"
    assert ref["reasons"] == ["off_issuer"]
    out = capsys.readouterr().out
    assert '"gate": "selector"' in out and '"slug": "crypto-com-eu"' in out
