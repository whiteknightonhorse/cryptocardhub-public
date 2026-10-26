"""The schedule/cadence-gate discrepancy: the gate correctly capped FIRES to
1/day while the channel was suppressed, but this generator kept writing 2
slots every day regardless — the un-fireable second slot sat unfired until
midnight ET, when the gate's own per-day counter reset and let it fire
within the hour, before that day's own regeneration even ran. Measured in
state/published.db: ~2/day, every day, for the whole 13-day suppressed
window, never 1.

Mutation to prove this test bites: revert schedule._target_slots() to
`return _TARGET_SLOTS` unconditionally (the pre-fix shape) — both tests
below go red, because a suppressed channel would get 2 slots again.
"""
from __future__ import annotations

from conftest import write_scored_events

from pipeline import analytics, schedule


def _suppress(sandbox):
    """20 recent videos, median views 0.5 — well under the health floor."""
    write_scored_events(sandbox / "logs" / "analytics",
                         [{"video_id": f"v{i}", "views": 0.5, "success": False}
                          for i in range(20)])


def _make_healthy(sandbox):
    write_scored_events(sandbox / "logs" / "analytics",
                         [{"video_id": f"v{i}", "views": 500, "success": True}
                          for i in range(20)])


def test_suppressed_channel_gets_only_the_gate_cap(sandbox, monkeypatch):
    monkeypatch.setattr(schedule, "_TARGET_SLOTS", 2)
    _suppress(sandbox)
    slots = schedule.generate("youtube")
    assert len(slots) == analytics._SUPPRESSED_SLOTS_PER_DAY == 1, (
        "a suppressed channel must be scheduled exactly the gate's cap, "
        "not the healthy-channel target — an extra slot here is an extra "
        "slot that will sit unfired and leak into tomorrow"
    )


def test_healthy_channel_keeps_its_normal_target(sandbox, monkeypatch):
    monkeypatch.setattr(schedule, "_TARGET_SLOTS", 2)
    _make_healthy(sandbox)
    slots = schedule.generate("youtube")
    assert len(slots) == 2


def test_target_slots_fails_closed_on_no_history(sandbox, monkeypatch):
    """No scored history at all -> channel_health() is fail-closed
    (suppressed=True per its own docstring) -> schedule must follow it, not
    default open to the healthy target."""
    monkeypatch.setattr(schedule, "_TARGET_SLOTS", 2)
    assert schedule._target_slots("youtube") == analytics._SUPPRESSED_SLOTS_PER_DAY
