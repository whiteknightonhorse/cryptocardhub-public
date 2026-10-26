#!/usr/bin/env python3
"""no_progress_guard.py — the ONE no-progress watchdog for every cron pipeline.

LESSON (commit 907e366, 2026-08-24): a run that returns success while the
thing it exists to grow never grows is not a healthy quiet day — it is a
stuck selector reporting green forever. That lesson was applied to one
pipeline (video topic pick). This module is the one place it should have
lived, so the next pipeline that grows this shape imports the fix instead of
re-learning it (28 straight "successful" faq-children runs, 15 days, one
file rewritten over and over, is the same shape again).

Call check() once per run, at the point a run would otherwise report success:

    stalled = check("pseo-pages", metric=len(existing_slugs()), queue_nonempty=bool(queue))
    if stalled:
        telegram_lib.alert("no_progress: pseo-pages stuck 3 runs, queue non-empty")
        return 1

An EMPTY queue is not stalling — there was nothing to do, so the streak
resets. Only a NON-empty queue with an unmoved metric, three runs running,
counts as stuck.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "logs" / "no-progress"
STREAK_LIMIT = 3


def check(pipeline: str, metric: int, queue_nonempty: bool, state_dir: Path = STATE_DIR) -> bool:
    """Record this run's metric under `pipeline`; return True if stalled."""
    state_dir.mkdir(parents=True, exist_ok=True)
    f = state_dir / f"{pipeline}.json"
    prev = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    prev_streak = prev.get("streak", 0)
    prev_metric = prev.get("last_metric")
    if not queue_nonempty:
        streak = 0
    elif prev_metric == metric:
        streak = prev_streak + 1
    else:
        streak = 1
    f.write_text(json.dumps({"streak": streak, "last_metric": metric}), encoding="utf-8")
    return streak >= STREAK_LIMIT


def _demo() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        # empty queue never stalls, however many times it is checked
        for _ in range(5):
            assert check("demo1", metric=10, queue_nonempty=False, state_dir=d) is False
        # non-empty queue, metric stuck at 10: 2 runs not yet stalled, 3rd is
        assert check("demo2", metric=10, queue_nonempty=True, state_dir=d) is False
        assert check("demo2", metric=10, queue_nonempty=True, state_dir=d) is False
        assert check("demo2", metric=10, queue_nonempty=True, state_dir=d) is True
        # metric moving resets the streak
        assert check("demo3", metric=1, queue_nonempty=True, state_dir=d) is False
        assert check("demo3", metric=2, queue_nonempty=True, state_dir=d) is False
        assert check("demo3", metric=2, queue_nonempty=True, state_dir=d) is False
        assert check("demo3", metric=2, queue_nonempty=True, state_dir=d) is True
    print("no_progress_guard self-check OK")


if __name__ == "__main__":
    _demo()
