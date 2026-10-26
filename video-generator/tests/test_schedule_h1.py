"""E-20 (VG_MODE=h1) — 3 slots/week, Mon/Wed/Fri 10:00 ET, YouTube only.

A different cadence law than the daily 1-6/day rotation `generate()` uses
otherwise: not a variant of it, a full replacement while VG_MODE=h1.
"""
from __future__ import annotations
from datetime import date

import pytest

from pipeline import schedule


@pytest.fixture
def h1_mode(monkeypatch):
    monkeypatch.setenv("VG_MODE", "h1")
    monkeypatch.setenv("VIDEO_GENERATOR_ENABLED", "true")


def test_monday_gets_exactly_one_slot_at_10am_et(h1_mode):
    slots = schedule.generate("youtube", target_date=date(2026, 9, 21))  # Monday
    assert len(slots) == 1
    assert slots[0]["due_at_iso"].startswith("2026-09-21T10:00")
    assert slots[0]["platform"] == "youtube"


def test_wednesday_and_friday_also_get_a_slot(h1_mode):
    assert len(schedule.generate("youtube", target_date=date(2026, 9, 23))) == 1  # Wed
    assert len(schedule.generate("youtube", target_date=date(2026, 9, 25))) == 1  # Fri


def test_tuesday_gets_no_slot(h1_mode):
    """Mutation target: a weekday set of {0,1,2,3,4} (every weekday) would
    pass the Mon/Wed/Fri tests above but let this one through too."""
    assert schedule.generate("youtube", target_date=date(2026, 9, 22)) == []  # Tuesday


def test_video_generator_disabled_creates_no_slot_even_on_monday(monkeypatch):
    monkeypatch.setenv("VG_MODE", "h1")
    monkeypatch.setenv("VIDEO_GENERATOR_ENABLED", "false")
    assert schedule.generate("youtube", target_date=date(2026, 9, 21)) == []


def test_instagram_and_tiktok_get_no_slots_in_h1_mode(h1_mode):
    assert schedule.generate("instagram", target_date=date(2026, 9, 21)) == []
    assert schedule.generate("tiktok", target_date=date(2026, 9, 21)) == []


def test_default_mode_is_unaffected(sandbox, monkeypatch):
    """Regression: VG_MODE unset must still produce the pre-E-20 daily
    rotation, not fall through to the H1 3/week law."""
    monkeypatch.delenv("VG_MODE", raising=False)
    monkeypatch.setattr(schedule, "_TARGET_SLOTS", 2)
    slots = schedule.generate("youtube", target_date=date(2026, 9, 22), seed=1)
    assert len(slots) >= 1
    assert slots[0]["slot_id"].startswith("youtube-2026-09-22-")
