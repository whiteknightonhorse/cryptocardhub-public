"""T-0142-03 — weekly-report.py's half of "a zero carries its cause".

16-20.09, both pSEO streams sat in `skip: fleet_paused` for six straight days
and the only trace was a cron log line nobody reads. `_collect_pseo()` now
counts those skips into `pause_ticks`, `render()` prints a literal
`PAUSE: N ticks skipped this week` line, and a non-zero count raises
`pseo_pause` through the same `Missing` -> `action_required()` -> `send()`
pipeline Y57 built for the idle-engine case (see test_pseo_idle_visibility.py).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "weekly_report", Path(__file__).resolve().parent.parent / "weekly-report.py")
wr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(wr)  # type: ignore[union-attr]


def _report(**over):
    r = {
        "since": "2026-09-14", "until": "2026-09-21",
        "human_sessions": 123, "raw_sessions": 341, "bot_direct": 218,
        "by_source": [{"source": "(direct)", "sessions": 25, "engaged": 4}],
        "clicks_by_source": {"chatgpt.com": 10},
        "clicks_by_partner": {"etherfi": 33},
        "utm_content": {"(not set)": 100},
        "videos_7d": 0,
        "median_views_7d": wr.Missing("median_views_7d", "no videos in window"),
        "retention_7d": wr.Missing("retention_7d", "no retention samples"),
        "pseo": {"pages_7d": 0, "idle_ticks": 98, "pause_ticks": 0, "queue": 0,
                 "clusters_total": 442, "covered": 423, "frozen": 19, "thin": 0},
    }
    r.update(over)
    return r


# --- _collect_pseo(): counts the real skip line, not the queue_empty one ---

def test_collect_pseo_counts_fleet_paused_skips(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    d = tmp_path / "logs" / "pseo"
    d.mkdir(parents=True)
    (d / "2026-09-16.jsonl").write_text(
        "[pseo-tick] skip: fleet_paused (until 2026-09-21T01:16:13+00:00, 7200min left)\n"
        "  reason: absent\n"
        "  by: absent\n"
        "[pseo-tick] skip: fleet_paused (until 2026-09-21T01:16:13+00:00, 7100min left)\n"
        "  reason: absent\n"
        "  by: absent\n",
        encoding="utf-8")
    (d / "2026-09-17.jsonl").write_text(
        '{"event": "queue_empty", "clusters_total": 442, "covered": 423, '
        '"frozen": 19, "thin": 0}\n',
        encoding="utf-8")

    out = wr._collect_pseo("2026-09-14", "2026-09-21")
    assert out["pseo"]["pause_ticks"] == 2
    assert out["pseo"]["idle_ticks"] == 1  # the JSON queue_empty record, no text line here
    assert "pseo_pause" in out, "a real paused week must raise the action itself"
    assert "PAUSE: 2 ticks skipped this week" in out["pseo_pause"].why


def test_collect_pseo_counts_faq_stream_pauses_too(tmp_path, monkeypatch):
    """T-0142-03 Fable ruling-1 REJECT: `faq-2026-09-16.jsonl`'s stem is
    lexically greater than "2026-09-21" (the `until` bound), so a bare
    `since <= f.stem <= until` comparison silently drops the whole FAQ
    stream from the window -- 280 counted vs 559 measured on real logs.
    Literal expected sum here, not derived from the code under test."""
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    d = tmp_path / "logs" / "pseo"
    d.mkdir(parents=True)
    (d / "2026-09-16.jsonl").write_text(
        "[pseo-tick] skip: fleet_paused (until 2026-09-21T01:16:13+00:00, 7200min left)\n"
        "  reason: absent\n  by: absent\n",
        encoding="utf-8")
    (d / "faq-2026-09-16.jsonl").write_text(
        "[faq-tick] skip: fleet_paused (until 2026-09-21T01:16:13+00:00, 7200min left)\n"
        "  reason: absent\n  by: absent\n"
        "[faq-tick] skip: fleet_paused (until 2026-09-21T01:16:13+00:00, 7100min left)\n"
        "  reason: absent\n  by: absent\n",
        encoding="utf-8")

    out = wr._collect_pseo("2026-09-14", "2026-09-21")
    assert out["pseo"]["pause_ticks"] == 3, "1 bank + 2 faq, must be summed"


def test_collect_pseo_faq_queue_empty_does_not_wipe_bank_funnel(tmp_path, monkeypatch):
    """T-0142-03 Fable ruling-2 REJECT: the FAQ stream's own queue_empty
    record (`faq_stream_status()` in pseo-generate.py -- {"stream": "faq",
    "cohort", "due"}, no clusters_total) sorted after the bank stream's
    `2026-*.jsonl` file and unconditionally overwrote `funnel`, wiping
    "clusters covered" from the report. Literal expected values here, not
    derived from the code under test."""
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    d = tmp_path / "logs" / "pseo"
    d.mkdir(parents=True)
    (d / "2026-09-16.jsonl").write_text(
        '{"event": "queue_empty", "clusters_total": 447, "covered": 428, '
        '"frozen": 19, "thin": 0}\n',
        encoding="utf-8")
    (d / "faq-2026-09-16.jsonl").write_text(
        '{"event": "queue_empty", "stream": "faq", "cohort": 3, "due": "2026-09-28"}\n',
        encoding="utf-8")

    out = wr._collect_pseo("2026-09-14", "2026-09-21")
    assert out["pseo"]["clusters_total"] == 447
    assert out["pseo"]["covered"] == 428


def test_collect_pseo_zero_pauses_raises_no_action(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    d = tmp_path / "logs" / "pseo"
    d.mkdir(parents=True)
    (d / "2026-09-16.jsonl").write_text(
        '{"event": "queue_empty", "clusters_total": 442, "covered": 423, '
        '"frozen": 19, "thin": 0}\n', encoding="utf-8")

    out = wr._collect_pseo("2026-09-14", "2026-09-21")
    assert out["pseo"]["pause_ticks"] == 0
    assert "pseo_pause" not in out


def test_a_paused_week_raises_action_required():
    r = _report(pseo_pause=wr.Missing(
        "pseo_pause", "PAUSE: 6 ticks skipped this week (fleet paused) — see reason/by"))
    header = wr.action_required(r)
    assert header is not None
    assert "pseo_pause" in header
    assert "PAUSE: 6 ticks skipped this week" in header


def test_a_healthy_week_raises_no_pause_action():
    r = _report()
    header = wr.action_required(r) or ""
    assert "pseo_pause" not in header


# --- the literal line reaches render() --------------------------------------

def test_render_shows_the_literal_pause_line():
    r = _report(pseo={**_report()["pseo"], "pause_ticks": 6})
    text = wr.render(r)
    assert "PAUSE: 6 ticks skipped this week" in text


def test_render_omits_the_pause_line_when_zero():
    text = wr.render(_report())
    assert "PAUSE:" not in text


# --- MUTATION: string built but never wired into send() (Y57) --------------

def test_real_paused_logs_reach_the_wire_end_to_end(tmp_path, monkeypatch):
    """The full path, not the helper (Y53): real log files on disk ->
    `_collect_pseo()` -> `action_required()` -> `send()`. MUTATION this pins:
    deleting the `if pause > 0: out["pseo_pause"] = Missing(...)` block in
    `_collect_pseo()` must turn this red, even though every OTHER test in
    this file that hand-builds `pseo_pause` would stay green."""
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    d = tmp_path / "logs" / "pseo"
    d.mkdir(parents=True)
    (d / "2026-09-16.jsonl").write_text(
        "[pseo-tick] skip: fleet_paused (until 2026-09-21T01:16:13+00:00, 7200min left)\n"
        "  reason: absent\n  by: absent\n"
        "[pseo-tick] skip: fleet_paused (until 2026-09-21T01:16:13+00:00, 7100min left)\n"
        "  reason: absent\n  by: absent\n",
        encoding="utf-8")

    r = _report(**wr._collect_pseo("2026-09-14", "2026-09-21"))

    sent: dict[str, str] = {}

    class _Resp:
        def read(self):
            return b"{}"

    def _fake_urlopen(url, data=None, timeout=None):
        import urllib.parse as _up
        sent["body"] = _up.parse_qs(data.decode())["text"][0]
        return _Resp()

    monkeypatch.setenv("TG_BOT_TOKEN", "x")
    monkeypatch.setenv("TG_CHAT_ID", "y")
    monkeypatch.setattr(wr.urllib.request, "urlopen", _fake_urlopen)

    wr.send(r, wr.render(r))

    body = sent.get("body", "")
    assert body.startswith("ACTION REQUIRED"), body[:80]
    assert "pseo_pause" in body
    assert "PAUSE: 2 ticks skipped this week" in body


def test_pause_header_actually_reaches_the_wire(monkeypatch):
    """MUTATION this pins: `pseo_pause` computed by `_collect_pseo()` but
    never surfaced through `action_required()`/`send()` -- must go red."""
    sent: dict[str, str] = {}

    class _Resp:
        def read(self):
            return b"{}"

    def _fake_urlopen(url, data=None, timeout=None):
        import urllib.parse as _up
        sent["body"] = _up.parse_qs(data.decode())["text"][0]
        return _Resp()

    monkeypatch.setenv("TG_BOT_TOKEN", "x")
    monkeypatch.setenv("TG_CHAT_ID", "y")
    monkeypatch.setattr(wr.urllib.request, "urlopen", _fake_urlopen)

    r = _report(pseo_pause=wr.Missing(
        "pseo_pause", "PAUSE: 6 ticks skipped this week (fleet paused) — see reason/by"))
    wr.send(r, wr.render(r))

    body = sent.get("body", "")
    assert body.startswith("ACTION REQUIRED"), body[:80]
    assert "pseo_pause" in body
    assert "PAUSE: 6 ticks skipped this week" in body
