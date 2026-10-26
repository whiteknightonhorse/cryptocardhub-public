"""G13 (Y50 P0-2) — the report may only speak in human sessions, and may
never send a number it did not measure.

THE FORMULA IS A LITERAL HERE (lesson Y49). If this test computed the
expectation by calling `human_sessions()`, then changing that function would
change the expectation too and the gate would stay green through the very
mutation it exists to catch. The numbers below are worked out by hand.
"""
from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "weekly_report", Path(__file__).resolve().parent.parent / "weekly-report.py")
wr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(wr)


# Shaped like the real property over 2026-05-06..08-11: a large, barely-engaged
# direct bucket (headless scanners) next to small, well-engaged real referrers.
FIXTURE = [
    {"source": "(direct)",    "sessions": 1426, "engaged": 156},
    {"source": "bing",        "sessions": 328,  "engaged": 229},
    {"source": "duckduckgo",  "sessions": 90,   "engaged": 52},
    {"source": "chatgpt.com", "sessions": 35,   "engaged": 18},
]

# LAW, worked out by hand: non-direct sessions + ENGAGED direct sessions.
#   non-direct = 328 + 90 + 35 = 453
#   engaged direct                = 156
#   human                         = 609
EXPECTED_HUMAN = 609
EXPECTED_RAW = 1879          # 1426 + 328 + 90 + 35
EXPECTED_BOT_DIRECT = 1270   # 1426 - 156


def test_human_sessions_uses_the_stated_formula():
    assert wr.human_sessions(FIXTURE) == EXPECTED_HUMAN


def test_human_sessions_is_not_raw_sessions():
    """The whole point: the two numbers must differ, and human must be smaller.

    If someone 'simplifies' human_sessions to sum(sessions), this fails.
    """
    assert wr.raw_sessions(FIXTURE) == EXPECTED_RAW
    assert wr.human_sessions(FIXTURE) < wr.raw_sessions(FIXTURE)
    assert wr.raw_sessions(FIXTURE) - wr.human_sessions(FIXTURE) == EXPECTED_BOT_DIRECT


def test_unengaged_direct_never_counts_as_a_human():
    """1 000 direct sessions that never engaged must add exactly 0 humans."""
    before = wr.human_sessions(FIXTURE)
    inflated = [dict(r) for r in FIXTURE]
    inflated[0]["sessions"] += 1000          # scanners arrive, none engage
    assert wr.human_sessions(inflated) == before


def test_engaged_direct_does_count():
    """A direct session that engages is a person and must be counted."""
    bumped = [dict(r) for r in FIXTURE]
    bumped[0]["engaged"] += 10
    bumped[0]["sessions"] += 10
    assert wr.human_sessions(bumped) == EXPECTED_HUMAN + 10


def test_bot_direct_share_is_reported():
    assert wr.bot_direct_sessions(FIXTURE) == EXPECTED_BOT_DIRECT


# --- the no-defaulted-figures gate -----------------------------------------
# Y57: unmeasured figures no longer block the send (a report that never
# arrives is a silent zero) — they must head the sent text with their cause.

# --- T-0137 (ruling-1): the localization Phase-2 gate reminder ------------
# LAW, literal per localization/SKILL.md §0: start 2026-05-05, gate 2026-11-05.
# The expected dates/labels below are written by hand, NOT derived by calling
# wr.LOCALIZATION_START / wr.LOCALIZATION_PHASE2_GATE (Y49: a test that reads
# its own expectation from the code under test cannot go red under mutation).

from datetime import date as _date


def test_localization_phase_before_gate_reports_month_and_countdown():
    out = wr.localization_phase(_date(2026, 9, 21))
    assert out["months"] == 4
    assert out["label"] == "Phase 1 (EN only) — month 4/6"
    assert out["days_left"] == 45
    assert "localization_gate" not in out


def test_localization_phase_control_day_before_gate_still_not_due():
    """Control: one day before 2026-11-05 the gate must NOT fire."""
    out = wr.localization_phase(_date(2026, 11, 4))
    assert "localization_gate" not in out


def test_localization_phase_gate_day_fires_and_reaches_action_required():
    out = wr.localization_phase(_date(2026, 11, 5))
    assert isinstance(out["localization_gate"], wr.Missing)
    assert "label" not in out
    header = wr.action_required({"localization": out})
    assert header is not None
    assert header.startswith("ACTION REQUIRED")
    assert "localization_gate" in header
    assert "2026-11-05" in header


def test_render_localization_prints_one_line_each_side_of_the_gate():
    before = wr.render_localization({"localization": wr.localization_phase(_date(2026, 9, 21))})
    assert before == "LOCALIZATION: Phase 1 (EN only) — month 4/6 · §0.1 gate 2026-11-05 in 45 days"
    after = wr.render_localization({"localization": wr.localization_phase(_date(2026, 11, 5))})
    assert after.startswith("LOCALIZATION: n/a (2026-11-05 reached")


def test_an_unmeasured_figure_heads_the_report_instead_of_blocking_it():
    report = {"human_sessions": 609,
              "clicks_by_partner": wr.Missing("clicks_by_partner", "GA4 400")}
    header = wr.action_required(report)
    assert header.startswith("ACTION REQUIRED")
    assert "clicks_by_partner" in header
    assert "GA4 400" in header


def test_a_fully_measured_report_carries_no_action_header():
    assert wr.action_required(
        {"human_sessions": 609, "clicks_by_partner": {"etherfi": 33}}) is None


