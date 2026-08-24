"""G13 (Y50 P0-2) — the report may only speak in human sessions, and may
never send a number it did not measure.

THE FORMULA IS A LITERAL HERE (lesson Y49). If this test computed the
expectation by calling `human_sessions()`, then changing that function would
change the expectation too and the gate would stay green through the very
mutation it exists to catch. The numbers below are worked out by hand.
"""
from __future__ import annotations

import importlib.util
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
