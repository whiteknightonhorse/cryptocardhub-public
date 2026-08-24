"""Edge TTS — direct library use with WordBoundary capture.

CRITICAL upgrade 2026-05-06: switched from subprocess(`edge-tts` CLI) to
direct use of the edge_tts Python library. This unlocks WordBoundary events
emitted by Microsoft's TTS engine — ground-truth millisecond timestamps for
each spoken word. Caption sync (per shorts-analysis SKILL §6 + SA4 invariant)
now matches the audio waveform exactly, with NO drift between start, middle,
and end of the video.

Output:
  voice.mp3                      — raw audio (rotated voice, rate-adjusted)
  voice.timestamps.json          — sidecar consumed by captions.py:
                                   [{"word": str, "start": float_s, "end": float_s}, ...]

Voice rotation persists in state/voice_rotation.json (round-robin).
Per-platform rate override (TikTok = +0% for US-Female; YT/IG = -5%).

Async edge_tts wrapped in asyncio.run() so the rest of the pipeline stays sync.
"""
from __future__ import annotations
import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

from . import _config

# 15-voice pool per polymarket vg4 §6
VOICE_POOL: list[str] = [
    "en-US-JennyNeural", "en-US-AriaNeural", "en-US-MichelleNeural",
    "en-US-SaraNeural",  "en-US-NancyNeural",
    "en-US-GuyNeural", "en-US-DavisNeural", "en-US-TonyNeural",
    "en-US-BrandonNeural", "en-US-EricNeural",
    "en-GB-LibbyNeural", "en-GB-SoniaNeural", "en-GB-MaisieNeural",
    "en-GB-RyanNeural", "en-GB-ThomasNeural",
]

_US_FEMALE = {"en-US-JennyNeural", "en-US-AriaNeural", "en-US-MichelleNeural",
              "en-US-SaraNeural", "en-US-NancyNeural"}

_STATE = _config.root() / "state" / "voice_rotation.json"

# Microsoft TTS: WordBoundary offset/duration are in 100-nanosecond units.
_HUNDRED_NS_PER_SEC = 10_000_000


def _next_voice() -> str:
    """Round-robin from state/voice_rotation.json. Atomic write."""
    if _STATE.exists():
        st = json.loads(_STATE.read_text(encoding="utf-8"))
    else:
        st = {"voices": VOICE_POOL, "idx": 0}
    voices = st.get("voices") or VOICE_POOL
    idx = (st.get("idx", 0)) % len(voices)
    voice = voices[idx]
    st["idx"] = (idx + 1) % len(voices)
    st["voices"] = voices
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2), encoding="utf-8")
    tmp.replace(_STATE)
    return voice


def _rate_for(voice: str, platform: str) -> str:
    """Per polymarket vg4 §6 + tiktok1 §7: TikTok US-F = +0%; everywhere else = -5%."""
    if platform == "tiktok" and voice in _US_FEMALE:
        return "+0%"
    return "-5%"


async def _synthesize_async(text: str, voice: str, rate: str,
                            audio_path: Path, ts_path: Path) -> dict:
    """Drive edge_tts.Communicate streaming + capture WordBoundary events.

    Returns metadata dict. Audio bytes written to audio_path; word timestamps
    written to ts_path as JSON list.
    """
    import edge_tts  # noqa: WPS433 — local import keeps top-level light

    # edge-tts 7.x defaults to SentenceBoundary (whole sentences). For
    # caption-level word-by-word sync we MUST request WordBoundary.
    communicate = edge_tts.Communicate(
        text=text, voice=voice, rate=rate, boundary="WordBoundary",
    )
    audio_chunks: list[bytes] = []
    word_boundaries: list[dict[str, float]] = []
    last_err: Exception | None = None

    for attempt in (1, 2):
        try:
            audio_chunks.clear()
            word_boundaries.clear()
            async for chunk in communicate.stream():
                ctype = chunk.get("type")
                if ctype == "audio":
                    audio_chunks.append(chunk["data"])
                elif ctype == "WordBoundary":
                    start_s = chunk["offset"] / _HUNDRED_NS_PER_SEC
                    dur_s = chunk["duration"] / _HUNDRED_NS_PER_SEC
                    word_boundaries.append({
                        "word": chunk["text"],
                        "start": round(start_s, 4),
                        "end": round(start_s + dur_s, 4),
                    })
            break
        except Exception as e:
            last_err = e
            if attempt == 2:
                raise RuntimeError(
                    f"edge-tts streaming failed twice: {type(e).__name__}: {e}"
                ) from e

    if not audio_chunks:
        raise RuntimeError(
            f"edge-tts returned 0 audio bytes after retry; last err: {last_err!r}"
        )

    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"".join(audio_chunks))
    ts_path.write_text(json.dumps(word_boundaries, indent=2), encoding="utf-8")

    return {
        "voice": voice,
        "rate": rate,
        "path": str(audio_path),
        "timestamps_path": str(ts_path),
        "word_count": len(word_boundaries),
    }


def synthesize(voiceover: str, platform: str, out_path: Path,
               voice: str | None = None, dry_run: bool = False,
               dry_run_seconds: float = 1.0) -> dict[str, Any]:
    """Run edge-tts library streaming for `voiceover`, emit `.mp3` at out_path.
    Saves a `.timestamps.json` sidecar next to the mp3 with WordBoundary events.

    `voice=None` triggers auto-rotation. Pass an explicit voice to pin
    (e.g., per-channel stable voice).

    On dry_run=True: writes a silence placeholder (`dry_run_seconds` long, 1 s
    by default) so downstream steps can run without the edge-tts binary
    installed; sidecar is an empty list. The long-form pilot passes the
    estimated narration length so its duration gate exercises a real render
    rather than a 1-second stub.
    """
    voice = voice or _next_voice()
    rate = _rate_for(voice, platform)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ts_path = out_path.with_suffix(".timestamps.json")

    if dry_run:
        # Silence placeholder via ffmpeg (no edge-tts call)
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-t", f"{max(0.1, dry_run_seconds):.3f}",
            "-ar", "44100", "-ac", "1", "-q:a", "9", str(out_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=30)
        ts_path.write_text("[]", encoding="utf-8")
        return {"voice": voice, "rate": rate, "path": str(out_path),
                "timestamps_path": str(ts_path), "dry_run": True}

    # Real edge-tts library call (NOT subprocess CLI). Captures WordBoundary
    # events alongside audio bytes for ground-truth caption alignment.
    return asyncio.run(_synthesize_async(voiceover, voice, rate, out_path, ts_path))
