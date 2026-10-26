"""T-0142-02 / G39 — main.py must refuse a Shorts render whose own narration
already overruns the 60s output cap, instead of letting render() silently
clamp it down (Y49: duration = min(max_seconds, tts + 0.5)).

Reproduces topic_2133956 (output/youtube/2026-09-23/topic_2133956/voice.mp3,
66.87s) — the actual artifact the H1 duration-band mismatch produced.

Mutations to prove this test bites (both performed for real against the
live files and reverted, see PROGRESS.md#T-0142-02-shorts-one-length-law):
  - remove the `tts_seconds + 0.5 > render.SHORTS_MAX_SECONDS` gate from
    `_run_after_topic` -> execution reaches `render.render()`, the `_boom`
    stub fires, and `test_run_after_topic_refuses_before_render` goes red.
  - remove the post-render ffprobe check -> a render whose output drifts
    from `min(SHORTS_MAX_SECONDS, tts+0.5)` is accepted silently, and
    `test_post_render_gate_catches_a_drifted_render` goes red.
"""
from __future__ import annotations
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline import main as pipeline_main, render, write_script
from tests.conftest import write_stub_media

_REAL_VOICE = (
    Path(__file__).resolve().parent.parent / "output" / "youtube"
    / "2026-09-23" / "topic_2133956" / "voice.mp3"
)

_TOPIC = {
    "id": 2133956, "class": "MOFU", "pool": "finance", "platform": "youtube",
    "title": "ether.fi Cash", "primary": "ether.fi cash card",
    "target_seconds": 37.5, "target_words": 87, "duration_band": [30, 45],
    "region": None,
}

_SCRIPT = {
    "hook": "h", "body": "b", "cta": "c", "voiceover": " ".join(["word"] * 60),
    "hashtags": {"youtube_visible": [], "youtube_api": [], "instagram": [], "tiktok": []},
    "thumbnail_hook": "H",
}


def _silence(path: Path, seconds: float) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-t", f"{seconds}", "-c:a", "libmp3lame", "-q:a", "9", str(path)],
        capture_output=True, check=True, timeout=120,
    )
    return path


@pytest.mark.skipif(not _REAL_VOICE.exists(),
                     reason="real 2026-09-23 render artifact not present on this checkout")
def test_real_23_09_voice_is_red_on_the_cap():
    duration = render.probe_duration(_REAL_VOICE)
    assert duration == pytest.approx(66.87, abs=0.01)
    assert duration + 0.5 > render.SHORTS_MAX_SECONDS


def _patch_pipeline(monkeypatch, voice_src: Path):
    """Everything BUT the TTS synth and render() itself is stubbed so the
    gate under test is the only thing standing between a too-long voice and
    a call to render.render() — no downstream mock (missing API keys, etc.)
    can produce a false red."""
    from pipeline import captions, fetch_music, fetch_visuals

    monkeypatch.setattr(write_script, "write", lambda *a, **k: dict(_SCRIPT))
    monkeypatch.setattr(pipeline_main._config, "tts_provider", lambda: "edge")

    def fake_synthesize(voiceover, platform, out_path, dry_run=False, **kw):
        shutil.copy(voice_src, out_path)
        return {"provider": "edge", "dry_run": dry_run}

    monkeypatch.setattr(pipeline_main.tts_edge, "synthesize", fake_synthesize)
    monkeypatch.setattr(pipeline_main.fetch_visuals, "fetch",
                         lambda *a, **k: [{"path": "x", "kind": "photo"}])
    monkeypatch.setattr(pipeline_main.fetch_music, "fetch", lambda *a, **k: {"track": "m"})
    monkeypatch.setattr(pipeline_main.captions, "align",
                         lambda *a, **k: [{"word": "w", "start": 0.0, "end": 0.3}])


def test_run_after_topic_refuses_before_render(tmp_path, monkeypatch):
    voice_src = (_silence(tmp_path / "src_voice.mp3", 66.87) if not _REAL_VOICE.exists()
                 else _REAL_VOICE)
    _patch_pipeline(monkeypatch, voice_src)

    def _boom(*a, **k):
        raise AssertionError("render.render must not run once the TTS gate refuses")
    monkeypatch.setattr(pipeline_main.render, "render", _boom)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with pytest.raises(RuntimeError, match="Shorts cap"):
        pipeline_main._run_after_topic("youtube", _TOPIC, out_dir, dry_run=False)


def test_run_after_topic_accepts_a_short_enough_voice(tmp_path, monkeypatch):
    voice_src = _silence(tmp_path / "src_voice.mp3", 35.0)
    _patch_pipeline(monkeypatch, voice_src)

    def _boom(*a, **k):
        raise AssertionError("gate must not fire for a voice under the cap")
    monkeypatch.setattr(pipeline_main.render, "render", _boom)
    monkeypatch.setattr(pipeline_main.fetch_visuals, "fetch", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("reached fetch_visuals — gate correctly let a short voice through")))

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with pytest.raises(RuntimeError, match="reached fetch_visuals"):
        pipeline_main._run_after_topic("youtube", _TOPIC, out_dir, dry_run=False)


def test_post_render_gate_catches_a_drifted_render(tmp_path, monkeypatch):
    """render() is supposed to output min(SHORTS_MAX_SECONDS, tts+0.5) — if it
    (or a future caller) ever drifts from that, G39 must catch it on the real
    artifact rather than trust the call succeeded."""
    voice_src = _silence(tmp_path / "src_voice.mp3", 35.0)
    _patch_pipeline(monkeypatch, voice_src)
    # tts+0.5 = 35.5s expected; write a deliberately wrong-length output.
    monkeypatch.setattr(pipeline_main.render, "render",
                         lambda **k: write_stub_media(k["out_path"], 10.0))

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with pytest.raises(RuntimeError, match="duration invariant"):
        pipeline_main._run_after_topic("youtube", _TOPIC, out_dir, dry_run=False)
