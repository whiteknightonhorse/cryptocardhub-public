"""page-updates.py — Fable 2026-09-14, FABLE_DEFYCARD_ANSWER.md A4 steps 3-4.

Channel 2: strengthen a page Bing already ranks for a query, instead of
printing a near-duplicate new one (A2.3: 5 of 6 promotions on 2026-09-01 did
exactly that). Gate is the SAME two literals faq-children.py enforces for a
brand-new child — this file does not re-derive them, it proves the wiring.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pu = _load("page_updates", "scripts/page-updates.py")

FRONTMATTER = (
    '---\ntitle: "Binance Card Review"\nslug: "binance-card-review"\n'
    'generated_at: "2026-05-08T22:02:19.436485+00:00"\n---\n'
)
# The rate sentence is not decoration: since E-11 a section may only state a
# number this page (or a verified fact) already carries, so the fixture page
# must own the numbers GOOD_SECTION quotes back. Worded differently from the
# child on purpose — the delta gate still has to see a real delta.
BODY_NO_FAQ = ("## What Is the Binance Card?\n\nSome real existing sentences about the card.\n"
               "Rates here run from 2% to 8% by holding, the middle steps pay 3% and 5%, "
               "and conversion adds 1% on top.\n")
BODY_WITH_FAQ = BODY_NO_FAQ + "\n## FAQ\n\nExisting FAQ content here.\n"

GOOD_SECTION = (
    "### Can I Use Binance Card for Everyday Purchases?\n\n"
    "Yes. Binance Card works at any Visa merchant for everyday spend, with "
    "cashback of 2% to 8% depending on BNB tier. Standard tier needs no BNB "
    "and earns 2%. Silver tier needs 50 BNB for 3%. Gold tier needs 500 BNB "
    "for 5%. Diamond tier needs 5,000 BNB for 8%. FX runs 1-2% outside BUSD."
)


# --- frontmatter split / insert / bump --------------------------------------

def test_split_frontmatter_round_trips():
    fm, body = pu.split_frontmatter(FRONTMATTER + BODY_NO_FAQ)
    assert fm == FRONTMATTER
    assert body == BODY_NO_FAQ


def test_split_frontmatter_refuses_a_non_frontmatter_document():
    import pytest
    with pytest.raises(ValueError):
        pu.split_frontmatter("no frontmatter here at all")


def test_insert_section_lands_before_the_faq_heading():
    out = pu.insert_section(BODY_WITH_FAQ, GOOD_SECTION)
    assert out.index(GOOD_SECTION.split("\n")[0]) < out.index("## FAQ")


def test_insert_section_appends_at_the_end_when_there_is_no_faq_heading():
    out = pu.insert_section(BODY_NO_FAQ, GOOD_SECTION)
    assert out.rstrip().endswith(GOOD_SECTION.split("\n")[-1])


def test_bump_generated_at_only_touches_an_existing_field():
    when = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)
    out = pu.bump_generated_at(FRONTMATTER, when)
    assert '"2026-09-14T12:00:00+00:00"' in out
    assert "2026-05-08T22:02:19" not in out


def test_bump_generated_at_leaves_frontmatter_untouched_if_the_field_is_absent():
    fm = '---\ntitle: "x"\n---\n'
    assert pu.bump_generated_at(fm, datetime.now(timezone.utc)) == fm


# --- apply_updates: the full wiring, writer injected ------------------------

def _fake_pc_rows(rows):
    return lambda path=None: rows


def test_apply_updates_inserts_a_passing_section_and_logs_applied(tmp_path, monkeypatch):
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    (content_dir / "binance-card-review.md").write_text(FRONTMATTER + BODY_WITH_FAQ, encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"

    monkeypatch.setattr(pu.pg, "CONTENT_DIR", content_dir)
    monkeypatch.setattr(pu.pg, "FROZEN_SLUGS", frozenset())
    monkeypatch.setattr(pu, "LEDGER", ledger)
    monkeypatch.setattr(pu.pc, "parse_update_candidates", _fake_pc_rows([
        {"query": "binance card usage for everyday purchases", "impressions": 215, "clicks": 4,
         "path": "/reviews/binance-card-review/", "page_impressions": 171, "page_clicks": 0, "position": 6.0},
    ]))

    result = pu.apply_updates(dry_run=False, writer=lambda q, body: GOOD_SECTION,
                              now=datetime(2026, 9, 14, tzinfo=timezone.utc))
    assert result["applied"] == 1
    written = (content_dir / "binance-card-review.md").read_text(encoding="utf-8")
    assert "Can I Use Binance Card for Everyday Purchases" in written
    assert written.index("Can I Use Binance") < written.index("## FAQ")
    assert '"2026-09-14T00:00:00+00:00"' in written   # generated_at bumped on real insertion

    ledger_rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines()]
    assert ledger_rows[0]["event"] == "applied"
    assert ledger_rows[0]["bing_before"]["position"] == 6.0


def test_apply_updates_never_touches_a_frozen_slug(tmp_path, monkeypatch):
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    (content_dir / "uphold-card-review.md").write_text(FRONTMATTER + BODY_WITH_FAQ, encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"

    monkeypatch.setattr(pu.pg, "CONTENT_DIR", content_dir)
    monkeypatch.setattr(pu.pg, "FROZEN_SLUGS", frozenset({"uphold-card-review"}))
    monkeypatch.setattr(pu, "LEDGER", ledger)
    monkeypatch.setattr(pu.pc, "parse_update_candidates", _fake_pc_rows([
        {"query": "uphold debit card", "impressions": 90, "clicks": 1,
         "path": "/reviews/uphold-card-review/", "page_impressions": 80, "page_clicks": 1, "position": 5.0},
    ]))

    result = pu.apply_updates(dry_run=False, writer=lambda q, body: GOOD_SECTION)
    assert result["applied"] == 0
    original = (content_dir / "uphold-card-review.md").read_text(encoding="utf-8")
    assert original == FRONTMATTER + BODY_WITH_FAQ   # byte-for-byte untouched
    ledger_rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines()]
    assert ledger_rows[0] == {**ledger_rows[0], "event": "refused", "reason": "frozen"}


def test_apply_updates_refuses_a_section_that_just_restates_the_page(tmp_path, monkeypatch):
    """The delta gate: a section that IS the page's own existing text must
    never be inserted, whatever the query's Bing numbers say."""
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    (content_dir / "binance-card-review.md").write_text(FRONTMATTER + BODY_WITH_FAQ, encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"

    monkeypatch.setattr(pu.pg, "CONTENT_DIR", content_dir)
    monkeypatch.setattr(pu.pg, "FROZEN_SLUGS", frozenset())
    monkeypatch.setattr(pu, "LEDGER", ledger)
    monkeypatch.setattr(pu.pc, "parse_update_candidates", _fake_pc_rows([
        {"query": "binance card usage for everyday purchases", "impressions": 215, "clicks": 4,
         "path": "/reviews/binance-card-review/", "page_impressions": 171, "page_clicks": 0, "position": 6.0},
    ]))

    result = pu.apply_updates(dry_run=False, writer=lambda q, body: body)   # writer parrots the page back
    assert result["applied"] == 0
    original = (content_dir / "binance-card-review.md").read_text(encoding="utf-8")
    assert original == FRONTMATTER + BODY_WITH_FAQ
    ledger_rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines()]
    assert ledger_rows[0]["event"] == "refused"
    assert ledger_rows[0]["reason"] == "delta_gate"


