"""G3 (P0-3) — the success threshold must be one the instrument can reach.

Absolute tiers (views >= 100..500) on a channel with a median of 3 views gave
5 successes out of 1 175 scorings. A threshold that always says "failure"
carries no signal, so Thompson sampling was picking among identically-bad arms.

Mutation to prove this test bites: drop the relative branch from
analytics.score_video (fall through to the absolute tier) → the first test
goes red with 0 successes on the same 20 videos.
"""
from __future__ import annotations
import pytest

from pipeline import analytics


def _pool(n=20):
    """n videos, views 1..n, retention above the pool median for all of them."""
    return {f"v{i:02d}": {"views": i, "retention": 0.5} for i in range(1, n + 1)}


def _score(vid, pool):
    history = {k: v for k, v in pool.items() if k != vid}
    entry = {"video_id": vid, "platform": "youtube", "topic_class": "BOFU"}
    stats = {"view_count": pool[vid]["views"], "retention": pool[vid]["retention"]}
    return analytics.score_video(entry, stats, history)[0]


def test_exactly_top_quartile_succeeds():
    pool = _pool(20)
    wins = [vid for vid in pool if _score(vid, pool)]
    assert len(wins) == 5, f"expected top 25% of 20 = 5 successes, got {len(wins)}: {wins}"
    assert set(wins) == {"v16", "v17", "v18", "v19", "v20"}


def test_threshold_splits_the_sample_never_all_never_none():
    """The gate the plan asks for: on real-shaped data successes are neither 0
    nor everything — including the degenerate case where every video ties."""
    for pool in (_pool(20), {f"v{i:02d}": {"views": 3, "retention": 0.0}
                             for i in range(1, 21)}):
        wins = [vid for vid in pool if _score(vid, pool)]
        assert 0 < len(wins) < len(pool), f"threshold does not split: {len(wins)}/{len(pool)}"


def test_retention_below_pool_median_blocks_success():
    pool = _pool(20)
    for v in pool.values():
        v["retention"] = 0.5
    history = {k: v for k, v in pool.items() if k != "v20"}
    entry = {"video_id": "v20", "platform": "youtube", "topic_class": "BOFU"}
    assert analytics.score_video(entry, {"view_count": 999, "retention": 0.4}, history)[0] is False
    assert analytics.score_video(entry, {"view_count": 999, "retention": 0.6}, history)[0] is True


def test_thin_history_falls_back_to_absolute_tier():
    entry = {"video_id": "v1", "platform": "youtube", "topic_class": "BOFU"}
    tiny = {f"v{i}": {"views": 1, "retention": 0.0} for i in range(3)}
    ok, reason = analytics.score_video(entry, {"view_count": 5, "retention": 0.9}, tiny)
    assert ok is False and "absolute" in reason


def test_top_quantile_is_always_a_strict_subset():
    assert analytics._top_quantile_ids({"a": {"views": 1}}) == set()
    tied = {f"v{i}": {"views": 7} for i in range(4)}
    got = analytics._top_quantile_ids(tied)
    assert 0 < len(got) < 4
