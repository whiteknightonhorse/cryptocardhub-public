"""T-0142-02 / G39 — pattern_selector must never hand back a duration_band
pattern that doesn't overlap the topic's own duration_band.

Root cause of the 66.9s H1 render (topic_2133956, 2026-09-23, see
output/youtube/2026-09-23/topic_2133956/): the topic's duration_band was
[30, 45] (youtube's H1/finance band), but `duration_long_60_90` ([60, 90])
was still selectable and the writer composed for it — render() then silently
clamped the output to 60s instead of the ~37.5s the topic actually asked for.

Mutation to prove this test bites: drop the `_duration_compatible(...)` term
from `select()`'s eligibility filter -> `duration_long_60_90` becomes
selectable again for the [30, 45] band and `test_long_pattern_unselectable_
for_h1_youtube_band` goes red.
"""
from __future__ import annotations

from pipeline import pattern_selector

H1_YOUTUBE_BAND = [30, 45]  # literal: templates/topic_pool.json duration_band_seconds.youtube.*


def test_long_pattern_unselectable_for_h1_youtube_band(sandbox):
    for seed in range(200):
        selection = pattern_selector.select(
            platform="youtube", topic_class="MOFU", seed=seed,
            duration_band=H1_YOUTUBE_BAND)
        assert selection["tuple"]["duration_band"] != "duration_long_60_90"


def test_duration_compatible_rejects_non_overlapping_range():
    pattern = {"target_seconds": [60, 90]}
    assert pattern_selector._duration_compatible(pattern, [30, 45]) is False
    assert pattern_selector._duration_compatible(pattern, [45, 75]) is True


def test_duration_compatible_is_permissive_without_a_band():
    pattern = {"target_seconds": [60, 90]}
    assert pattern_selector._duration_compatible(pattern, None) is True


def test_duration_compatible_ignores_axes_without_target_seconds():
    pattern = {"id": "hook_pov_first_person"}  # no target_seconds key
    assert pattern_selector._duration_compatible(pattern, [30, 45]) is True
