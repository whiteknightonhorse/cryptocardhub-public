"""T-0142-03 — a zero carries its cause, two more places (Y55 restated).

1. The FAQ stream's `queue_empty` line used to reuse the BANK's counters
   (clusters_total/covered/frozen/thin) even though the real reason for an
   empty FAQ queue lives in the cohort ledger, not the keyword bank. A quick
   read of the log line pointed at the wrong instrument.
2. `skip: fleet_paused` named only the deadline, never who paused the fleet
   or why -- the operator's own `~/.fleet-paused-until` format carries
   `reason:`/`by:` lines that nothing ever printed.

Both laws are pinned at the wiring boundary (Y53: test that the value
ARRIVES, not that a helper could in principle build it) by running the real
`_main()` entry point with the heavy steps (model calls, keyword bank growth)
stubbed out, exactly as `test_pseo_idle_visibility.py` does for the report
side of the same lesson.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
_SPEC = importlib.util.spec_from_file_location("pseo_generate", ROOT / "scripts" / "pseo-generate.py")
pg = importlib.util.module_from_spec(_SPEC)
sys.modules["pseo_generate"] = pg
_SPEC.loader.exec_module(pg)


# --- faq_stream_status(): the literal law ------------------------------------

def test_faq_stream_status_reads_cohort_and_due_date_from_the_ledger(tmp_path, monkeypatch):
    ledger = tmp_path / "faq-cohorts.json"
    ledger.write_text(json.dumps({"cohorts": [
        {"opened": "2026-08-13", "verdict_due_14": "2026-08-27", "items": []},
        {"opened": "2026-09-14", "verdict_due_14": "2026-09-28", "items": []},
    ]}), encoding="utf-8")
    monkeypatch.setattr(pg, "FAQ_LEDGER", ledger)

    out = pg.faq_stream_status()
    assert out == {"stream": "faq", "cohort": 2, "due": "2026-09-28"}, out


def test_faq_stream_status_absent_ledger_is_null_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(pg, "FAQ_LEDGER", tmp_path / "missing.json")
    assert pg.faq_stream_status() == {"stream": "faq", "cohort": None, "due": None}


# --- fleet_pause_reason_by(): reason/by, or 'absent' -------------------------

def test_reason_and_by_are_read_from_lines_2_and_3(tmp_path, monkeypatch):
    f = tmp_path / ".fleet-paused-until"
    f.write_text("1893456000\nreason: throttling a burst of duplicate ticks\nby: dispatcher 2026-09-14\n",
                 encoding="utf-8")
    monkeypatch.setattr(pg, "FLEET_PAUSED_UNTIL_PATH", f)

    reason, by = pg.fleet_pause_reason_by()
    assert reason == "throttling a burst of duplicate ticks"
    assert by == "dispatcher 2026-09-14"


def test_reason_and_by_are_absent_when_the_setter_did_not_write_them(tmp_path, monkeypatch):
    f = tmp_path / ".fleet-paused-until"
    f.write_text("1893456000\n", encoding="utf-8")
    monkeypatch.setattr(pg, "FLEET_PAUSED_UNTIL_PATH", f)

    assert pg.fleet_pause_reason_by() == ("absent", "absent")


def test_reason_and_by_are_absent_when_the_file_does_not_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(pg, "FLEET_PAUSED_UNTIL_PATH", tmp_path / "does-not-exist")
    assert pg.fleet_pause_reason_by() == ("absent", "absent")


# --- the wiring: real _main(), heavy steps stubbed ---------------------------

def _stub_common(monkeypatch, faq_ledger_path):
    monkeypatch.setattr(pg, "pseo_enabled", lambda: True)
    monkeypatch.setattr(pg, "FLEET_PAUSED_UNTIL_PATH", faq_ledger_path.parent / "no-such-pause-file")
    monkeypatch.setattr(pg, "no_progress_guard",
                         type("_G", (), {"check": staticmethod(lambda *a, **k: False)})())


def test_faq_queue_empty_line_carries_stream_cohort_due_not_bank_counters(
        tmp_path, monkeypatch, capsys):
    """MUTATION: reverting the queue_empty branch to `queue_funnel(clusters)`
    for the FAQ stream must turn this red -- clusters_total/covered/frozen/
    thin would reappear and stream/cohort/due would vanish."""
    ledger = tmp_path / "faq-cohorts.json"
    ledger.write_text(json.dumps({"cohorts": [
        {"opened": "2026-08-13", "verdict_due_14": "2026-08-27", "items": []},
        {"opened": "2026-09-14", "verdict_due_14": "2026-09-28", "items": []},
    ]}), encoding="utf-8")
    monkeypatch.setattr(pg, "FAQ_LEDGER", ledger)
    _stub_common(monkeypatch, ledger)
    monkeypatch.setattr(pg, "faq_child_queue_with_reopen",
                         lambda dry_run: ([], {"event": "cohort_refused", "reason": "test"}))

    rc = pg._main(["--faq-children", "--limit", "1"])
    assert rc == 0

    out = capsys.readouterr().out
    rec = next(json.loads(l) for l in out.splitlines() if l.startswith("{"))
    assert rec["event"] == "queue_empty"
    assert rec["stream"] == "faq"
    assert rec["cohort"] == 2
    assert rec["due"] == "2026-09-28"
    assert "clusters_total" not in rec, "FAQ queue_empty must not carry the bank's counters"


def test_bank_queue_empty_line_still_carries_bank_counters(tmp_path, monkeypatch, capsys):
    """Control: the bank stream (no --faq-children) keeps its own funnel --
    this law is FAQ-specific, not a blanket removal of queue_funnel()."""
    ledger = tmp_path / "faq-cohorts.json"
    _stub_common(monkeypatch, ledger)
    monkeypatch.setattr(pg, "build_queue", lambda clusters, only_new, upgrade_thin, **k: [])

    rc = pg._main(["--limit", "1"])
    assert rc == 0

    out = capsys.readouterr().out
    rec = next(json.loads(l) for l in out.splitlines() if l.startswith("{"))
    assert rec["event"] == "queue_empty"
    assert "stream" not in rec
    assert "clusters_total" in rec


def test_fleet_paused_skip_prints_reason_and_by(tmp_path, monkeypatch, capsys):
    """MUTATION: dropping either print() call must turn this red -- the
    eight-day silence this task closes was exactly 'the deadline printed,
    nothing about who or why'."""
    pause_file = tmp_path / ".fleet-paused-until"
    pause_file.write_text(
        f"{int(time.time()) + 3600}\nreason: throttling duplicate ticks\nby: dispatcher 2026-09-14\n",
        encoding="utf-8")
    monkeypatch.setattr(pg, "pseo_enabled", lambda: True)
    monkeypatch.setattr(pg, "FLEET_PAUSED_UNTIL_PATH", pause_file)

    rc = pg._main(["--limit", "1"])
    assert rc == 0

    out = capsys.readouterr().out
    assert "skip: fleet_paused" in out
    assert "reason: throttling duplicate ticks" in out
    assert "by: dispatcher 2026-09-14" in out


def test_fleet_paused_skip_prints_absent_when_the_file_has_no_reason_by(
        tmp_path, monkeypatch, capsys):
    pause_file = tmp_path / ".fleet-paused-until"
    pause_file.write_text(f"{int(time.time()) + 3600}\n", encoding="utf-8")
    monkeypatch.setattr(pg, "pseo_enabled", lambda: True)
    monkeypatch.setattr(pg, "FLEET_PAUSED_UNTIL_PATH", pause_file)

    rc = pg._main(["--limit", "1"])
    assert rc == 0

    out = capsys.readouterr().out
    assert "reason: absent" in out
    assert "by: absent" in out
