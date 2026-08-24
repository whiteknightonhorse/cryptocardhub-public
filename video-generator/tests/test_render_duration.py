"""Y49 P1-1 gate — speech, not a constant, sets the length of the video.

This gate renders real mp4s with ffmpeg and measures them; a formula-only
test would not have caught the 38 s floor, because the floor was applied
exactly where the formula lived.

Tolerance 0.10 s: over 32 real renders on disk (output/**/final.mp4, the ones
not pinned at the old 38 s floor) the worst |final - (voice + 0.5)| was
0.065 s — frame quantisation at 30 fps (0.033 s) plus AAC frame granularity
(1024 samples @ 44.1 kHz = 0.023 s). 0.10 s = 3 video frames, 1.5x the
observed worst case.
"""
from __future__ import annotations
import json
import subprocess

import pytest

from pipeline import render

TOL = 0.10


def _probe(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        timeout=30,
    )
    return float(json.loads(out)["format"]["duration"])


def _silence(path, seconds):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-t", f"{seconds}", "-c:a", "libmp3lame", "-q:a", "9", str(path)],
        capture_output=True, check=True, timeout=120,
    )
    return path


def _color_png(path, color="blue"):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=1080x1920",
         "-frames:v", "1", str(path)],
        capture_output=True, check=True, timeout=60,
    )
    return path


def _render(tmp_path, tts_seconds, target_seconds):
    voice = _silence(tmp_path / "voice.mp3", tts_seconds)
    music = _silence(tmp_path / "music.mp3", 90)
    clips = [{"kind": "photo", "path": str(_color_png(tmp_path / f"c{i}.png", c))}
             for i, c in enumerate(("blue", "red"))]
    out = render.render(
        voiceover_path=voice, music_path=music, clips=clips, words=[],
        ftc_overlay_path=None, target_seconds=target_seconds,
        out_path=tmp_path / "final.mp4",
    )
    return _probe(voice), _probe(out)


@pytest.mark.parametrize("tts_seconds,target_seconds", [(17.0, 37.5), (5.0, 37.5)])
def test_video_never_outlives_the_voice(tmp_path, tts_seconds, target_seconds):
    """The retention case: a short script must yield a short video.

    target_seconds is deliberately the live YouTube value (37.5 s) — it must
    not stretch the render, and neither may any floor.
    """
    tts, video = _render(tmp_path, tts_seconds, target_seconds)
    assert video <= tts + 0.5 + TOL, (
        f"video {video:.3f}s outlives voice {tts:.3f}s + 0.5 by more than {TOL}s"
    )
    assert video >= tts + 0.5 - TOL, (
        f"video {video:.3f}s is cut short of voice {tts:.3f}s + 0.5"
    )


def test_sixty_second_cap_still_holds(tmp_path):
    """Output spec caps a Short at 60 s — the cap is the only clamp left."""
    _tts, video = _render(tmp_path, 62.0, 37.5)
    assert abs(video - 60.0) <= TOL, f"expected the 60 s cap, got {video:.3f}s"
