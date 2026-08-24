"""Y49 P1-2 gate — a script shorter than the floor never reaches render
without one regeneration, and never loops more than once.

The floor is derived, not chosen: (band_lo - 0.5 s) * 1.96 words/s, where
1.96 is the measured edge-tts rate over the 38 real renders on disk.
"""
from __future__ import annotations

from pipeline import write_script

YT_TOPIC = {
    "id": 9001, "title": "Crypto card fees", "platform": "youtube",
    "class": "BOFU", "primary": "crypto card fees", "target_seconds": 37.5,
    "target_words": 90, "duration_band": [30, 45],
}
CREATIVE = {"tuple": {"hook": "stat"}, "phase": "explore"}


def _script(n_words: int) -> dict:
    vo = " ".join(["cashback"] * n_words)
    return {"hook": "h", "body": "b", "cta": "c", "voiceover": vo,
            "visual_cues": [], "hashtags": {}, "thumbnail_hook": "t"}


def _spy(monkeypatch, *replies):
    calls = []

    def fake(topic, creative, note=None):
        calls.append(note)
        return replies[min(len(calls) - 1, len(replies) - 1)]

    monkeypatch.setattr(write_script, "_call_claude", fake)
    return calls


def test_floor_comes_from_the_duration_band():
    assert write_script.min_words(YT_TOPIC) == 58          # ceil(29.5 * 1.96)
    assert write_script.min_words({"duration_band": [15, 30]}) == 29


def test_short_script_is_regenerated_once(monkeypatch):
    calls = _spy(monkeypatch, _script(41), _script(80))
    out = write_script.write(YT_TOPIC, creative=CREATIVE)
    assert len(calls) == 2, "a 41-word script must trigger exactly one retry"
    assert calls[1] and "58 words" in calls[1], "the retry must state the floor"
    assert len(out["voiceover"].split()) == 80


def test_retry_happens_at_most_once(monkeypatch):
    """A model that keeps returning short copy must not spin a live cron."""
    calls = _spy(monkeypatch, _script(41), _script(42))
    out = write_script.write(YT_TOPIC, creative=CREATIVE)
    assert len(calls) == 2
    assert len(out["voiceover"].split()) == 42


def test_long_enough_script_is_not_retried(monkeypatch):
    calls = _spy(monkeypatch, _script(58))
    write_script.write(YT_TOPIC, creative=CREATIVE)
    assert len(calls) == 1 and calls[0] is None
