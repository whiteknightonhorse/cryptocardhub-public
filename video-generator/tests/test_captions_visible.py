"""G25 (Y52) — the captions the renderer generates actually reach the picture.

WHY THIS GATE EXISTS: `render.py` used to read an ambient env var
(`DEFY_NEW_DESIGN`) and, when it was set, swap the `ass=` subtitle filter for
`null`. One caller — `main.py`, YouTube branch — added the captions back as an
HTML overlay afterwards. Every other caller did not, and nothing said so. The
long-form pilot calls `render.render()` directly, so all five pilot videos
rendered with a valid, 24 KB, fully-populated `final.ass` sitting next to an
mp4 that never showed a single word. Instagram and TikTok were the same mine,
still asleep behind their disabled flags.

That is the Y52 failure shape exactly: the artefact was verified as PRESENT
(the .ass file exists and parses) and never as REACHING THE VIEWER — the same
confusion as a CTA that is in the description string but not clickable.

HOW IT IS MEASURED: comparing two renders would need thresholds on encoder
noise and would depend on the b-roll. Instead the background is CONTROLLED —
the clip is solid black, so the only thing in the frame that can emit light is
a caption. Max luma above a floor means text is on screen; near-zero means the
filter graph dropped it. No calibration, no second render.

The env var is FORCED ON here on purpose. A test that only passes on a clean
environment is blind to production, which is where the bug lived (lesson Y49).
"""
from __future__ import annotations

import json
import subprocess

import pytest

from pipeline import render

# LAW: a frame showing white caption text on black is nowhere near black.
# Empty black frames measure 0; a frame with burned text measures >200.
MIN_LUMA_WITH_CAPTIONS = 100.0

# Sampled while a word is on screen and well before the end-of-video CTA
# drawtext (the other thing in this render that emits light).
SAMPLE_AT_SECONDS = 2.0
CLIP_SECONDS = 12.0


def _black_clip(path, seconds=CLIP_SECONDS):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s=1080x1920:r=30",
         "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, check=True, timeout=120,
    )
    return path


def _silence(path, seconds=CLIP_SECONDS):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
         "-t", str(seconds), "-ar", "44100", "-ac", "1", str(path)],
        capture_output=True, check=True, timeout=120,
    )
    return path


def _words():
    """Word timings covering the sampled instant."""
    return [{"word": w, "start": 0.5 + i * 0.5, "end": 1.0 + i * 0.5}
            for i, w in enumerate(["CRYPTO", "CARD", "CASHBACK", "EXPLAINED",
                                   "RIGHT", "HERE", "NOW", "TODAY"])]


def _max_luma(path, at=SAMPLE_AT_SECONDS):
    """Brightest pixel of the frame at `at` seconds, 0-255."""
    stats = path.parent / f"{path.stem}.ymax.txt"
    subprocess.run(
        ["ffmpeg", "-v", "quiet", "-ss", str(at), "-i", str(path), "-frames:v", "1",
         "-vf", f"signalstats,metadata=print:key=lavfi.signalstats.YMAX:file={stats}",
         "-f", "null", "-"],
        capture_output=True, check=True, timeout=120,
    )
    for line in stats.read_text().splitlines():
        if "YMAX" in line:
            return float(line.rsplit("=", 1)[1])
    raise AssertionError(f"no YMAX measured for {path}")


def _render(tmp_path, **kw):
    voice = _silence(tmp_path / "voice.mp3")
    music = _silence(tmp_path / "music.mp3")
    clip = _black_clip(tmp_path / "clip.mp4")
    out = tmp_path / f"out{kw.get('burn_captions', True)}.mp4"
    render.render(
        voiceover_path=voice, music_path=music,
        clips=[{"kind": "video", "path": str(clip), "source": "test"}],
        words=_words(), ftc_overlay_path=None,
        target_seconds=CLIP_SECONDS - 2, out_path=out, platform="youtube",
        max_seconds=CLIP_SECONDS, **kw,
    )
    return out


@pytest.fixture(autouse=True)
def _production_env(monkeypatch):
    """Production has this set. The bug was invisible without it."""
    monkeypatch.setenv("DEFY_NEW_DESIGN", "1")


def test_captions_are_actually_in_the_picture(tmp_path):
    """The default path burns captions — regardless of any ambient env var."""
    luma = _max_luma(_render(tmp_path))
    assert luma >= MIN_LUMA_WITH_CAPTIONS, (
        f"frame at {SAMPLE_AT_SECONDS}s peaks at luma {luma} on a black "
        f"background — no caption reached the picture, even though the .ass "
        f"file was written. This is what shipped in the five Y52 pilots."
    )


def test_ass_file_existing_proves_nothing(tmp_path):
    """The cheap check that would have stayed green through the whole bug.

    Kept as a live demonstration: the sidecar is written in BOTH branches, so
    asserting on it measures nothing about what the viewer sees.
    """
    out = _render(tmp_path, burn_captions=False)
    assert out.with_suffix(".ass").is_file()
    assert _max_luma(out) < MIN_LUMA_WITH_CAPTIONS


def test_opting_out_is_explicit_not_ambient(tmp_path):
    """Captions may only be dropped by a caller that says so in the call —
    never by an environment variable the renderer reads behind its back."""
    assert _max_luma(_render(tmp_path, burn_captions=False)) < MIN_LUMA_WITH_CAPTIONS
    assert _max_luma(_render(tmp_path, burn_captions=True)) >= MIN_LUMA_WITH_CAPTIONS


def test_longform_asks_for_burned_captions(tmp_path, monkeypatch):
    """The pilot path must not opt out — it has no step 6b to add them back."""
    seen = {}

    def _spy(**kw):
        seen.update(kw)
        return kw["out_path"]

    monkeypatch.setattr(render, "render", _spy)
    from pipeline import longform
    monkeypatch.setattr(longform, "out_dir", lambda slug, day=None: tmp_path / slug)
    monkeypatch.setattr(longform, "probe_duration", lambda p: 200.0)
    monkeypatch.setattr(longform.tts_edge, "synthesize", lambda *a, **k: {"voice": "x"})
    monkeypatch.setattr(longform.fetch_visuals, "fetch",
                        lambda *a, **k: [{"kind": "photo", "path": "p", "source": "t"}])
    monkeypatch.setattr(longform.fetch_music, "fetch", lambda *a, **k: {"track": "t"})
    monkeypatch.setattr(longform.captions, "align", lambda *a, **k: _words())
    try:
        longform.render_one("is-redotpay-legit", dry_run=True, day="2026-08-11")
    except Exception:
        pass  # only the render call's arguments are under test
    assert seen.get("burn_captions", True) is True, (
        "long-form opted out of burned captions but has no HTML overlay step"
    )
