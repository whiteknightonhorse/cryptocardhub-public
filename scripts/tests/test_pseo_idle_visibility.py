"""G28 — a finished pSEO engine must announce itself, not idle in silence.

The engine is not broken: 544 pages already cover every cluster the keyword
bank produces, so the queue is legitimately empty. But for eight days that
produced 97 idle cron fires and zero pages, and the only trace was the words
"queue empty" in a log nobody reads — the Y55/Y57 failure exactly: a zero that
carries its cause but reaches no human surface.

These tests pin the two halves of the fix: the funnel reaches the report, and
the report's ACTION REQUIRED header survives the transmit boundary (Y53 — test
the wiring, not the helper).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "weekly_report", Path(__file__).resolve().parent.parent / "weekly-report.py")
wr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(wr)  # type: ignore[union-attr]


def _report(**over):
    """A report complete enough for render() — the same shape the suite's own
    fixtures use, plus the pSEO section under test."""
    r = {
        "since": "2026-08-06", "until": "2026-08-13",
        "human_sessions": 123, "raw_sessions": 341, "bot_direct": 218,
        "by_source": [{"source": "(direct)", "sessions": 25, "engaged": 4}],
        "clicks_by_source": {"chatgpt.com": 10},
        "clicks_by_partner": {"etherfi": 33},
        "utm_content": {"(not set)": 100},
        "videos_7d": 0,
        "median_views_7d": wr.Missing("median_views_7d", "no videos in window"),
        "retention_7d": wr.Missing("retention_7d", "no retention samples"),
        "pseo": {"pages_7d": 0, "idle_ticks": 98, "queue": 0,
                 "clusters_total": 442, "covered": 423, "frozen": 19, "thin": 0},
    }
    r.update(over)
    return r


# --- the law: an exhausted universe raises an action, not a number ----------

def test_idle_engine_raises_action_required_at_the_transmit_boundary():
    r = _report(pseo_expansion=wr.Missing(
        "pseo_expansion", "queue empty 98 ticks/7d — universe exhausted"))
    header = wr.action_required(r)
    assert header is not None, "an unmeasured figure produced no header"
    assert header.startswith("ACTION REQUIRED"), header[:60]
    assert "pseo_expansion" in header


def test_a_healthy_engine_raises_no_pseo_action():
    """Control: pages were written, so THIS engine owes nothing.

    Asserting the whole header is None would be wrong — other figures may be
    legitimately unmeasured in the same report, and that is not this law's
    business.
    """
    r = _report(pseo={"pages_7d": 4, "idle_ticks": 2, "queue": 3})
    header = wr.action_required(r) or ""
    assert "pseo_expansion" not in header


# --- the funnel reaches the rendered text -----------------------------------

def test_render_shows_the_funnel_numbers():
    text = wr.render(_report())
    assert "pSEO ENGINE" in text
    assert "idle ticks" in text
    assert "98" in text, "the idle-tick count never reaches the reader"
    assert "423/442" in text, "the coverage funnel never reaches the reader"


def test_render_survives_a_missing_pseo_key():
    """An absent section must not take the whole report down."""
    r = _report()
    del r["pseo"]
    text = wr.render(r)
    assert "pSEO ENGINE" in text
    assert "n/a" in text


def test_render_survives_a_missing_object():
    r = _report(pseo=wr.Missing("pseo", "OSError"))
    text = wr.render(r)
    assert "pSEO ENGINE" in text


# --- the acknowledgement flag -----------------------------------------------

def test_flag_file_silences_the_action(tmp_path, monkeypatch):
    """The operator may accept 'the universe is complete' — but only in writing."""
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    (tmp_path / "logs" / "pseo").mkdir(parents=True)
    (tmp_path / "logs" / "pseo" / "2026-08-10.jsonl").write_text(
        "[pseo] queue empty — nothing to generate\n", encoding="utf-8")

    out = wr._collect_pseo("2026-08-06", "2026-08-13")
    assert "pseo_expansion" in out, "no flag, yet no action was raised"

    (tmp_path / "scripts" / "state").mkdir(parents=True)
    (tmp_path / "scripts" / "state" / "pseo-universe-complete").write_text("2026-08-13")
    out2 = wr._collect_pseo("2026-08-06", "2026-08-13")
    assert "pseo_expansion" not in out2, "the flag did not silence the action"
    assert out2["pseo"]["idle_ticks"] == 1, "the funnel stopped counting once acknowledged"


# --- the transmit boundary itself, not the helper ---------------------------

def test_the_header_actually_reaches_the_wire(monkeypatch):
    """Y53, learned again the hard way: the first version of this gate called
    `action_required()` directly, so the mutation "header built but never
    prepended in send()" passed green. A law about what the operator receives
    has to read what leaves the process.
    """
    sent: dict[str, str] = {}

    class _Resp:
        def read(self):  # noqa: D401
            return b"{}"

    def _fake_urlopen(url, data=None, timeout=None):
        import urllib.parse as _up
        sent["body"] = _up.parse_qs(data.decode())["text"][0]
        return _Resp()

    monkeypatch.setenv("TG_BOT_TOKEN", "x")
    monkeypatch.setenv("TG_CHAT_ID", "y")
    monkeypatch.setattr(wr.urllib.request, "urlopen", _fake_urlopen)

    r = _report(pseo_expansion=wr.Missing(
        "pseo_expansion", "queue empty 98 ticks/7d — universe exhausted"))
    wr.send(r, wr.render(r))

    body = sent.get("body", "")
    assert body.startswith("ACTION REQUIRED"), body[:80]
    assert "pseo_expansion" in body
    assert "pSEO ENGINE" in body, "the report body itself never reached the wire"
