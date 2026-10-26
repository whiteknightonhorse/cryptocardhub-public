"""T-0142-02 — write_script's word ceiling, symmetric to the existing floor
(min_words). Root cause: an H1 topic (duration_band [30, 45]) received a
175-word script composed for a duration_long_60_90 pattern the (now-fixed)
pattern_selector filter should never have offered; the ceiling is the
writer-side half of the same law, catching an overlong script even if a
future creative axis reintroduces the mismatch some other way.

Mutation to prove this test bites: remove the ceiling block from write() ->
the overlong script passes straight through, _call_claude is invoked only
once, and test_overlong_script_regenerates_once goes red.
"""
from __future__ import annotations

from pipeline import write_script

TOPIC = {
    "id": 2133956, "class": "MOFU", "pool": "finance", "platform": "youtube",
    "title": "ether.fi Cash", "primary": "ether.fi cash card",
    "target_seconds": 37.5, "target_words": 87, "duration_band": [30, 45],
    "region": None,
}
CREATIVE = {"tuple": {}, "phase": "test"}


def _script(n_words):
    return {
        "hook": "h", "body": "b", "cta": "c",
        "voiceover": " ".join(["word"] * n_words),
        "hashtags": {"youtube_visible": [], "youtube_api": [],
                     "instagram": [], "tiktok": []},
        "thumbnail_hook": "H",
    }


def test_max_words_is_floor_of_band_hi_minus_tail_times_wps():
    assert write_script.max_words(TOPIC) == 87  # floor((45 - 0.5) * 1.96)


def test_overlong_script_regenerates_once(monkeypatch):
    calls = []

    def fake_call_claude(topic, creative, note=None):
        calls.append(note)
        return _script(80) if note else _script(200)

    monkeypatch.setattr(write_script, "_call_claude", fake_call_claude)
    script = write_script.write(TOPIC, dry_run=False, creative=CREATIVE)

    assert len(calls) == 2, "ceiling must trigger exactly one regeneration"
    assert calls[1] is not None and "too long" in calls[1]
    assert len(script["voiceover"].split()) == 80


def test_script_within_ceiling_is_not_regenerated(monkeypatch):
    calls = []

    def fake_call_claude(topic, creative, note=None):
        calls.append(note)
        return _script(70)

    monkeypatch.setattr(write_script, "_call_claude", fake_call_claude)
    script = write_script.write(TOPIC, dry_run=False, creative=CREATIVE)

    assert len(calls) == 1
    assert len(script["voiceover"].split()) == 70
