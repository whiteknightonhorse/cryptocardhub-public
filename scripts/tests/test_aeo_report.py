"""G18 (Y51 AEO-2) — the citability instrument counts assistants, not scanners.

THE LAWS ARE LITERALS HERE. Nothing below imports a constant from the code it
is policing: the user-agent list, the status code, the URL shape and the
declared thresholds are all typed out again by hand. A test that reads its
expectation out of the module under test moves with every mutation of that
module and can never go red (lesson Y49).

Why the filters matter: the top 404 producers on this host arrive wearing
`OAI-SearchBot` and `PerplexityBot` and ask for /.env, /.aws/credentials and
/id_rsa. They are vulnerability scanners. Without the status-200 and
article-shape filters they land in the one number this whole programme is
judged on.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


aeo = _load("aeo_aggregate", "scripts/aeo-aggregate.py")
wr = _load("weekly_report", "scripts/weekly-report.py")


# --- fixtures: real log lines, one per rule ---------------------------------

def _line(ua, path, status):
    return (f'1.2.3.4 - - [05/Aug/2026:10:00:00 +0000] "GET {path} HTTP/1.1" '
            f'{status} 1234 "-" "{ua}"')


CHATGPT_UA = ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); "
              "compatible; ChatGPT-User/1.0; +https://openai.com/bot")
CLAUDE_UA = "Claude-User/1.0; +Claude-User@anthropic.com"
PERPLEXITY_UA = "Mozilla/5.0 (compatible; Perplexity-User/1.0)"
SCANNER_UA = "Mozilla/5.0 (compatible; OAI-SearchBot/1.0)"
BROWSER_UA = "Mozilla/5.0 (Macintosh) Chrome/131.0 Safari/537.36"

COUNTED = [
    _line(CHATGPT_UA, "/reviews/is-redotpay-legit/", 200),      # (a) counts
    _line(CLAUDE_UA, "/faq/who-can-get-ether-fi-cash/", 200),
    _line(PERPLEXITY_UA, "/", 200),
]
NOT_COUNTED = [
    _line(SCANNER_UA, "/api/.env", 404),                        # (b) scanner
    _line(CHATGPT_UA, "/reviews/is-redotpay-legit/", 404),      # (c) 404
    _line(BROWSER_UA, "/reviews/is-redotpay-legit/", 200),      # a human
    _line(SCANNER_UA, "/reviews/is-redotpay-legit/", 200),      # an INDEXER, not answer-time
    _line(CHATGPT_UA, "/.aws/credentials", 200),                # shape
    _line(CHATGPT_UA, "/videos/abc123/", 200),                  # not an article bucket
]

WEEK = (date(2026, 8, 3), date(2026, 8, 9))


def test_a_real_article_fetch_counts():
    for line in COUNTED:
        assert aeo.parse_line(line) is not None, line


def test_a_404_never_counts():
    """Fixture (c): same agent, same article URL, status 404."""
    assert aeo.parse_line(_line(CHATGPT_UA, "/reviews/is-redotpay-legit/", 404)) is None


def test_a_scanner_path_never_counts():
    """Fixture (b): the /.env family, whatever user agent it claims."""
    assert aeo.parse_line(_line(SCANNER_UA, "/api/.env", 404)) is None
    assert aeo.parse_line(_line(CHATGPT_UA, "/.aws/credentials", 200)) is None
    assert aeo.parse_line(_line(CHATGPT_UA, "/id_rsa", 200)) is None


def test_only_answer_time_agents_count():
    """Indexing crawlers say nothing about being used in an answer."""
    for ua in ("GPTBot/1.2", "ClaudeBot/1.0", "PerplexityBot/1.0",
               "OAI-SearchBot/1.0", BROWSER_UA):
        assert aeo.parse_line(_line(ua, "/reviews/is-redotpay-legit/", 200)) is None


def test_the_three_answer_time_agents_are_exactly_these():
    """The list, spelled out. Widening it silently would inflate the metric."""
    assert set(aeo.AI_ANSWER_AGENTS) == {"ChatGPT-User", "Claude-User", "Perplexity-User"}


def test_article_shape_is_bucket_slash_slug():
    assert aeo.is_article_path("/")
    assert aeo.is_article_path("/reviews/is-redotpay-legit/")
    assert aeo.is_article_path("/geo/best-crypto-card-india/")
    assert not aeo.is_article_path("/reviews/")           # bucket index
    assert not aeo.is_article_path("/reviews/x")          # no trailing slash
    assert not aeo.is_article_path("/wp-admin/setup/")    # unknown bucket
    assert not aeo.is_article_path("/.env")


def test_aggregate_counts_only_the_three_good_lines():
    agg = aeo.aggregate(COUNTED + NOT_COUNTED, *WEEK)
    assert agg["ai_fetches"] == 3
    assert agg["ai_pages_fetched"] == 3
    assert agg["by_agent"] == {"ChatGPT-User": 1, "Claude-User": 1, "Perplexity-User": 1}


def test_aggregate_ignores_lines_outside_the_week():
    outside = _line(CHATGPT_UA, "/hub/best-crypto-card-2026/", 200).replace(
        "05/Aug/2026", "20/Jul/2026")
    agg = aeo.aggregate(COUNTED + [outside], *WEEK)
    assert agg["ai_fetches"] == 3


# --- (d) a week with no aggregate is Missing, and Missing heads the send ----
# Y57: Missing no longer blocks the send — it must surface in the ACTION
# REQUIRED header of the text that actually reaches the operator.

def test_a_missing_week_is_missing_not_zero():
    m = wr.Missing("fetch_week", "no aggregate for 2026-W32")
    assert "n/a" in str(m)
    assert str(m).strip() != "0"


def test_an_unmeasured_aeo_figure_heads_the_report_one_level_down():
    """The AEO figures live one level down, under report['aeo']. A guard that
    only scanned the top level would have hidden a whole n/a section."""
    report = {"human_sessions": 609,
              "aeo": {"fetch_week": wr.Missing("fetch_week", "no aggregate for 2026-W32")}}
    header = wr.action_required(report)
    assert header.startswith("ACTION REQUIRED")
    assert "aeo.fetch_week" in header
    assert "no aggregate for 2026-W32" in header


def test_a_fully_measured_aeo_section_carries_no_action_header():
    assert wr.action_required({
        "human_sessions": 609,
        "aeo": {"fetch_week": {"ai_fetches": 1443}, "ai_sessions": {"chatgpt.com": 3}},
    }) is None


# --- history outlives log rotation -----------------------------------------

def test_history_round_trips_and_replaces_rather_than_duplicates(tmp_path, monkeypatch):
    """nginx keeps 15 days. If a re-run appended instead of replacing, the
    8-week window would double-count whichever weeks were recomputed."""
    monkeypatch.setattr(aeo, "AEO_DIR", tmp_path)
    monkeypatch.setattr(aeo, "HISTORY", tmp_path / "history.jsonl")
    aeo.write_week("2026-W32", {"ai_fetches": 100})
    aeo.write_week("2026-W33", {"ai_fetches": 200})
    aeo.write_week("2026-W32", {"ai_fetches": 111})     # re-run of an old week
    rows = [json.loads(l) for l in (tmp_path / "history.jsonl").read_text().splitlines()]
    assert [r["week"] for r in rows] == ["2026-W32", "2026-W33"]
    assert aeo.load_week("2026-W32")["ai_fetches"] == 111


def test_last_complete_week_never_returns_the_running_one():
    # 2026-08-11 is a Tuesday; the week that has actually finished is W32.
    assert aeo.last_complete_week(date(2026, 8, 11)) == "2026-W32"
    assert aeo.iso_week_bounds("2026-W32") == (date(2026, 8, 3), date(2026, 8, 9))


# --- the thresholds, declared before the work and not revisable -------------

def test_the_declared_thresholds_are_exactly_these():
    """Typed out by hand. If someone edits weekly-report.py to make an
    unsuccessful round look successful, this is the file that has to be edited
    too — in a reviewed diff, with a reason.

    Baseline read live from GA4 on the declaration date over 2026-06-16..08-10:
    chatgpt.com 26 + claude.ai 6 + perplexity.ai 1 = 33 sessions; 9 clicks.
    """
    assert wr.AEO_BASELINE_DECLARED_ON == "2026-08-11"
    assert wr.AEO_BASELINE_WINDOW == ("2026-06-16", "2026-08-10")
    assert wr.AEO_BASELINE_AI_SESSIONS == 33
    assert wr.AEO_BASELINE_AI_CLICKS == 9
    assert wr.AEO_SUCCESS_AI_SESSIONS == 66      # 2x
    assert wr.AEO_SUCCESS_AI_CLICKS == 18        # 2x
    assert wr.AEO_PARTIAL_AI_SESSIONS == 50      # 1.5x, rounded up from 49.5
    assert wr.AEO_PARTIAL_AI_CLICKS == 14        # 1.5x, rounded up from 13.5
    assert wr.AEO_VERDICT_WEEKS == 8


def test_success_requires_doubling_BOTH_numbers():
    assert wr.verdict(66, 18) == "WORKED"
    assert wr.verdict(66, 17) != "WORKED"        # sessions alone is not success
    assert wr.verdict(65, 18) != "WORKED"        # clicks alone is not success
    assert wr.verdict(200, 9) != "WORKED"


def test_holding_the_baseline_is_not_success():
    """The channel is falling. Standing still is noise, not an effect."""
    assert wr.verdict(33, 9) == "DID NOT WORK"


def test_partial_and_failure_boundaries():
    assert wr.verdict(50, 0) == "PARTIAL"
    assert wr.verdict(0, 14) == "PARTIAL"
    assert wr.verdict(49, 13) == "DID NOT WORK"


def test_the_ai_source_list_is_this_exact_list():
    assert set(wr.AEO_AI_SOURCES) == {
        "chatgpt.com", "chat.openai.com", "claude.ai", "perplexity.ai",
        "copilot.microsoft.com", "gemini.google.com"}


def test_declaring_the_start_twice_does_not_restart_the_clock(tmp_path, monkeypatch):
    """A verdict window that can be restarted after a bad month is not a
    window. The second call must return the first call's dates."""
    monkeypatch.setattr(wr, "AEO_DIR", tmp_path)
    monkeypatch.setattr(wr, "AEO_BASELINE_FILE", tmp_path / "baseline.json")
    first = wr.declare_start("2026-08-12")
    second = wr.declare_start("2026-10-01")
    assert second["verdict_window"] == first["verdict_window"] == ["2026-08-12", "2026-10-07"]
    assert second["baseline_ai_sessions"] == 33


# --- the staleness line the report must carry ------------------------------

def test_report_names_an_overdue_availability_list(tmp_path, monkeypatch):
    """AEO-1's rule, mirrored in the report: no fresh check, no silence."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "availability.yaml").write_text(
        'cards:\n'
        '  etherfi:\n    name: "x"\n    verified: "2026-06-10"\n'   # 62 days before
        '  cryptocom:\n    name: "y"\n',                            # never verified
        encoding="utf-8")
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    lines = wr.availability_staleness("2026-08-11")
    assert any("OVERDUE" in l and "etherfi" in l for l in lines)
    assert any("never verified" in l and "cryptocom" in l for l in lines)


def test_a_fresh_list_produces_no_alarm(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "availability.yaml").write_text(
        'cards:\n  etherfi:\n    name: "x"\n    verified: "2026-08-01"\n', encoding="utf-8")
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    assert wr.availability_staleness("2026-08-11") == []
