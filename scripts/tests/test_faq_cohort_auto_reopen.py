"""faq_child_queue_with_reopen() — Fable 2026-09-14, A4 step 5 rework.

THE STALL THIS PINS: cohort 1 finished printing 2026-09-05 and nothing ever
opened cohort 2 — `faq-children.py --cohort 20 --write` was a manual command
never wired to a cron (ruling-2's REJECT, item 4: "cohort 3 will stall the
same way cohort 2 did"). Rather than a NEW weekly cron line (which could
still leave up to 6 days of stall), the already-installed `15,45 * * * *`
FAQ-children tick now auto-reopens the next cohort itself the moment the
current one is fully consumed — no new cron required.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
_SPEC = importlib.util.spec_from_file_location("pseo_generate", ROOT / "scripts" / "pseo-generate.py")
pg = importlib.util.module_from_spec(_SPEC)
sys.modules["pseo_generate"] = pg
_SPEC.loader.exec_module(pg)


class _FakeFaqModule:
    def __init__(self, result):
        self._result = result
        self.calls = 0

    def open_next_cohort(self, only_if_consumed, writer_sha=None):
        self.calls += 1
        assert only_if_consumed is True
        return self._result


def test_reopens_and_rebuilds_the_queue_when_a_cohort_finishes(monkeypatch):
    """THE BUG THIS PINS: without this wiring, a fully-consumed cohort left
    the queue empty forever — this is the exact 2026-09-05 stall."""
    calls = {"n": 0}

    def fake_queue():
        calls["n"] += 1
        return [] if calls["n"] == 1 else ["item-from-cohort-3"]

    fake_fc = _FakeFaqModule({"event": "opened", "cohort": 3, "size": 20})
    monkeypatch.setattr(pg, "faq_child_queue", fake_queue)
    monkeypatch.setattr(pg, "_faq_module", lambda: fake_fc)

    queue, reopen = pg.faq_child_queue_with_reopen(dry_run=False)
    assert queue == ["item-from-cohort-3"]
    assert reopen == {"event": "opened", "cohort": 3, "size": 20}
    assert fake_fc.calls == 1


def test_never_reopens_under_dry_run(monkeypatch):
    """A dry run must never write the cohort ledger."""
    fake_fc = _FakeFaqModule({"event": "opened", "cohort": 3, "size": 20})
    monkeypatch.setattr(pg, "faq_child_queue", lambda: [])
    monkeypatch.setattr(pg, "_faq_module", lambda: fake_fc)

    queue, reopen = pg.faq_child_queue_with_reopen(dry_run=True)
    assert queue == []
    assert reopen is None
    assert fake_fc.calls == 0


def test_never_attempts_reopen_when_the_queue_is_already_non_empty(monkeypatch):
    fake_fc = _FakeFaqModule({"event": "opened", "cohort": 3, "size": 20})
    monkeypatch.setattr(pg, "faq_child_queue", lambda: ["still-open-item"])
    monkeypatch.setattr(pg, "_faq_module", lambda: fake_fc)

    queue, reopen = pg.faq_child_queue_with_reopen(dry_run=False)
    assert queue == ["still-open-item"]
    assert reopen is None
    assert fake_fc.calls == 0


def test_carries_the_refusal_reason_when_the_tap_is_closed(monkeypatch):
    """Y55: a zero must carry its cause — the caller (main()'s queue_empty
    log line) needs the real refusal reason, not just an empty queue."""
    fake_fc = _FakeFaqModule({"event": "cohort_refused", "reason": "cohort_not_consumed"})
    monkeypatch.setattr(pg, "faq_child_queue", lambda: [])
    monkeypatch.setattr(pg, "_faq_module", lambda: fake_fc)

    queue, reopen = pg.faq_child_queue_with_reopen(dry_run=False)
    assert queue == []
    assert reopen == {"event": "cohort_refused", "reason": "cohort_not_consumed"}


def test_queue_skips_a_voided_cohorts_open_rows(tmp_path, monkeypatch):
    led = tmp_path / "faq-cohorts.json"
    row = {"question": "Q?", "slug": "q", "parent": "/help/x/"}
    led.write_text(json.dumps({"cohorts": [
        {"items": [row], "voided": {"on": "2026-09-29"}}, {"items": [row]}]}), encoding="utf-8")
    monkeypatch.setattr(pg, "FAQ_LEDGER", led)
    assert [c.faq_cohort for c in pg.faq_child_queue()] == [1]
