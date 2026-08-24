"""G1 (P0-1) — the scoring verdict must survive to disk.

Before the fix `main_loop` compared `updated_entries != entries`, two lists
holding the SAME dict objects mutated in place → always equal → the log was
never rewritten and every video was re-scored on every night of its 7-day
window (1 175 `scored` events for 208 videos; 0 rows with scored=true).

Mutation to prove this test bites: restore `and updated_entries != entries`
on the write branch in analytics.main_loop → both asserts below go red.
"""
from __future__ import annotations
import json
from datetime import timedelta

from pipeline import analytics
from conftest import now_utc, read_video_log, read_events


def _entry(video_id="vid1", hours_ago=48):
    return {
        "video_id": video_id,
        "platform": "youtube",
        "ts_published": (now_utc() - timedelta(hours=hours_ago)).isoformat(),
        "topic_id": 1,
        "topic_class": "BOFU",
        "patterns_used": {"hook_type": "hook_a", "format": "format_b"},
        "transport": "direct",
        "scored": False,
        "score_attempts": 0,
    }


def test_verdict_is_persisted_and_video_scored_once(sandbox, monkeypatch):
    analytics._VIDEO_LOG_PATH.write_text(json.dumps(_entry()) + "\n", encoding="utf-8")
    monkeypatch.setattr(analytics, "pull_youtube_stats",
                        lambda vid: {"view_count": 120, "retention": 0.7})

    first = analytics.main_loop()
    second = analytics.main_loop()

    rows = read_video_log(analytics._VIDEO_LOG_PATH)
    assert len(rows) == 1
    assert rows[0]["scored"] is True, "verdict did not survive to video_pattern_log.jsonl"
    assert rows[0]["score_result"]["success"] is True

    scored_events = [e for e in read_events(analytics._LOG_DIR, "scored")
                     if e["video_id"] == "vid1"]
    assert len(scored_events) == 1, (
        f"video re-scored {len(scored_events)}x — the persistence fix is not holding")
    assert first["scored_now"] == 1 and second["scored_now"] == 0


def test_dry_run_never_writes(sandbox, monkeypatch):
    analytics._VIDEO_LOG_PATH.write_text(json.dumps(_entry()) + "\n", encoding="utf-8")
    monkeypatch.setattr(analytics, "pull_youtube_stats",
                        lambda vid: {"view_count": 120, "retention": 0.7})

    analytics.main_loop(dry_run=True)

    assert read_video_log(analytics._VIDEO_LOG_PATH)[0]["scored"] is False