def test_send_transmits_the_header_not_just_builds_it(monkeypatch):
    """Y53 lesson: a correct helper nobody calls is the bug. This reads the
    text at the boundary where it leaves for Telegram."""
    import urllib.parse
    sent = {}

    def fake_urlopen(url, data=None, timeout=None):
        sent["data"] = data

        class R:
            def read(self):
                return b""
        return R()

    monkeypatch.setattr(wr.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("TG_BOT_TOKEN", "t")
    monkeypatch.setenv("TG_CHAT_ID", "c")
    wr.send({"x": wr.Missing("x", "the cause")}, "BODY")
    body = urllib.parse.unquote_plus(sent["data"].decode())
    assert "ACTION REQUIRED" in body
    assert "the cause" in body
    assert "BODY" in body


def test_telegram_cap_is_the_documented_literal():
    """Literal, not derived from the function under test (Y49's rule) — this
    is Telegram's own documented sendMessage limit, and it should never move
    quietly if someone "cleans up" the constant."""
    assert wr.TELEGRAM_MAX_LEN == 4096


def test_send_splits_a_message_over_the_telegram_cap(monkeypatch):
    """Ruling-2, E-15: the 09-14 cron 400'd because the report grew past 4096
    chars and send() made exactly one call with the whole text. Reproduce a
    report of that shape and prove send() now makes >1 call, each within the
    real Telegram cap, with no content lost across the split."""
    import urllib.parse
    calls = []

    def fake_urlopen(url, data=None, timeout=None):
        calls.append(data)

        class R:
            def read(self):
                return b""
        return R()

    monkeypatch.setattr(wr.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("TG_BOT_TOKEN", "t")
    monkeypatch.setenv("TG_CHAT_ID", "c")

    big_text = "\n".join(f"line {i} some report figures here" for i in range(200))
    assert len(big_text) > 4096
    wr.send({}, big_text)

    assert len(calls) > 1
    bodies = []
    for d in calls:
        fields = urllib.parse.parse_qs(d.decode())
        text = fields["text"][0]
        assert len(text) <= 4096
        # strip the "[i/n]\n" part marker before checking content survives
        bodies.append(text.split("\n", 1)[1])
    assert "\n".join(bodies) == big_text


def test_missing_never_renders_as_a_number():
    """A failed fetch must read as 'n/a', never as 0."""
    text = str(wr.Missing("median_views_7d", "no videos in window"))
    assert "n/a" in text
    assert text.strip() != "0"


def test_report_text_never_prints_raw_sessions_as_the_audience():
    """The headline figure is HUMAN SESSIONS; raw is only ever shown as a
    discard note, so nobody can quote the inflated number."""
    report = {
        "since": "2026-08-04", "until": "2026-08-11",
        "human_sessions": EXPECTED_HUMAN, "raw_sessions": EXPECTED_RAW,
        "bot_direct": EXPECTED_BOT_DIRECT, "by_source": FIXTURE,
        "clicks_by_source": {"chatgpt.com": 10}, "clicks_by_partner": {"etherfi": 33},
        "utm_content": {"(not set)": 1940}, "videos_7d": 0,
        "median_views_7d": wr.Missing("median_views_7d", "no videos in window"),
        "retention_7d": wr.Missing("retention_7d", "no retention samples"),
    }
    text = wr.render(report)
    assert f"HUMAN SESSIONS: {EXPECTED_HUMAN}" in text
    assert "discarded as non-human" in text
    head = text.split("HUMAN SESSIONS")[0]
    assert str(EXPECTED_RAW) not in head, "raw sessions must not lead the report"


# --- bing funnel + page-updates (Fable 2026-09-14, A4 steps 4 and 6) --------
#
# 07.09's live bug: "bing queries N" alone could not distinguish 3 genuine
# candidates from one query counted 3 times across weekly rows. The funnel
# line must show new/update/near-dup/below-floor, read from CANDIDATES.md
# (the Monday 05:40 cron), not a second live Bing API hit.

CANDIDATES_MD_FIXTURE = """# Keyword candidates

- fetched: `2026-09-14T05:40:00+00:00`
- queries returned by Bing (unique, summed across weekly rows): **225**
- already covered by a page: 18
- off transactional intent: 2
- near-duplicate of an existing page: 11
- below 10 impressions (summed over the window): 190
- seen in only one weekly row (< 2 weeks): 3
- **new-page candidates: 0**
- **update candidates (a page of ours already ranks): 9**

## New-page candidates

_No candidate cleared the filters. That is an answer, not a failure:_
_a zero here means the measured demand is already served or already ranked._

## Update candidates

| query | impressions | clicks | ranked page | page impressions | page clicks | avg position |
|---|---|---|---|---|---|---|
| binance card usage for everyday purchases | 215 | 4 | `/reviews/binance-card-review/` | 171 | 0 | 6 |
"""


def test_collect_bing_reads_the_funnel_from_candidates_md(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    d = tmp_path / ".claude" / "skills" / "keywords"
    d.mkdir(parents=True)
    (d / "CANDIDATES.md").write_text(CANDIDATES_MD_FIXTURE, encoding="utf-8")
    out = wr._collect_bing()
    assert out["bing"] == {"queries": 225, "new": 0, "update": 9, "near_dup": 11, "below_floor": 190}


def test_collect_bing_is_missing_not_zero_when_candidates_md_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    out = wr._collect_bing()
    assert isinstance(out["bing"], wr.Missing)


def test_collect_page_updates_counts_applied_judged_improved(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    d = tmp_path / "logs" / "pseo"
    d.mkdir(parents=True)
    (d / "page-updates.jsonl").write_text(
        '{"event": "applied", "query": "a"}\n'
        '{"event": "applied", "query": "b"}\n'
        '{"event": "verdict", "query": "a", "pos_before": 6.0, "pos_after": 3.0}\n'
        '{"event": "verdict", "query": "b", "pos_before": 6.0, "pos_after": 9.0}\n'
        '{"event": "refused", "reason": "frozen", "query": "c"}\n',
        encoding="utf-8")
    out = wr._collect_page_updates()
    assert out["page_updates"] == {"applied": 2, "judged": 2, "improved": 1}


def test_collect_page_updates_is_a_real_zero_not_missing_when_ledger_absent(tmp_path, monkeypatch):
    """Y57's rule the other way: an empty ledger is a legitimate zero, not a
    failure — this figure must never block or hide the send."""
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    assert wr._collect_page_updates() == {"page_updates": {"applied": 0, "judged": 0, "improved": 0}}


def test_render_always_prints_the_updates_line_even_on_an_empty_ledger():
    """Mutation target: an empty ledger must not make the whole line vanish —
    'the report has no UPDATES line' must be indistinguishable from nothing to
    report only by reading the numbers, never by the line's absence."""
    report = {
        "since": "2026-09-01", "until": "2026-09-08",
        "human_sessions": 100, "raw_sessions": 120, "bot_direct": 20, "by_source": [],
        "clicks_by_source": {}, "clicks_by_partner": {}, "utm_content": {}, "videos_7d": 0,
        "median_views_7d": wr.Missing("median_views_7d", "no videos"),
        "retention_7d": wr.Missing("retention_7d", "no retention"),
        "page_updates": {"applied": 0, "judged": 0, "improved": 0},
    }
    text = wr.render(report)
    assert "UPDATES: 0 applied / 0 judged / 0 improved" in text


# --- backup (E-04, 2026-09-14) ---------------------------------------------
# The law: the BACKUP line is ALWAYS in the rendered text — present, absent
# key, or a failed tar all print one line — and an absent/failed backup is
# Missing (ACTION REQUIRED), never a silently vanished line or a fake 0.

def test_collect_backup_is_missing_when_no_archive_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    out = wr._collect_backup()
    assert isinstance(out["backup"], wr.Missing)
    assert "backup-daily.sh" in str(out["backup"])


def test_collect_backup_reads_age_size_of_the_newest_archive(tmp_path, monkeypatch):
    import os
    import tarfile
    import time

    monkeypatch.setattr(wr, "ROOT", tmp_path)
    d = tmp_path / "backups"
    d.mkdir()
    src = tmp_path / "dummy.txt"
    src.write_text("x")

    older = d / "daily-20260901T0000Z.tar.gz"
    newer = d / "daily-20260913T0000Z.tar.gz"
    now = time.time()
    for p, days_old in ((older, 13), (newer, 1)):
        with tarfile.open(p, "w:gz") as tf:
            tf.add(src, arcname="dummy.txt")
        ts = now - days_old * 86400
        os.utime(p, (ts, ts))

    out = wr._collect_backup()
    b = out["backup"]
    assert b["path"] == newer.name, "must pick the newest archive, not the first found"
    assert b["age_days"] == 1
    assert b["verified"] is True
    assert b["size_bytes"] > 0


def test_collect_backup_is_missing_when_the_newest_archive_fails_verification(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    d = tmp_path / "backups"
    d.mkdir()
    (d / "daily-20260913T0000Z.tar.gz").write_bytes(b"not actually a gzip")
    out = wr._collect_backup()
    assert isinstance(out["backup"], wr.Missing)
    assert "verification" in str(out["backup"])


def test_render_always_prints_a_backup_line_even_when_not_collected():
    """Mutation target: dropping the key must not make the line vanish."""
    report = {
        "since": "2026-09-01", "until": "2026-09-08",
        "human_sessions": 100, "raw_sessions": 120, "bot_direct": 20, "by_source": [],
        "clicks_by_source": {}, "clicks_by_partner": {}, "utm_content": {}, "videos_7d": 0,
        "median_views_7d": wr.Missing("median_views_7d", "no videos"),
        "retention_7d": wr.Missing("retention_7d", "no retention"),
    }
    assert "BACKUP: n/a (not collected in this run)" in wr.render(report)


def test_render_prints_the_backup_figures_when_present():
    report = {
        "since": "2026-09-01", "until": "2026-09-08",
        "human_sessions": 100, "raw_sessions": 120, "bot_direct": 20, "by_source": [],
        "clicks_by_source": {}, "clicks_by_partner": {}, "utm_content": {}, "videos_7d": 0,
        "median_views_7d": wr.Missing("median_views_7d", "no videos"),
        "retention_7d": wr.Missing("retention_7d", "no retention"),
        "backup": {"path": "daily-20260913T0000Z.tar.gz", "age_days": 1,
                   "size_bytes": 3183776, "verified": True},
    }
    assert "BACKUP: daily-20260913T0000Z.tar.gz (1d old, 3183776 bytes, verified)" in wr.render(report)


def test_a_missing_backup_heads_the_report_with_action_required():
    header = wr.action_required(
        {"human_sessions": 609, "backup": wr.Missing("backup", "no backups/daily-*.tar.gz")})
    assert header.startswith("ACTION REQUIRED")
    assert "backup" in header


def test_render_prints_the_bing_funnel_line_with_all_five_figures():
    report = {
        "since": "2026-09-01", "until": "2026-09-08",
        "human_sessions": 100, "raw_sessions": 120, "bot_direct": 20, "by_source": [],
        "clicks_by_source": {}, "clicks_by_partner": {}, "utm_content": {}, "videos_7d": 0,
        "median_views_7d": wr.Missing("median_views_7d", "no videos"),
        "retention_7d": wr.Missing("retention_7d", "no retention"),
        "bing": {"queries": 225, "new": 0, "update": 9, "near_dup": 11, "below_floor": 190},
    }
    text = wr.render(report)
    assert "bing: 225 queries / new 0 / update 9 / near-dup 11 / below-floor 190" in text


# --- GSC as the second demand instrument (E-12, Fable 2026-09-14) ----------
#
# The figures below are the literal FABLE_DEFYCARD_PART2.md line-34 answer:
# 211 impressions / 1 click / avg position 40.6 / 89 of 873 pages with >=1
# impression / 1 of 8 inspected top-fetched URLs "Discovered - currently not
# indexed". _collect_gsc() reads logs/gsc/report.json (written by the Monday
# 06:30 cron, scripts/gsc-report.py) — same read-the-funnel-file rule as
# _collect_bing(), never a second live API hit from inside this report.

GSC_FIXTURE = {"status": "ok", "impressions": 211, "clicks": 1, "position": 40.6,
              "pages": 89, "indexing": {"inspected": 8, "not_indexed": 1}}


def test_collect_gsc_reads_the_report_file(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    d = tmp_path / "logs" / "gsc"
    d.mkdir(parents=True)
    (d / "report.json").write_text(json.dumps(GSC_FIXTURE), encoding="utf-8")
    out = wr._collect_gsc()
    assert out["gsc"] == GSC_FIXTURE


def test_collect_gsc_is_missing_not_zero_when_report_file_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    out = wr._collect_gsc()
    assert isinstance(out["gsc"], wr.Missing)


def test_collect_gsc_is_missing_when_the_instrument_reports_itself_dead(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    d = tmp_path / "logs" / "gsc"
    d.mkdir(parents=True)
    (d / "report.json").write_text(
        json.dumps({"status": "instrument_dead", "why": "HttpError 403"}), encoding="utf-8")
    out = wr._collect_gsc()
    assert isinstance(out["gsc"], wr.Missing)
    assert "HttpError 403" in str(out["gsc"])


def test_render_prints_the_google_line_from_the_accepted_fixture():
    """Y53/Y57 wiring rule: this reads the line at the boundary the report
    actually sends — render() — not just that render_gsc() can build the
    string in isolation. A mutation that stops wiring gsc into render() or
    into collect() must turn this red."""
    report = {
        "since": "2026-09-01", "until": "2026-09-08",
        "human_sessions": 100, "raw_sessions": 120, "bot_direct": 20, "by_source": [],
        "clicks_by_source": {}, "clicks_by_partner": {}, "utm_content": {}, "videos_7d": 0,
        "median_views_7d": wr.Missing("median_views_7d", "no videos"),
        "retention_7d": wr.Missing("retention_7d", "no retention"),
        "gsc": GSC_FIXTURE,
    }
    text = wr.render(report)
    assert "google: 211 imp / 1 clicks / pos 40.6 / 89 pages / not-indexed 1/8" in text


def test_render_prints_the_google_line_even_when_gsc_was_never_collected():
    report = {
        "since": "2026-09-01", "until": "2026-09-08",
        "human_sessions": 100, "raw_sessions": 120, "bot_direct": 20, "by_source": [],
        "clicks_by_source": {}, "clicks_by_partner": {}, "utm_content": {}, "videos_7d": 0,
        "median_views_7d": wr.Missing("median_views_7d", "no videos"),
        "retention_7d": wr.Missing("retention_7d", "no retention"),
    }
    assert "google: n/a (not collected in this run)" in wr.render(report)


def test_a_dead_gsc_instrument_heads_the_report_with_action_required():
    header = wr.action_required(
        {"human_sessions": 609, "gsc": wr.Missing("gsc", "HttpError 403")})
    assert header.startswith("ACTION REQUIRED")
    assert "gsc" in header


def test_collect_wires_gsc_into_the_top_level_report(tmp_path, monkeypatch):
    """The render()-level wiring test above catches a mutation inside render();
    this one catches the other end — dropping `out.update(_collect_gsc())`
    from collect() itself (Y53's rule applied at both boundaries the value
    crosses, not just one)."""
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    monkeypatch.setattr(wr, "_ga4_client", lambda: object())
    d = tmp_path / "logs" / "gsc"
    d.mkdir(parents=True)
    (d / "report.json").write_text(json.dumps(GSC_FIXTURE), encoding="utf-8")
    report = wr.collect("2026-09-01", "2026-09-08")
    assert report["gsc"] == GSC_FIXTURE


# --- E-14: A2.4 hypothesis check section. _collect_a24() reads the latest
# logs/aeo/a24-check-*.json (written by scripts/a24-check.py) — same
# read-the-artefact rule as _collect_gsc()/_collect_bing(), no live hit.

A24_FIXTURE = {
    "ranked_pages_n": 1, "weeks": ["2026-W34", "2026-W35", "2026-W36", "2026-W37"],
    "instruments": {
        "bing_get_query_stats": {"status": "ok"},
        "aeo_verified_by_page": {"status": "ok"},
        "ga4_landing_chatgpt": {"status": "instrument_dead", "why": "GA4 down"},
    },
    "by_week": {
        "2026-W34": {"correlation_bing_vs_verified": {"n": 1, "r": "underpowered"},
                    "correlation_verified_vs_ga4_chatgpt": {"n": 0, "r": "instrument_dead"}},
        "2026-W35": {"correlation_bing_vs_verified": {"n": 1, "r": "underpowered"},
                    "correlation_verified_vs_ga4_chatgpt": {"n": 0, "r": "instrument_dead"}},
        "2026-W36": {"correlation_bing_vs_verified": {"n": 1, "r": "underpowered"},
                    "correlation_verified_vs_ga4_chatgpt": {"n": 0, "r": "instrument_dead"}},
        "2026-W37": {"correlation_bing_vs_verified": {"n": 1, "r": "underpowered"},
                    "correlation_verified_vs_ga4_chatgpt": {"n": 0, "r": "instrument_dead"}},
    },
}


def test_collect_a24_reads_the_latest_report_file(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    d = tmp_path / "logs" / "aeo"
    d.mkdir(parents=True)
    (d / "a24-check-2026-09-01.json").write_text('{"ranked_pages_n": 0}', encoding="utf-8")
    (d / "a24-check-2026-09-08.json").write_text(json.dumps(A24_FIXTURE), encoding="utf-8")
    out = wr._collect_a24()
    assert out["a24"] == A24_FIXTURE  # the newest file (sorted by name) wins


def test_collect_a24_is_missing_not_zero_when_never_run(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    out = wr._collect_a24()
    assert isinstance(out["a24"], wr.Missing)


def test_render_prints_the_a24_section_from_the_accepted_fixture():
    """Y53/Y57 wiring rule, same as the GSC test above: read render()'s own
    output, not render_a24() in isolation — a mutation dropping the a24 call
    from render() must turn this red."""
    report = {
        "since": "2026-09-01", "until": "2026-09-08",
        "human_sessions": 100, "raw_sessions": 120, "bot_direct": 20, "by_source": [],
        "clicks_by_source": {}, "clicks_by_partner": {}, "utm_content": {}, "videos_7d": 0,
        "median_views_7d": wr.Missing("median_views_7d", "no videos"),
        "retention_7d": wr.Missing("retention_7d", "no retention"),
        "a24": A24_FIXTURE,
    }
    text = wr.render(report)
    assert "A2.4 CHECK (read-only, no verdict — 1 ranked pages, weeks 2026-W34..2026-W37):" in text
    assert "ga4_landing_chatgpt: instrument_dead (GA4 down)" in text
    assert "2026-W35  r(bing,verified) n=1 r=underpowered | r(verified,chatgpt) n=0 r=instrument_dead" in text


def test_render_prints_a24_na_when_never_collected():
    report = {
        "since": "2026-09-01", "until": "2026-09-08",
        "human_sessions": 100, "raw_sessions": 120, "bot_direct": 20, "by_source": [],
        "clicks_by_source": {}, "clicks_by_partner": {}, "utm_content": {}, "videos_7d": 0,
        "median_views_7d": wr.Missing("median_views_7d", "no videos"),
        "retention_7d": wr.Missing("retention_7d", "no retention"),
    }
    assert "A2.4 CHECK: n/a (not collected in this run)" in wr.render(report)


def test_collect_wires_a24_into_the_top_level_report(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    monkeypatch.setattr(wr, "_ga4_client", lambda: object())
    d = tmp_path / "logs" / "aeo"
    d.mkdir(parents=True)
    (d / "a24-check-2026-09-08.json").write_text(json.dumps(A24_FIXTURE), encoding="utf-8")
    report = wr.collect("2026-09-01", "2026-09-08")
    assert report["a24"] == A24_FIXTURE


# --- E-15: YouTube traffic sources (insightTrafficSourceType) for H1 -------
#
# Runbook R5 is cancelled: the OAuth scope was already granted (measured live
# 2026-09-14). This section doubles as the H1 instrument's weekly EXT_URL
# snapshot, appended to logs/video/traffic-sources-history.jsonl on every run
# of this already-scheduled report — no second cron.

import sys as _sys  # noqa: E402
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "video-generator"))
from pipeline import analytics as _vg_analytics  # noqa: E402


def test_h1_traffic_baseline_is_a_frozen_literal():
    """Y49's rule: the baseline is a literal here, not recomputed from the
    function under test — a mutation to the constants must show up as a diff
    in THIS file, not just in weekly-report.py."""
    assert wr.H1_TRAFFIC_BASELINE_EXT_URL == 1
    assert wr.H1_TRAFFIC_BASELINE_WINDOW == ("2026-08-15", "2026-09-13")
    assert wr.H1_TRAFFIC_BASELINE_DECLARED_ON == "2026-09-14"


def test_collect_h1_traffic_returns_ext_url_and_appends_history(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    monkeypatch.setattr(_vg_analytics, "pull_traffic_sources",
                        lambda since, until: {"status": "ok",
                                              "data": {"SHORTS": 1089, "YT_SEARCH": 22,
                                                       "EXT_URL": 3}})
    out = wr._collect_h1_traffic("2026-09-08", "2026-09-14")
    assert out["h1_traffic"]["ext_url"] == 3
    assert out["h1_traffic"]["by_source"]["SHORTS"] == 1089

    lines = (tmp_path / "logs" / "video" / "traffic-sources-history.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    rec = json.loads(lines[-1])
    assert rec["status"] == "ok" and rec["data"]["EXT_URL"] == 3
    assert rec["since"] == "2026-09-08" and rec["until"] == "2026-09-14"


def test_collect_h1_traffic_is_missing_not_zero_when_instrument_dead(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    monkeypatch.setattr(_vg_analytics, "pull_traffic_sources",
                        lambda since, until: {"status": "instrument_dead",
                                              "error": "YouTube OAuth creds missing"})
    out = wr._collect_h1_traffic("2026-09-08", "2026-09-14")
    assert isinstance(out["h1_traffic"], wr.Missing)
    rec = json.loads((tmp_path / "logs" / "video" / "traffic-sources-history.jsonl")
                     .read_text(encoding="utf-8").strip())
    assert rec["status"] == "instrument_dead"  # dead week stays visible in the series


def test_collect_h1_traffic_dry_run_never_appends_to_history(tmp_path, monkeypatch):
    """A manual/test run is not the weekly cron — it must not pollute the
    series H1's baseline comparison reads."""
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    monkeypatch.setattr(_vg_analytics, "pull_traffic_sources",
                        lambda since, until: {"status": "ok", "data": {"EXT_URL": 3}})
    out = wr._collect_h1_traffic("2026-09-08", "2026-09-14", dry_run=True)
    assert out["h1_traffic"]["ext_url"] == 3
    assert not (tmp_path / "logs" / "video" / "traffic-sources-history.jsonl").exists()


def test_render_prints_ext_url_against_the_frozen_baseline():
    report = {
        "since": "2026-09-08", "until": "2026-09-14",
        "human_sessions": 100, "raw_sessions": 120, "bot_direct": 20, "by_source": [],
        "clicks_by_source": {}, "clicks_by_partner": {}, "utm_content": {}, "videos_7d": 0,
        "median_views_7d": wr.Missing("median_views_7d", "no videos"),
        "retention_7d": wr.Missing("retention_7d", "no retention"),
        "h1_traffic": {"since": "2026-09-08", "until": "2026-09-14",
                       "by_source": {"SHORTS": 1089, "EXT_URL": 3}, "ext_url": 3},
    }
    text = wr.render(report)
    assert ("EXT_URL 3  (baseline 1 over 2026-08-15..2026-09-13, "
           "declared 2026-09-14)") in text
    assert "by source SHORTS 1089, EXT_URL 3" in text


def test_render_prints_h1_traffic_na_when_never_collected():
    report = {
        "since": "2026-09-08", "until": "2026-09-14",
        "human_sessions": 100, "raw_sessions": 120, "bot_direct": 20, "by_source": [],
        "clicks_by_source": {}, "clicks_by_partner": {}, "utm_content": {}, "videos_7d": 0,
        "median_views_7d": wr.Missing("median_views_7d", "no videos"),
        "retention_7d": wr.Missing("retention_7d", "no retention"),
    }
    assert ("H1 TRAFFIC SOURCES (insightTrafficSourceType, YouTube Analytics v2):\n"
           "  n/a (not collected in this run)") in wr.render(report)


def test_collect_wires_h1_traffic_into_the_top_level_report(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    monkeypatch.setattr(wr, "_ga4_client", lambda: object())
    monkeypatch.setattr(_vg_analytics, "pull_traffic_sources",
                        lambda since, until: {"status": "ok", "data": {"EXT_URL": 5}})
    report = wr.collect("2026-09-08", "2026-09-14")
    assert report["h1_traffic"]["ext_url"] == 5


def test_collect_dry_run_flag_reaches_h1_traffic_and_skips_the_append(tmp_path, monkeypatch):
    """Wiring test, not just the helper (Y53's rule): main()'s --dry-run must
    reach through collect() into _collect_h1_traffic, not just be accepted by
    the helper directly."""
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    monkeypatch.setattr(wr, "_ga4_client", lambda: object())
    monkeypatch.setattr(_vg_analytics, "pull_traffic_sources",
                        lambda since, until: {"status": "ok", "data": {"EXT_URL": 5}})
    report = wr.collect("2026-09-08", "2026-09-14", dry_run=True)
    assert report["h1_traffic"]["ext_url"] == 5
    assert not (tmp_path / "logs" / "video" / "traffic-sources-history.jsonl").exists()


# --- E-20: `h1: videos N / median views / youtube->click` — the mode's
# pre-registered success line (FABLE_DEFYCARD_PART2.md D7: 4 weeks from first
# publication, median views >= 10 AND >= 1 youtube.com -> register_click). ---

def _write_h1_publish_log(tmp_path, rows):
    """rows: (ts, channel, topic_id, external_id, status, notes)"""
    import sqlite3
    db_dir = tmp_path / "video-generator" / "state"
    db_dir.mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(db_dir / "published.db")
    cx.execute("""CREATE TABLE publish_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, channel TEXT, topic_id INTEGER,
        transport TEXT, external_id TEXT, status TEXT, notes TEXT)""")
    for ts, channel, topic_id, external_id, status, notes in rows:
        cx.execute(
            "INSERT INTO publish_log(ts, channel, topic_id, transport, external_id, "
            "status, notes) VALUES (?,?,?,?,?,?,?)",
            (ts, channel, topic_id, "direct", external_id, status, notes))
    cx.commit()
    cx.close()


def test_collect_h1_mode_counts_videos_and_median_views_of_h1_only(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    _write_h1_publish_log(tmp_path, [
        ("2026-09-01T10:00:00+00:00", "youtube", 111, "vidA", "ok", "h1"),
        ("2026-09-03T10:00:00+00:00", "youtube", 222, "vidB", "ok", "h1"),
        ("2026-09-05T10:00:00+00:00", "youtube", 333, "vidC", "ok", ""),  # NOT h1
    ])
    monkeypatch.setattr(_vg_analytics, "channel_history", lambda platform="youtube", **k: {
        "vidA": {"views": 20, "retention": 0.5},
        "vidB": {"views": 40, "retention": 0.5},
        "vidC": {"views": 999, "retention": 0.9},
    })
    out = wr._collect_h1_mode(object(), "2026-09-01", "2026-09-08")
    assert out["h1_videos"] == 2, "the non-h1 row must not be counted"
    assert out["h1_median_views"] == 30, "vidC's 999 views must not pull the median"


def test_collect_h1_mode_median_views_is_missing_not_zero_before_any_video_is_scored(
        tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    _write_h1_publish_log(tmp_path, [])
    monkeypatch.setattr(_vg_analytics, "channel_history", lambda platform="youtube", **k: {})
    out = wr._collect_h1_mode(object(), "2026-09-01", "2026-09-08")
    assert out["h1_videos"] == 0
    assert isinstance(out["h1_median_views"], wr.Missing)


def test_collect_h1_mode_sums_youtube_source_register_clicks(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    _write_h1_publish_log(tmp_path, [])
    monkeypatch.setattr(_vg_analytics, "channel_history", lambda platform="youtube", **k: {})
    monkeypatch.setattr(wr, "_click_filter", lambda: "CLICK")
    monkeypatch.setattr(wr, "_youtube_source_filter", lambda: "YT_SRC")
    monkeypatch.setattr(wr, "_and", lambda *a: ("AND", a))

    def fake_run_report(client, dims, mets, since, until, dim_filter=None, limit=200):
        assert dim_filter == ("AND", ("CLICK", "YT_SRC"))
        return [(["youtube.com"], ["4"]), (["m.youtube.com"], ["1"])]

    monkeypatch.setattr(wr, "_run_report", fake_run_report)
    out = wr._collect_h1_mode(object(), "2026-09-01", "2026-09-08")
    assert out["h1_youtube_clicks"] == 5


def test_collect_h1_mode_click_count_is_missing_when_ga4_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    _write_h1_publish_log(tmp_path, [])
    monkeypatch.setattr(_vg_analytics, "channel_history", lambda platform="youtube", **k: {})

    def boom(*a, **k):
        raise RuntimeError("ga4 down")

    monkeypatch.setattr(wr, "_run_report", boom)
    out = wr._collect_h1_mode(object(), "2026-09-01", "2026-09-08")
    assert isinstance(out["h1_youtube_clicks"], wr.Missing)


_H1_RENDER_BASE = {
    "since": "2026-09-08", "until": "2026-09-14",
    "human_sessions": 100, "raw_sessions": 120, "bot_direct": 20, "by_source": [],
    "clicks_by_source": {}, "clicks_by_partner": {}, "utm_content": {}, "videos_7d": 0,
    "median_views_7d": wr.Missing("median_views_7d", "no videos"),
    "retention_7d": wr.Missing("retention_7d", "no retention"),
}


def test_render_prints_the_h1_line_with_all_three_figures():
    report = {**_H1_RENDER_BASE, "h1_videos": 3, "h1_median_views": 12,
             "h1_youtube_clicks": 2}
    assert "h1: videos 3 / median views 12 / youtube→click 2" in wr.render(report)


def test_render_prints_h1_na_when_never_collected():
    assert "h1: n/a (not collected in this run)" in wr.render(dict(_H1_RENDER_BASE))


def test_render_prints_action_required_shaped_h1_figures_as_missing_not_a_number():
    report = {**_H1_RENDER_BASE, "h1_videos": 0,
             "h1_median_views": wr.Missing("h1_median_views", "no H1 videos scored yet"),
             "h1_youtube_clicks": wr.Missing("h1_youtube_clicks", "AttributeError")}
    text = wr.render(report)
    assert "h1: videos 0 / median views n/a (no H1 videos scored yet) / " \
           "youtube→click n/a (AttributeError)" in text


def test_collect_wires_h1_mode_into_the_top_level_report(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    monkeypatch.setattr(wr, "_ga4_client", lambda: object())
    _write_h1_publish_log(tmp_path, [
        ("2026-09-01T10:00:00+00:00", "youtube", 1, "vidA", "ok", "h1")])
    monkeypatch.setattr(_vg_analytics, "channel_history",
                        lambda platform="youtube", **k: {"vidA": {"views": 15, "retention": 0.4}})
    report = wr.collect("2026-09-08", "2026-09-14")
    assert report["h1_videos"] == 1
    assert report["h1_median_views"] == 15
    # The fake client has no .run_report(), so the GA4-backed figure fails
    # closed to Missing rather than crashing collect() or vanishing silently.
    assert isinstance(report["h1_youtube_clicks"], wr.Missing)


# --- E-13: CTA funnel, ether.fi cabinet export, click->registration
# assumption (Fable 2026-09-14, FABLE_DEFYCARD_PART2.md wave 2) ------------
#
# cta_view has fired since 2026-08-18 and nothing read it until this task
# (line 38 of the plan). `_cta_view_filter()`/`_click_filter()` need the real
# google-analytics-data package, which is not installed in this sandbox
# (confirmed: `ModuleNotFoundError: No module named 'google'`) — every test
# here stubs those two filter builders to plain sentinels instead, exactly
# as the rest of this file always stubs `_ga4_client()` rather than reaching
# a real GA4 property.

def test_collect_cta_funnel_ranks_by_views_and_joins_clicks(monkeypatch):
    monkeypatch.setattr(wr, "_cta_view_filter", lambda: "CTA_VIEW")
    monkeypatch.setattr(wr, "_click_filter", lambda: "CLICK")

    def fake_run_report(client, dims, mets, since, until, dim_filter=None, limit=200):
        if dim_filter == "CTA_VIEW":
            return [(["/a/"], ["40"]), (["/b/"], ["33"]), (["/c/"], ["5"])]
        if dim_filter == "CLICK":
            return [(["/a/"], ["2"]), (["/c/"], ["1"])]
        raise AssertionError(dim_filter)

    monkeypatch.setattr(wr, "_run_report", fake_run_report)
    out = wr._collect_cta_funnel(object(), "2026-09-01", "2026-09-08")
    # /b/ has no register_click rows at all -> clicks defaults to 0, not Missing.
    assert out == [
        {"path": "/a/", "views": 40, "clicks": 2},
        {"path": "/b/", "views": 33, "clicks": 0},
        {"path": "/c/", "views": 5, "clicks": 1},
    ]


def test_collect_cta_funnel_keeps_only_the_top_ten_by_views(monkeypatch):
    monkeypatch.setattr(wr, "_cta_view_filter", lambda: "CTA_VIEW")
    monkeypatch.setattr(wr, "_click_filter", lambda: "CLICK")
    views = [([f"/p{i}/"], [str(i)]) for i in range(1, 16)]  # 15 pages, views 1..15

    def fake_run_report(client, dims, mets, since, until, dim_filter=None, limit=200):
        return views if dim_filter == "CTA_VIEW" else []

    monkeypatch.setattr(wr, "_run_report", fake_run_report)
    out = wr._collect_cta_funnel(object(), "2026-09-01", "2026-09-08")
    assert len(out) == 10
    assert [row["path"] for row in out] == [f"/p{i}/" for i in range(15, 5, -1)]


_CABINET_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "etherfi-cabinet-2026-09-24.xlsx"

_PERF_HEADER = ["Date", "Signups", "Cardholders", "Spenders", "Total"]
_REWARDS_HEADER = ["Date", "New Member Bonus", "High Spender Bonus",
                   "Physical Card Commission", "Card Fee Commission",
                   "Spend Rev Share", "2nd Degree Share", "Liquid Yield Rev Share",
                   "Staked Yield Rev Share", "Total"]
_PAYOUTS_HEADER = ["Amount (USD)", "Earnings for", "Paid Date (UTC)",
                   "Paid Time (UTC)", "Tx Hash"]


def _xlsx_cell(ref, value):
    if isinstance(value, str):
        return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'
    return f'<c r="{ref}"><v>{value}</v></c>'


def _xlsx_sheet_xml(rows):
    cols = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    body = []
    for i, row in enumerate(rows, start=1):
        cells = "".join(_xlsx_cell(f"{cols[j]}{i}", v) for j, v in enumerate(row))
        body.append(f'<row r="{i}">{cells}</row>')
    return ('<?xml version="1.0"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(body)}</sheetData></worksheet>')


def _write_cabinet_xlsx(tmp_path, name, sheets, mtime_days_old=1):
    """Minimal synthetic cabinet export, built with the same zipfile module
    the reader under test uses — inline strings, no sharedStrings, deliberately
    the OTHER cell shape from the real fixture (which uses sharedStrings) so
    both paths in `_read_xlsx` get exercised somewhere in the suite."""
    import os
    import time

    d = tmp_path / "state" / "etherfi-cabinet"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    sheet_names = list(sheets.keys())
    sheets_xml = "".join(
        f'<sheet name="{n}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
        for i, n in enumerate(sheet_names))
    workbook_xml = (
        '<?xml version="1.0"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{sheets_xml}</sheets></workbook>')
    rels_xml = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{i + 1}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i + 1}.xml"/>'
            for i in range(len(sheet_names)))
        + "</Relationships>")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("xl/workbook.xml", workbook_xml)
        z.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        for i, n in enumerate(sheet_names):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _xlsx_sheet_xml(sheets[n]))
    ts = time.time() - mtime_days_old * 86400
    os.utime(p, (ts, ts))
    return p


def _minimal_cabinet_sheets(*, perf_rows, rewards_rows, payout_rows):
    return {
        wr.CABINET_SHEET_REWARDS: [_REWARDS_HEADER] + rewards_rows,
        wr.CABINET_SHEET_PERF: [_PERF_HEADER] + perf_rows,
        wr.CABINET_SHEET_PAYOUTS: [_PAYOUTS_HEADER] + payout_rows,
    }


def _install_cabinet_fixture(tmp_path, mtime_days_old=1):
    import os
    import time

    d = tmp_path / "state" / "etherfi-cabinet"
    d.mkdir(parents=True, exist_ok=True)
    dest = d / _CABINET_FIXTURE.name
    dest.write_bytes(_CABINET_FIXTURE.read_bytes())
    ts = time.time() - mtime_days_old * 86400
    os.utime(dest, (ts, ts))
    return dest


def test_collect_cabinet_is_missing_when_no_export_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    out = wr._collect_cabinet()
    assert isinstance(out["cabinet"], wr.Missing)
    assert "Runbook R7" in str(out["cabinet"])


def test_collect_cabinet_reads_the_real_export_fixture_g44(tmp_path, monkeypatch):
    """G44 (T-0142-12, ruling-3): every literal below is worked out BY HAND
    against the real captured export (see the task's KNOWLEDGE entry) — not
    re-derived from the code under test (Y49)."""
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    _install_cabinet_fixture(tmp_path)
    out = wr._collect_cabinet()
    cab = out["cabinet"]
    assert cab["signups"] == 6
    assert cab["cardholders"] == 0
    assert cab["spenders"] == 1
    assert cab["qualified"] == 1
    assert cab["last_qualified"] == "2026-09-14"
    assert cab["last_signup"] == "2026-09-20"
    assert cab["last_spender"] == "2026-08-23"
    assert cab["earned_usd"] == 15.17
    assert cab["paid_usd"] == 5.03
    assert cab["payouts"] == 1
    assert cab["last_paid"] == "2026-09-16"
    assert cab["paid_for"] == "August, 2026"
    assert cab["file"] == "etherfi-cabinet-2026-09-24.xlsx"


def test_collect_cabinet_qualified_reads_new_member_bonus_not_spenders(tmp_path, monkeypatch):
    """Mutation (ruling-3): flip the real export's New Member Bonus cell
    (sheet1 B4, 10 -> 0) directly in the zip bytes. qualified must drop to 0
    while spenders (a different sheet/column entirely) stays 1 — proves
    `qualified` is sourced from Affiliate Rewards/New Member Bonus, not from
    Referral Performance/Spenders."""
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    d = tmp_path / "state" / "etherfi-cabinet"
    d.mkdir(parents=True)
    with zipfile.ZipFile(_CABINET_FIXTURE) as zin:
        data = {n: zin.read(n) for n in zin.namelist()}
    sheet1 = data["xl/worksheets/sheet1.xml"].decode()
    assert '<c r="B4" s="3"><v>10</v></c>' in sheet1
    data["xl/worksheets/sheet1.xml"] = sheet1.replace(
        '<c r="B4" s="3"><v>10</v></c>', '<c r="B4" s="3"><v>0</v></c>').encode()
    dest = d / "mutated.xlsx"
    with zipfile.ZipFile(dest, "w") as zout:
        for n, content in data.items():
            zout.writestr(n, content)
    import os
    import time
    ts = time.time() - 86400
    os.utime(dest, (ts, ts))
    out = wr._collect_cabinet()
    cab = out["cabinet"]
    assert cab["qualified"] == 0
    assert cab["spenders"] == 1


def test_collect_cabinet_renamed_sheet_is_missing_not_a_silent_zero(tmp_path, monkeypatch):
    """Mutation (ruling-3): rename a required sheet in the real export.
    Must fail closed to Missing — the old CSV reader's `continue` on an
    unrecognised column was a silent zero, this must not reproduce it."""
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    d = tmp_path / "state" / "etherfi-cabinet"
    d.mkdir(parents=True)
    with zipfile.ZipFile(_CABINET_FIXTURE) as zin:
        data = {n: zin.read(n) for n in zin.namelist()}
    wbxml = data["xl/workbook.xml"].decode()
    assert 'name="Referral Performance"' in wbxml
    data["xl/workbook.xml"] = wbxml.replace(
        'name="Referral Performance"', 'name="Referral Perf"').encode()
    dest = d / "renamed.xlsx"
    with zipfile.ZipFile(dest, "w") as zout:
        for n, content in data.items():
            zout.writestr(n, content)
    import os
    import time
    ts = time.time() - 86400
    os.utime(dest, (ts, ts))
    out = wr._collect_cabinet()
    assert isinstance(out["cabinet"], wr.Missing)
    assert "Referral Performance" in str(out["cabinet"])
    text = wr.render({
        "since": "2026-09-01", "until": "2026-09-08",
        "human_sessions": 100, "raw_sessions": 120, "bot_direct": 20, "by_source": [],
        "clicks_by_source": {}, "clicks_by_partner": {},
        "utm_content": {}, "videos_7d": 0,
        "median_views_7d": wr.Missing("median_views_7d", "no videos"),
        "retention_7d": wr.Missing("retention_7d", "no retention"),
        "cabinet": out["cabinet"],
    })
    assert "signups 0" not in text


def test_collect_cabinet_picks_the_newest_file(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    old = _minimal_cabinet_sheets(
        perf_rows=[[46231.7, 99, 0, 0, 99]],
        rewards_rows=[[46257.7, 0, 0, 0, 0, 0, 0, 0, 0, 0]],
        payout_rows=[[1.0, "July, 2026", 46250, "00:00:00", "0xold"]],
    )
    new = _minimal_cabinet_sheets(
        perf_rows=[[46231.7, 6, 0, 0, 6]],
        rewards_rows=[[46257.7, 0, 0, 0, 0, 0, 0, 0, 0, 0]],
        payout_rows=[[1.0, "August, 2026", 46281, "00:00:00", "0xnew"]],
    )
    _write_cabinet_xlsx(tmp_path, "export-old.xlsx", old, mtime_days_old=10)
    _write_cabinet_xlsx(tmp_path, "export-new.xlsx", new, mtime_days_old=1)
    out = wr._collect_cabinet()
    assert out["cabinet"]["file"] == "export-new.xlsx"
    assert out["cabinet"]["signups"] == 6


def test_collect_cabinet_is_missing_when_export_older_than_35_days(tmp_path, monkeypatch):
    """35 is the literal from the task text (Y49): this must not read
    wr.CABINET_STALE_DAYS, or a mutation to that constant stays invisible."""
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    sheets = _minimal_cabinet_sheets(
        perf_rows=[[46231.7, 6, 0, 0, 6]],
        rewards_rows=[[46257.7, 0, 0, 0, 0, 0, 0, 0, 0, 0]],
        payout_rows=[[1.0, "July, 2026", 46250, "00:00:00", "0x1"]],
    )
    _write_cabinet_xlsx(tmp_path, "export-stale.xlsx", sheets, mtime_days_old=36)
    out = wr._collect_cabinet()
    assert isinstance(out["cabinet"], wr.Missing)
    assert "35 days" in str(out["cabinet"])


def test_collect_cabinet_is_not_missing_at_exactly_35_days(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    sheets = _minimal_cabinet_sheets(
        perf_rows=[[46231.7, 6, 0, 0, 6]],
        rewards_rows=[[46257.7, 0, 0, 0, 0, 0, 0, 0, 0, 0]],
        payout_rows=[[1.0, "July, 2026", 46250, "00:00:00", "0x1"]],
    )
    _write_cabinet_xlsx(tmp_path, "export-edge.xlsx", sheets, mtime_days_old=35)
    out = wr._collect_cabinet()
    assert not isinstance(out["cabinet"], wr.Missing)


def test_render_always_prints_cta_cabinet_assumption_lines_even_when_not_collected():
    """Mutation target (Y57): the lines must not vanish when the keys are
    simply absent from the report, and must not vanish if a future edit
    computes the strings but forgets to append them to L."""
    report = {
        "since": "2026-09-01", "until": "2026-09-08",
        "human_sessions": 100, "raw_sessions": 120, "bot_direct": 20, "by_source": [],
        "clicks_by_source": {}, "clicks_by_partner": {}, "utm_content": {}, "videos_7d": 0,
        "median_views_7d": wr.Missing("median_views_7d", "no videos"),
        "retention_7d": wr.Missing("retention_7d", "no retention"),
    }
    text = wr.render(report)
    assert "CTA FUNNEL (views -> clicks, top 10 by cta_view):" in text
    assert "  n/a (not collected in this run)" in text
    assert "CABINET (ether.fi affiliate export):" in text
    assert "ASSUMPTION (B6): n/a (cabinet not collected)" in text


def test_render_prints_cabinet_and_b6_assumption_when_present():
    report = {
        "since": "2026-09-01", "until": "2026-09-08",
        "human_sessions": 100, "raw_sessions": 120, "bot_direct": 20, "by_source": [],
        "clicks_by_source": {}, "clicks_by_partner": {"etherfi": 40},
        "utm_content": {}, "videos_7d": 0,
        "median_views_7d": wr.Missing("median_views_7d", "no videos"),
        "retention_7d": wr.Missing("retention_7d", "no retention"),
        "cta_funnel": [{"path": "/reviews/ether-fi-cash-review/", "views": 33, "clicks": 1}],
        "cabinet": {
            "signups": 6, "cardholders": 0, "spenders": 1, "qualified": 1,
            "earned_usd": 15.17, "paid_usd": 5.03, "payouts": 1,
            "last_signup": "2026-09-20", "last_spender": "2026-08-23",
            "last_qualified": "2026-09-14", "last_paid": "2026-09-16",
            "paid_for": "August, 2026", "export_date": "2026-09-24",
            "file": "etherfi-cabinet-2026-09-24.xlsx",
        },
        "cabinet_clicks_since_launch": 40,
    }
    text = wr.render(report)
    assert "/reviews/ether-fi-cash-review/               33 views ->   1 clicks" in text
    assert "qualified 1 (New Member Bonus rows, last 2026-09-14)" in text
    # 40 clicks / 10 = 4.0 expected, worked out by hand (Y49), vs the 6 signups on file;
    # 6 signups / 3 = 2.0 expected qualified vs the 1 actually qualified.
    assert "ASSUMPTION (B6): 40 clicks 2026-05-06.." in text
    assert "4.0 expected vs 6 signups; 6 / 3 = 2.0 expected qualified vs 1" in text


def test_cabinet_missing_heads_the_report_with_action_required_not_a_zero():
    header = wr.action_required(
        {"human_sessions": 100,
         "cabinet": wr.Missing("cabinet", "no cabinet export in state/etherfi-cabinet/")})
    assert header.startswith("ACTION REQUIRED")
    assert "cabinet" in header
    text = wr.render({
        "since": "2026-09-01", "until": "2026-09-08",
        "human_sessions": 100, "raw_sessions": 120, "bot_direct": 20, "by_source": [],
        "clicks_by_source": {}, "clicks_by_partner": {},
        "utm_content": {}, "videos_7d": 0,
        "median_views_7d": wr.Missing("median_views_7d", "no videos"),
        "retention_7d": wr.Missing("retention_7d", "no retention"),
        "cabinet": wr.Missing("cabinet", "no cabinet export in state/etherfi-cabinet/"),
    })
    assert "signups 0" not in text
    assert "n/a (no cabinet export in state/etherfi-cabinet/)" in text


def test_collect_cabinet_clicks_queries_the_same_window_as_the_cabinet_export(tmp_path, monkeypatch):
    """ruling-2: B6 compares clicks to signups over the SAME window (tracker
    launch -> cabinet export date). Proven through collect() with the actual
    since/until args captured at the GA4 boundary and compared to literals —
    reading wr.CABINET_CLICKS_SINCE or cab['export_date'] back out of the
    module under test would let both of these go unnoticed:
    - CABINET_CLICKS_SINCE swapped for the report window's own `since`
    - cab['export_date'] swapped for the report window's own `until`
    """
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    monkeypatch.setattr(wr, "_ga4_client", lambda: object())
    _install_cabinet_fixture(tmp_path, mtime_days_old=0)  # export_date == today
    calls = []

    def fake_run_report(client, dims, mets, since, until, dim_filter=None, limit=200):
        if dims == [] and mets == ["eventCount"]:
            calls.append((since, until))
            return [([], ["10"])]
        return []

    monkeypatch.setattr(wr, "_run_report", fake_run_report)
    report = wr.collect("2026-09-01", "2026-09-08")
    assert report["cabinet_clicks_since_launch"] == 10
    assert calls == [("2026-05-06", "2026-09-24")]


def test_collect_cabinet_clicks_is_missing_with_cabinets_own_reason_when_cabinet_is_missing(
        tmp_path, monkeypatch):
    """ruling-2: when the cabinet itself is Missing, `cabinet_clicks_since_launch`
    must still be a key in the report — carrying the cabinet's own reason — not
    simply absent (a silent drop `_unmeasured()` would never surface)."""
    monkeypatch.setattr(wr, "ROOT", tmp_path)  # no export dropped -> cabinet Missing
    monkeypatch.setattr(wr, "_ga4_client", lambda: object())
    monkeypatch.setattr(wr, "_run_report", lambda *a, **k: [])
    report = wr.collect("2026-09-01", "2026-09-08")
    assert isinstance(report["cabinet"], wr.Missing)
    clicks = report["cabinet_clicks_since_launch"]
    assert isinstance(clicks, wr.Missing)
    assert clicks.why == report["cabinet"].why
    assert "cabinet_clicks_since_launch: " in "\n".join(wr._unmeasured(report))


def test_collect_cabinet_clicks_is_missing_with_cabinets_own_reason_when_ga4_client_fails(
        tmp_path, monkeypatch):
    """ruling-3: the GA4-client-init-failure early return in collect() is a
    sister branch of the one the test above covers — it must not leave
    `cabinet_clicks_since_launch` absent either, and cabinet's own reason
    (not the GA4 failure) must win when the cabinet itself is also Missing."""
    monkeypatch.setattr(wr, "ROOT", tmp_path)  # no export dropped -> cabinet Missing

    def raise_client():
        raise RuntimeError("boom")

    monkeypatch.setattr(wr, "_ga4_client", raise_client)
    report = wr.collect("2026-09-01", "2026-09-08")
    assert isinstance(report["cabinet"], wr.Missing)
    clicks = report["cabinet_clicks_since_launch"]
    assert isinstance(clicks, wr.Missing)
    assert clicks.why == report["cabinet"].why
    assert "cabinet_clicks_since_launch: " in "\n".join(wr._unmeasured(report))


def test_collect_wires_cabinet_and_clicks_since_launch_into_the_top_level_report(tmp_path, monkeypatch):
    """Same boundary the gsc wiring test above checks: collect() must
    actually call and store both, not just define them."""
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    monkeypatch.setattr(wr, "_ga4_client", lambda: object())
    _install_cabinet_fixture(tmp_path)
    report = wr.collect("2026-09-01", "2026-09-08")
    assert report["cabinet"]["signups"] == 6
    assert report["cabinet"]["file"] == "etherfi-cabinet-2026-09-24.xlsx"
    # The fake client has no .run_report(), so both cta_funnel and the B6
    # click count must fail closed to Missing rather than crash collect() or
    # silently vanish from the report.
    assert isinstance(report["cta_funnel"], wr.Missing)
    assert isinstance(report["cabinet_clicks_since_launch"], wr.Missing)


# --- T-0138 (ruling-1, П4): _ga4_client() must name a wrong interpreter,
# not report a bare ImportError indistinguishable from a dead GA4 -----------

def test_ga4_client_missing_sdk_raises_typed_error_naming_the_interpreter(monkeypatch):
    monkeypatch.setitem(_sys.modules, "google.analytics.data_v1beta", None)
    with pytest.raises(wr.Ga4SdkMissing) as exc_info:
        wr._ga4_client()
    assert _sys.executable in str(exc_info.value)


# --- T-0142-10 (ruling-2 + ruling-3, Q9.3): $0 COMMISSION carries its cause,
# and ACTION REQUIRED names only the partner the operator can actually fix ---

def test_real_affiliates_yaml_flags_cryptocom_as_not_obtainable():
    """cryptocom and cryptocom_eu share CRYPTOCOM_REF_URL and are both blocked
    by the same jurisdiction cause (ruling-3, Q9.3) — both must carry the flag
    or the header regains noise through whichever entry is missed."""
    import yaml
    aff = yaml.safe_load(
        (wr.ROOT / "config" / "affiliates.yaml").read_text(encoding="utf-8")
    )["affiliates"]
    assert aff["cryptocom"]["referral_obtainable"] is False
    assert aff["cryptocom_eu"]["referral_obtainable"] is False
    assert "cannot join the Crypto.com programme" in aff["cryptocom"]["unmonetized_note"]


def test_unmonetized_partners_cryptocom_line_carries_its_cause():
    """Literal from ruling-3 Q9.3's own canonical line: `$0 COMMISSION
    cryptocom:` must say WHY, not just that the var is empty."""
    lines = wr.unmonetized_partners()
    cryptocom_line = next(l for l in lines if l.startswith("cryptocom:"))
    assert "cannot join" in cryptocom_line


def test_real_report_action_required_never_names_cryptocom():
    """CRYPTOCOM_REF_URL is confirmed empty in this repo's own .env (the
    operator cannot register at all) — so today's real referral_gaps() must
    never surface it, no matter what plasma's own fill state is."""
    header = wr.action_required({"referral_gaps": wr.referral_gaps()})
    assert header is None or "CRYPTOCOM_REF_URL" not in header


def _write_referral_gaps_fixture(tmp_path, cryptocom_obtainable_false: bool) -> None:
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    flag_line = "    referral_obtainable: false\n" if cryptocom_obtainable_false else ""
    (tmp_path / "config" / "affiliates.yaml").write_text(
        "affiliates:\n"
        "  cryptocom:\n"
        "    url: \"${CRYPTOCOM_REF_URL}\"\n"
        "    url_fallback: \"https://crypto.com/us/cards\"\n"
        + flag_line +
        "  plasma:\n"
        "    url: \"${PLASMA_REF_URL}\"\n"
        "    url_fallback: \"https://www.plasma.org/personal\"\n",
        encoding="utf-8")
    (tmp_path / "config" / "availability.yaml").write_text(
        "etherfi:\n  fallback_partner: cryptocom\n", encoding="utf-8")
    corpus = tmp_path / "seo-site" / "src" / "content" / "pseo"
    corpus.mkdir(parents=True)
    (corpus / "texas.md").write_text(
        '---\nprimary_partner: "etherfi"\n---\n'
        "Second card: [Plasma One](<<AFFILIATE_URL:plasma>>)\n", encoding="utf-8")


def test_referral_gaps_excludes_the_partner_flagged_not_obtainable(tmp_path, monkeypatch):
    """The mechanism, proven on a controlled fixture (not the live .env,
    which may already have PLASMA_REF_URL filled by the time this runs):
    cryptocom is referenced (it's the fallback_partner) but flagged, so it
    is excluded; plasma is referenced and unflagged, so it raises."""
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    monkeypatch.delenv("CRYPTOCOM_REF_URL", raising=False)
    monkeypatch.delenv("PLASMA_REF_URL", raising=False)
    _write_referral_gaps_fixture(tmp_path, cryptocom_obtainable_false=True)

    gaps = wr.referral_gaps()
    assert "cryptocom" not in gaps
    assert "plasma" in gaps
    assert "PLASMA_REF_URL" in gaps["plasma"].why

    header = wr.action_required({"referral_gaps": gaps})
    assert header is not None
    assert "PLASMA_REF_URL" in header
    assert "CRYPTOCOM_REF_URL" not in header


def test_referral_gaps_mutation_removing_the_flag_reintroduces_cryptocom_noise(
        tmp_path, monkeypatch):
    """Y49 canary, the exact mutation ruling-3 names: drop
    `referral_obtainable: false` and watch CRYPTOCOM_REF_URL noise come back
    into the header — proves the flag, not something else, is what gates it."""
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    monkeypatch.delenv("CRYPTOCOM_REF_URL", raising=False)
    monkeypatch.delenv("PLASMA_REF_URL", raising=False)
    _write_referral_gaps_fixture(tmp_path, cryptocom_obtainable_false=False)

    gaps = wr.referral_gaps()
    assert "cryptocom" in gaps
    header = wr.action_required({"referral_gaps": gaps})
    assert "CRYPTOCOM_REF_URL" in header


def test_collect_wires_referral_gaps_into_the_top_level_report(tmp_path, monkeypatch):
    """Y53: a correct function nobody calls from collect() is the bug —
    reads the boundary collect() actually populates, not the helper alone."""
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    monkeypatch.setattr(wr, "_ga4_client", lambda: object())
    monkeypatch.delenv("CRYPTOCOM_REF_URL", raising=False)
    monkeypatch.delenv("PLASMA_REF_URL", raising=False)
    _write_referral_gaps_fixture(tmp_path, cryptocom_obtainable_false=True)
    report = wr.collect("2026-09-01", "2026-09-08")
    assert "plasma" in report["referral_gaps"]
    assert "cryptocom" not in report["referral_gaps"]


# --- T-CCH-INTEL-429-1005: standing intel gaps head the Monday report ---------

def _intel_cfg(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "intel_sources.yaml").write_text(
        "sources:\n"
        "  - id: src-fresh\n    last_ok: \"2026-10-05\"\n"
        "  - id: etherfi-affiliates-page\n    last_ok: \"2026-09-14\"\n"
        "  - id: etherfi-blog-feed\n    last_ok: null\n", encoding="utf-8")
    (tmp_path / "logs" / "intel").mkdir(parents=True)
    (tmp_path / "logs" / "intel" / "events.jsonl").write_text(
        json.dumps({"source_id": "etherfi-affiliates-page", "kind": "fetch_failed",
                    "diff_summary": "HTTPError: HTTP Error 429: Too Many Requests"}) + "\n",
        encoding="utf-8")


def test_collect_intel_flags_unread_sources_with_age_cause_and_action(tmp_path, monkeypatch):
    from datetime import date
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    _intel_cfg(tmp_path)
    out = wr._collect_intel(today=date(2026, 10, 5))
    missing = [k for k, v in out.items() if isinstance(v, wr.Missing)]
    assert sorted(missing) == ["etherfi-affiliates-page", "etherfi-blog-feed"]
    assert "unread since 2026-09-14 (21 d)" in str(out["etherfi-affiliates-page"])
    assert "429" in str(out["etherfi-affiliates-page"])
    assert "--from-file etherfi-affiliates-page" in str(out["etherfi-affiliates-page"])
    assert "unread since never" in str(out["etherfi-blog-feed"])
    assert "intel.etherfi-affiliates-page" in wr.action_required({"intel": out})


def test_render_prints_intel_line_present_and_absent(tmp_path, monkeypatch):
    from datetime import date
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    _intel_cfg(tmp_path)
    report = {
        "since": "2026-09-01", "until": "2026-09-08",
        "human_sessions": 100, "raw_sessions": 120, "bot_direct": 20, "by_source": [],
        "clicks_by_source": {}, "clicks_by_partner": {}, "utm_content": {}, "videos_7d": 0,
        "median_views_7d": wr.Missing("median_views_7d", "no videos"),
        "retention_7d": wr.Missing("retention_7d", "no retention"),
    }
    assert "intel: n/a (not collected in this run)" in wr.render(report)
    report["intel"] = wr._collect_intel(today=date(2026, 10, 5))
    assert ("intel: 3 sources, 1 read ≤7 d, 2 unread "
            "(etherfi-affiliates-page 21 d, etherfi-blog-feed never)") in wr.render(report)


# ---- CCH-SEO-01: index-status + crawl supply (literals, Y49) ----
import os as _os
from datetime import date as _date, timedelta as _td


def _idx_urls():
    u = {}
    def add(prefix, n_ok, n_bad):
        for i in range(n_ok):
            u[f"https://defycard.com/{prefix}/ok{i}/"] = {"coverage": "Submitted and indexed", "verdict": "PASS"}
        for i in range(n_bad):
            u[f"https://defycard.com/{prefix}/bad{i}/"] = {"coverage": "Crawled - currently not indexed", "verdict": "NEUTRAL"}
    add("faq", 350, 228)       # content 350/578
    add("videos", 40, 211)     # videos 40/251
    for i in range(15):        # other: 15 indexed, 36 not, 2 errors = 53
        u[f"https://defycard.com/o{i}/"] = {"coverage": "Submitted and indexed", "verdict": "PASS"}
    for i in range(36):
        u[f"https://defycard.com/n{i}/"] = {"coverage": "Discovered - currently not indexed", "verdict": "NEUTRAL"}
    for i in range(2):
        u[f"https://defycard.com/e{i}/"] = {"coverage": "instrument_error", "why": "x"}
    return u


def _gsc_index_root(tmp_path, run):
    d = tmp_path / "logs" / "gsc"
    d.mkdir(parents=True)
    (d / "index-status.json").write_text(json.dumps(
        {"run": str(run), "inspected": 882, "urls": _idx_urls()}), encoding="utf-8")


def test_render_prints_google_index_line(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    _gsc_index_root(tmp_path, "2026-10-20")
    class D(_date):
        @classmethod
        def today(cls): return cls(2026, 10, 21)
    monkeypatch.setattr(wr, "date", D)
    out = wr._collect_gsc_index()
    assert wr.render_gsc_index(out) == (
        "google index (2026-10-20): indexed 405/882 · content 350/578 · videos 40/251 · errors 2")
    report = {"since": "2026-10-13", "until": "2026-10-20", "human_sessions": 1, "raw_sessions": 1,
              "bot_direct": 0, "by_source": [], "clicks_by_source": {}, "clicks_by_partner": {},
              "utm_content": {}, "videos_7d": 0,
              "median_views_7d": wr.Missing("m", "x"), "retention_7d": wr.Missing("r", "x"), **out}
    assert "google index (2026-10-20): indexed 405/882" in wr.render(report)


def test_stale_index_status_is_missing_with_age(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    _gsc_index_root(tmp_path, _date.today() - _td(days=11))
    m = wr._collect_gsc_index()["gsc_index"]
    assert isinstance(m, wr.Missing) and "11 d old" in str(m)


def test_crawl_supply_line_literal(tmp_path):
    (tmp_path / "logs/aeo").mkdir(parents=True)
    (tmp_path / "video-generator/state").mkdir(parents=True)
    (tmp_path / "seo-site/dist/faq/a").mkdir(parents=True)
    (tmp_path / "seo-site/dist/faq/b").mkdir(parents=True)
    (tmp_path / "seo-site/dist/faq/a/index.html").write_text("x")
    (tmp_path / "seo-site/dist/faq/b/index.html").write_text("x")
    a = tmp_path / "logs/aeo/indexnow-state.json"
    a.write_text(json.dumps({"u1": "h", "u2": "h", "u3": "h"}))
    b = tmp_path / "video-generator/state/bing-submitted.json"
    b.write_text(json.dumps(["u1"]))
    _os.utime(a, (1792324800, 1792324800))   # 2026-10-18 12:00 UTC
    _os.utime(b, (1792065600, 1792065600))   # 2026-10-15 12:00 UTC
    assert wr.render_crawl_supply(tmp_path) == (
        "crawl supply: indexnow 2026-10-18 (3 urls hashed) · "
        "bing-submit 2026-10-15 (1/2) · crawler-404s n/a (CCH-SEO-02 not run)")
    b.unlink()
    assert "bing-submit n/a (bing-submitted.json missing)" in wr.render_crawl_supply(tmp_path)


def test_render_appends_crawl_supply(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    report = {"since": "2026-10-13", "until": "2026-10-20", "human_sessions": 1, "raw_sessions": 1,
              "bot_direct": 0, "by_source": [], "clicks_by_source": {}, "clicks_by_partner": {},
              "utm_content": {}, "videos_7d": 0,
              "median_views_7d": wr.Missing("m", "x"), "retention_7d": wr.Missing("r", "x")}
    assert "crawl supply: indexnow n/a (indexnow-state.json missing)" in wr.render(report)


# --- CCH-PIN-02: the pinterest line (boundary test, Y53) -------------------
def _pin_root(tmp_path, runs):
    (tmp_path / "seo-site/src/data").mkdir(parents=True)
    (tmp_path / "seo-site/src/data/pinterest-manifest.json").write_text('{"a": {}, "b": {}}')
    (tmp_path / "logs/pinterest").mkdir(parents=True)
    (tmp_path / "logs/pinterest/feed.jsonl").write_text(
        "\n".join(json.dumps({"event": "run", **r}) for r in runs))
    return tmp_path


def test_collect_pinterest_counts_only_the_last_7_days(tmp_path):
    from datetime import date
    root = _pin_root(tmp_path, [
        {"date": "2026-10-01", "candidates_new": 9, "candidates_backfill": 9, "image_failed": 9},
        {"date": "2026-10-18", "candidates_new": 1, "candidates_backfill": 3, "image_failed": 2}])
    out = wr._collect_pinterest(root, date(2026, 10, 19))["pinterest"]
    assert out == {"manifest": 2, "new": 1, "backfill": 3, "image_failed": 2}


def test_collect_pinterest_missing_files_are_missing_not_zero(tmp_path):
    assert isinstance(wr._collect_pinterest(tmp_path)["pinterest"], wr.Missing)


def test_pinterest_line_is_in_the_transmitted_text(tmp_path, monkeypatch):
    import urllib.parse
    sent = []

    class R:
        def read(self):
            return b""
    monkeypatch.setattr(wr.urllib.request, "urlopen",
                        lambda url, data=None, timeout=None: sent.append(data) or R())
    monkeypatch.setenv("TG_BOT_TOKEN", "t")
    monkeypatch.setenv("TG_CHAT_ID", "c")
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    report = {
        "since": "2026-10-12", "until": "2026-10-19",
        "human_sessions": 100, "raw_sessions": 120, "bot_direct": 20, "by_source": [],
        "clicks_by_source": {}, "clicks_by_partner": {}, "utm_content": {}, "videos_7d": 0,
        "median_views_7d": wr.Missing("median_views_7d", "no videos"),
        "retention_7d": wr.Missing("retention_7d", "no retention"),
        "pinterest": {"manifest": 3, "new": 0, "backfill": 3, "image_failed": 0},
    }
    wr.send(report, wr.render(report))
    body = "".join(urllib.parse.unquote_plus(d.decode()) for d in sent)
    assert "pinterest: manifest 3 (+0 new, +3 backfill, 7d) image_failed 0" in body
