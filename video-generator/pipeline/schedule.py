"""Daily 6-slot schedule generator with platform prime-time windows.

Per CLAUDE.md schedule policy + competitor-analysis §4.5 + polymarket vg2 IT-10-SCHEDULE.

Windows are publisher-local ET. AVOID 20:00 ET for finance.
Min 60-min gap between slots.
"""
from __future__ import annotations
import json
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import _config

ET = ZoneInfo("America/New_York")

# Per-platform priority windows (start_hour, end_hour, weekday_filter, min_slots)
# weekday_filter: None = any day; or set of weekday ints (0=Mon)
_WINDOWS: dict[str, list[dict[str, Any]]] = {
    "youtube": [
        {"start": 12, "end": 14, "weekdays": None, "min": 1},  # lunch (must-fill)
        {"start": 14, "end": 16, "weekdays": None, "min": 2},  # YT prime weekday afternoon
        {"start": 11, "end": 13, "weekdays": None, "min": 1},  # morning EU/US
    ],
    "instagram": [
        {"start": 11, "end": 13, "weekdays": None, "min": 2},  # daily peak
        {"start": 13, "end": 19, "weekdays": {1, 2}, "min": 2},  # Tue/Wed surge
        {"start": 12, "end": 13, "weekdays": None, "min": 1},  # lunch must-fill
    ],
    "tiktok": [
        {"start": 14, "end": 18, "weekdays": {1, 2, 3, 4}, "min": 3},  # Tue-Fri TT prime
        {"start": 12, "end": 13, "weekdays": None, "min": 1},          # lunch must-fill
        {"start": 13, "end": 14, "weekdays": {0}, "min": 1},           # Mon
    ],
}

# AVOID 20:00 ET ± 30 min for ALL platforms (competitor-analysis §4.5)
_AVOID = [(time(19, 30), time(20, 30))]
_MIN_GAP_MIN = 60
# 2026-05-13: dropped from 6 → 2 (Y46) because 6/day on a 10-sub channel
# is a spam fingerprint per YT classifier. Real new creators publish
# 1-3/week; we were hitting 42/week. Ramp back up to 4-6 after subs >= 100
# and view counts confirm algorithm trust. Override via TARGET_SLOTS_PER_DAY
# env var if testing.
import os as _os
_TARGET_SLOTS = int(_os.environ.get("TARGET_SLOTS_PER_DAY", "2"))
_WINDOW_OPEN = time(9, 0)
_WINDOW_CLOSE = time(21, 0)


def _avoid(t: time) -> bool:
    return any(s <= t <= e for s, e in _AVOID)


def _gap_ok(new: time, existing: list[time]) -> bool:
    new_min = new.hour * 60 + new.minute
    for e in existing:
        if abs(new_min - (e.hour * 60 + e.minute)) < _MIN_GAP_MIN:
            return False
    return True


def _random_in(rng: random.Random, win: dict[str, Any]) -> time:
    minute = rng.randint(0, 59)
    hour = rng.randint(win["start"], win["end"] - 1)
    return time(hour, minute)


def generate(platform: str, target_date: date | None = None,
             seed: int | None = None) -> list[dict[str, Any]]:
    """Generate today's 6-slot schedule for a platform.

    Returns: [{slot_id, due_at_iso, fired: false}, ...]
    """
    target_date = target_date or datetime.now(ET).date()
    rng = random.Random(seed if seed is not None else int(target_date.toordinal()))

    weekday = target_date.weekday()
    chosen_times: list[time] = []
    eligible_windows = [
        w for w in _WINDOWS.get(platform, _WINDOWS["youtube"])
        if w.get("weekdays") is None or weekday in w["weekdays"]
    ]

    # First fill prime windows to satisfy `min` per window
    for w in eligible_windows:
        for _ in range(w["min"]):
            for _attempt in range(40):
                t = _random_in(rng, w)
                if t < _WINDOW_OPEN or t >= _WINDOW_CLOSE:
                    continue
                if _avoid(t):
                    continue
                if not _gap_ok(t, chosen_times):
                    continue
                chosen_times.append(t)
                break
            if len(chosen_times) >= _TARGET_SLOTS:
                break
        if len(chosen_times) >= _TARGET_SLOTS:
            break

    # Then fill remaining slots uniformly across the open window
    while len(chosen_times) < _TARGET_SLOTS:
        for _attempt in range(80):
            hour = rng.randint(9, 20)
            minute = rng.randint(0, 59)
            t = time(hour, minute)
            if t >= _WINDOW_CLOSE or _avoid(t) or not _gap_ok(t, chosen_times):
                continue
            chosen_times.append(t)
            break
        else:
            break  # could not fit; ship with fewer slots

    chosen_times.sort()
    slots = []
    for i, t in enumerate(chosen_times):
        dt = datetime.combine(target_date, t, tzinfo=ET)
        slots.append({
            "slot_id": f"{platform}-{target_date.isoformat()}-{i+1}",
            "platform": platform,
            "due_at_iso": dt.isoformat(),
            "due_at_utc": dt.astimezone(ZoneInfo("UTC")).isoformat(),
            "fired": False,
        })
    return slots


def _state_path() -> Path:
    return _config.root() / "state" / "schedule.json"


def write(all_slots: dict[str, list[dict[str, Any]]]) -> Path:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "generated_at": datetime.now(ET).isoformat(),
        "by_platform": all_slots,
    }, indent=2), encoding="utf-8")
    tmp.replace(p)
    return p


def load() -> dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return {"by_platform": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def due_slot(platform: str, now_utc: datetime | None = None) -> dict[str, Any] | None:
    """Find the first un-fired slot whose due_at_utc <= now."""
    now_utc = now_utc or datetime.now(ZoneInfo("UTC"))
    state = load()
    slots = state.get("by_platform", {}).get(platform, [])
    for s in slots:
        if s["fired"]:
            continue
        due = datetime.fromisoformat(s["due_at_utc"])
        if due <= now_utc:
            return s
    return None


def mark_fired(slot_id: str) -> None:
    state = load()
    for plat, slots in state.get("by_platform", {}).items():
        for s in slots:
            if s["slot_id"] == slot_id:
                s["fired"] = True
                s["fired_at"] = datetime.now(ZoneInfo("UTC")).isoformat()
                break
    p = _state_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(p)
