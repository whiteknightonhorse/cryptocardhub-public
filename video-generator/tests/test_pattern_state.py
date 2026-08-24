"""G2 (P0-2) — one video may contribute exactly one outcome to the weights.

The live state had 6 242 failures against 25.3 successes over 208 real videos:
each video was charged on every night of its window because of P0-1.

Mutation to prove this test bites: remove the `if any(... video_id ...): return`
guard from pattern_selector.record_video_outcome → the idempotence test goes
red (2 outcomes for 1 video, counts doubled).
"""
from __future__ import annotations
import json

from pipeline import analytics, pattern_selector


PATTERNS = {"hook_type": "hook_a", "format": "format_b"}


def _poisoned_state(n_videos=10, repeats=6):
    """What the live file looked like: every video logged `repeats` times."""
    video_log = []
    patterns = {}
    for r in range(repeats):
        for i in range(n_videos):
            video_log.append({
                "video_id": f"v{i}", "platform": "youtube",
                "patterns_used": PATTERNS, "success": (i == 0),
            })
            for pid in PATTERNS.values():
                rt = patterns.setdefault(pid, {"success_count": 0, "failure_count": 0})
                rt["success_count" if i == 0 else "failure_count"] += 1
    return {"publish_counts": {"youtube": n_videos},
            "video_log": video_log, "patterns": patterns}


def test_rebuild_collapses_to_one_outcome_per_video(sandbox):
    pattern_selector._STATE_PATH.write_text(json.dumps(_poisoned_state()), encoding="utf-8")

    result = analytics.rebuild_pattern_state()

    assert result["before"]["video_log"] == 60
    assert result["after"]["video_log"] == 10 == result["after"]["unique_videos"]

    state = json.loads(pattern_selector._STATE_PATH.read_text(encoding="utf-8"))
    ids = [v["video_id"] for v in state["video_log"]]
    assert len(ids) == len(set(ids)), "state video_log still holds duplicate video_ids"

    total = sum(p["success_count"] + p["failure_count"] for p in state["patterns"].values())
    assert total == len(set(ids)) * len(PATTERNS), (
        f"outcomes in state ({total}) != unique videos x axes "
        f"({len(set(ids))} x {len(PATTERNS)})")
    assert state["patterns"]["hook_a"]["success_count"] == 1
    assert state["patterns"]["hook_a"]["failure_count"] == 9


def test_rebuild_backs_up_the_old_file(sandbox):
    pattern_selector._STATE_PATH.write_text(json.dumps(_poisoned_state()), encoding="utf-8")
    analytics.rebuild_pattern_state()
    backups = list(pattern_selector._STATE_PATH.parent.glob("*.bak-*"))
    assert len(backups) == 1
    assert len(json.loads(backups[0].read_text(encoding="utf-8"))["video_log"]) == 60


def test_rebuild_dry_run_writes_nothing(sandbox):
    pattern_selector._STATE_PATH.write_text(json.dumps(_poisoned_state()), encoding="utf-8")
    analytics.rebuild_pattern_state(dry_run=True)
    state = json.loads(pattern_selector._STATE_PATH.read_text(encoding="utf-8"))
    assert len(state["video_log"]) == 60


def test_record_video_outcome_is_idempotent_per_video(sandbox):
    for _ in range(6):
        pattern_selector.record_video_outcome(
            platform="youtube", video_id="v1", patterns_used=PATTERNS, success=False)

    state = json.loads(pattern_selector._STATE_PATH.read_text(encoding="utf-8"))
    assert len(state["video_log"]) == 1, "same video charged more than once"
    assert state["patterns"]["hook_a"]["failure_count"] == 1