def test_applied_this_week_counts_intels_queued_rows_too(tmp_path, monkeypatch):
    """MUTATION "queued dropped from the filter": E-18's `intel-route.py`
    writes `event: "queued"` rows into this SAME ledger so the D4 cap
    ("5/нед общий с Bing") is enforced by one counter reading one file. If
    `applied_this_week` only counted `applied`, the two callers would keep
    two independent counts that can drift — the schedule/cadence "brother of
    slugify" defect this project has already paid for once."""
    ledger = tmp_path / "ledger.jsonl"
    week = "2026-W38"
    ledger.write_text("\n".join(json.dumps(r) for r in [
        {"event": "applied", "week": week, "query": "bing1"},
        {"event": "queued", "week": week, "source": "intel", "event_id": "e1"},
        {"event": "applied", "week": "2026-W37", "query": "other week"},
        {"event": "refused", "week": week, "reason": "delta_gate"},
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(pu, "LEDGER", ledger)
    assert pu.applied_this_week(week) == 2


def test_weekly_cap_is_pinned_to_five():
    """FA-01: D4 ruling "5/нед". MUTATION WEEKLY_CAP=6 -> red. The literal is the law;
    the cap test below only reads the constant, so it moves with the mutation."""
    assert pu.WEEKLY_CAP == 5


def test_apply_updates_respects_the_weekly_cap(tmp_path, monkeypatch):
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    for i in range(3):
        (content_dir / f"page{i}.md").write_text(
            FRONTMATTER.replace("binance-card-review", f"page{i}") + BODY_WITH_FAQ, encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)   # a Monday, ISO week 2026-W38
    ledger.write_text(
        "\n".join(json.dumps({"event": "applied", "week": pu.iso_week(now.date()), "query": f"q{i}"})
                  for i in range(pu.WEEKLY_CAP - 1)) + "\n",
        encoding="utf-8")

    monkeypatch.setattr(pu.pg, "CONTENT_DIR", content_dir)
    monkeypatch.setattr(pu.pg, "FROZEN_SLUGS", frozenset())
    monkeypatch.setattr(pu, "LEDGER", ledger)
    monkeypatch.setattr(pu.pc, "parse_update_candidates", _fake_pc_rows([
        {"query": f"query {i}", "impressions": 50, "clicks": 1, "path": f"/reviews/page{i}/",
         "page_impressions": 40, "page_clicks": 0, "position": 5.0} for i in range(3)
    ]))

    result = pu.apply_updates(dry_run=False, writer=lambda q, body: GOOD_SECTION, now=now)
    assert result["applied"] == 1     # only 1 slot of room was left


def test_apply_updates_dry_run_writes_nothing():
    pass  # covered structurally below


def test_apply_updates_dry_run_touches_no_file_and_no_ledger(tmp_path, monkeypatch):
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    (content_dir / "binance-card-review.md").write_text(FRONTMATTER + BODY_WITH_FAQ, encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"

    monkeypatch.setattr(pu.pg, "CONTENT_DIR", content_dir)
    monkeypatch.setattr(pu.pg, "FROZEN_SLUGS", frozenset())
    monkeypatch.setattr(pu, "LEDGER", ledger)
    monkeypatch.setattr(pu.pc, "parse_update_candidates", _fake_pc_rows([
        {"query": "binance card usage for everyday purchases", "impressions": 215, "clicks": 4,
         "path": "/reviews/binance-card-review/", "page_impressions": 171, "page_clicks": 0, "position": 6.0},
    ]))

    result = pu.apply_updates(dry_run=True, writer=lambda q, body: GOOD_SECTION)
    assert result["applied"] == 1
    assert (content_dir / "binance-card-review.md").read_text(encoding="utf-8") == FRONTMATTER + BODY_WITH_FAQ
    assert not ledger.exists()


# --- compute_verdicts: +14/+28 readback --------------------------------------

def test_compute_verdicts_skips_an_update_younger_than_14_days(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps({
        "event": "applied", "week": "2026-W36", "date": "2026-09-10T00:00:00+00:00",
        "query": "q", "slug": "s", "bing_before": {"impressions": 1, "clicks": 0, "position": 5.0},
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(pu, "LEDGER", ledger)
    out = pu.compute_verdicts(today=date(2026, 9, 14), page_stats=lambda q: [])
    assert out == []


def test_compute_verdicts_reads_the_14_day_window_and_matches_the_page(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps({
        "event": "applied", "week": "2026-W36", "date": "2026-08-31T00:00:00+00:00",
        "query": "binance card usage for everyday purchases", "slug": "binance-card-review",
        "bing_before": {"impressions": 171, "clicks": 0, "position": 6.0},
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(pu, "LEDGER", ledger)
    stats = lambda q: [{"path": "/binance-card-review/", "impressions": 250, "clicks": 3, "position": 3.5}]
    out = pu.compute_verdicts(today=date(2026, 9, 14), page_stats=stats)
    assert len(out) == 1
    assert out[0]["window"] == 14
    assert out[0]["pos_before"] == 6.0
    assert out[0]["pos_after"] == 3.5


def test_compute_verdicts_never_judges_the_same_window_twice(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        json.dumps({"event": "applied", "week": "2026-W36", "date": "2026-08-31T00:00:00+00:00",
                    "query": "q", "slug": "s", "bing_before": {"impressions": 1, "clicks": 0, "position": 6.0}}) + "\n" +
        json.dumps({"event": "verdict", "window": 14, "query": "q", "slug": "s",
                    "applied_date": "2026-08-31", "pos_before": 6.0, "pos_after": 4.0}) + "\n",
        encoding="utf-8")
    monkeypatch.setattr(pu, "LEDGER", ledger)
    out = pu.compute_verdicts(today=date(2026, 9, 14), page_stats=lambda q: [])
    assert out == []


# --- G37 wiring (CCH-HUM-05): the write must use the REWRITTEN section ------

TELLY_SECTION = GOOD_SECTION.replace("Yes.", "Actually, it is very crucial to note: yes.")


def _stub_hres(payload, refused=None):
    import humanize_gate as hg
    return hg.HumanizeResult(payload, 5, 0 if not refused else 5, rewritten=not refused,
                             refused_reason=refused)


def _setup_one_page(tmp_path, monkeypatch):
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    page = content_dir / "binance-card-review.md"
    page.write_text(FRONTMATTER + BODY_WITH_FAQ, encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(pu.pg, "CONTENT_DIR", content_dir)
    monkeypatch.setattr(pu.pg, "FROZEN_SLUGS", frozenset())
    monkeypatch.setattr(pu, "LEDGER", ledger)
    monkeypatch.setattr(pu.telegram_lib, "alert", lambda *a, **k: True)
    monkeypatch.setattr(pu.pc, "parse_update_candidates", _fake_pc_rows([
        {"query": "binance card usage for everyday purchases", "impressions": 215, "clicks": 4,
         "path": "/reviews/binance-card-review/", "page_impressions": 171, "page_clicks": 0, "position": 6.0},
    ]))
    return page, ledger


def test_applied_file_contains_the_humanized_rewrite(tmp_path, monkeypatch):
    page, ledger = _setup_one_page(tmp_path, monkeypatch)
    seen = {}

    def humanizer(payload):
        seen["in"] = payload["body_markdown"]
        return _stub_hres({"body_markdown": GOOD_SECTION})

    result = pu.apply_updates(dry_run=False, writer=lambda q, b: TELLY_SECTION,
                              humanizer=humanizer, now=datetime(2026, 9, 14, tzinfo=timezone.utc))
    assert result["applied"] == 1
    assert seen["in"] == TELLY_SECTION
    written = page.read_text(encoding="utf-8")
    assert "Actually, it is very crucial" not in written
    assert "Can I Use Binance Card for Everyday Purchases" in written
    row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert row["humanize"] == {"flags_before": 5, "flags_after": 0, "rewritten": True}


def test_refused_humanize_leaves_the_page_untouched_and_logs_a_g37_row(tmp_path, monkeypatch):
    page, ledger = _setup_one_page(tmp_path, monkeypatch)
    before = page.read_text(encoding="utf-8")
    result = pu.apply_updates(
        dry_run=False, writer=lambda q, b: TELLY_SECTION,
        humanizer=lambda p: _stub_hres(p, refused="G37:score_after:9.00"),
        now=datetime(2026, 9, 14, tzinfo=timezone.utc))
    assert result["applied"] == 0
    assert page.read_text(encoding="utf-8") == before
    row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert row["event"] == "refused" and row["gate"] == "G37"
    assert row["reason"] == "G37:score_after:9.00"


def test_apply_updates_never_applies_the_same_query_to_the_same_slug_twice(tmp_path, monkeypatch):
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    page = content_dir / "binance-card-review.md"
    page.write_text(FRONTMATTER + BODY_WITH_FAQ, encoding="utf-8")
    before = page.read_bytes()
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps({"event": "applied", "week": "2026-W40", "slug": "binance-card-review",
                                  "query": "binance card usage for everyday purchases"}) + "\n", encoding="utf-8")

    monkeypatch.setattr(pu.pg, "CONTENT_DIR", content_dir)
    monkeypatch.setattr(pu.pg, "FROZEN_SLUGS", frozenset())
    monkeypatch.setattr(pu, "LEDGER", ledger)
    monkeypatch.setattr(pu.pc, "parse_update_candidates", _fake_pc_rows([
        {"query": "binance card usage for everyday purchases", "impressions": 215, "clicks": 4,
         "path": "/reviews/binance-card-review/", "page_impressions": 171, "page_clicks": 0, "position": 6.0},
    ]))
    calls = []

    def writer(q, body):
        calls.append(q)
        return GOOD_SECTION

    result = pu.apply_updates(dry_run=False, writer=writer,
                              now=datetime(2026, 10, 20, tzinfo=timezone.utc))
    assert calls == []
    assert result["applied"] == 0
    assert [(e["event"], e["reason"]) for e in result["entries"]] == [("refused", "already_applied")]
    assert page.read_bytes() == before
