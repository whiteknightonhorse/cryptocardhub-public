"""T-0144 — one Telegram alert per (slot, cause), never per 10-minute tick.

Mutations that must go red:
  * drop the mark_failed guard in main()            -> test_same_cause_alerts_once (3 != 1)
  * key the dedup on slot_id alone                  -> test_a_new_cause_alerts_again (1 != 2)
  * mark the slot fired on failure "to stop the noise" -> test_same_cause_alerts_once (fired is True)
"""
from __future__ import annotations
import json, sys
import pytest
from pipeline import main as m, schedule

SLOT = "youtube-h1-2026-09-25"


def _state(tmp_path, monkeypatch):
    p = tmp_path / "schedule.json"
    p.write_text(json.dumps({"by_platform": {"youtube": [{
        "slot_id": SLOT, "platform": "youtube",
        "due_at_utc": "2026-09-25T14:00:00+00:00", "fired": False}]}}))
    monkeypatch.setattr(schedule, "_state_path", lambda: p)
    return p


def _tick(monkeypatch, alerts, msg):
    def boom(*a, **k): raise RuntimeError(msg)
    monkeypatch.setattr(m, "run_pipeline", boom)
    monkeypatch.setattr(m.telegram_preview, "alert", lambda text, dry_run=False: alerts.append(text))
    monkeypatch.setattr(sys, "argv", ["main", "--platform=youtube", "--check-slot"])
    with pytest.raises(RuntimeError):
        m.main()


def test_same_cause_alerts_once(tmp_path, monkeypatch):
    p = _state(tmp_path, monkeypatch); alerts = []
    for _ in range(3):
        _tick(monkeypatch, alerts, "VG_MODE=h1: no eligible hot etherfi page for youtube")
    assert len(alerts) == 1
    assert json.loads(p.read_text())["by_platform"]["youtube"][0]["fired"] is False


def test_a_new_cause_alerts_again(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch); alerts = []
    _tick(monkeypatch, alerts, "VG_MODE=h1: no eligible hot etherfi page for youtube")
    _tick(monkeypatch, alerts, "claude --print failed: no output")
    assert len(alerts) == 2
