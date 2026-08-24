"""Word-level caption alignment.

Source preference order:
  1. edge-tts WordBoundary sidecar (`<audio>.timestamps.json`) — GROUND TRUTH
     from Microsoft TTS engine, millisecond-accurate, no drift across start/
     middle/end of video. Default path since 2026-05-06.
  2. openai-whisper (Python) — only used if edge-tts sidecar missing AND
     whisper module installed.
  3. Uniform-distribution fallback — last resort; produces drift, especially
     on long voiceovers. Only kicks in if BOTH above unavailable.

Per shorts-analysis SKILL §6 + SA4 invariant: burned-in word-level captions
are the SOLE communication channel for ~85-95% of feed plays (sound-off).
Sync drift = brand-killing.
"""
from __future__ import annotations
import json
import subprocess
from pathlib import Path
from typing import Any


def _audio_duration_sec(path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", str(path)],
        timeout=20,
    )
    d = json.loads(out)
    return float(d["format"]["duration"])


def _redistribute_zero_durations(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """V10-7: if a word has zero duration, take time from the next word."""
    out = []
    for i, w in enumerate(words):
        start = float(w["start"])
        end = float(w["end"])
        if end <= start:
            end = start + 0.1
            if i + 1 < len(words):
                nxt = words[i + 1]
                nxt["start"] = max(end, float(nxt["start"]))
        out.append({"word": (w.get("word") or w.get("text", "")).strip(),
                    "start": start, "end": end})
    return out


def _try_edge_tts_sidecar(audio_path: Path) -> list[dict[str, Any]] | None:
    """Read the WordBoundary sidecar produced by tts_edge.synthesize()."""
    sidecar = audio_path.with_suffix(".timestamps.json")
    if not sidecar.exists():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, list) or not data:
        return None
    return _redistribute_zero_durations(data)


def _try_whisper(audio_path: Path) -> list[dict[str, Any]] | None:
    """Heavy fallback — only runs if openai-whisper installed."""
    try:
        import whisper  # type: ignore
    except ImportError:
        return None
    try:
        model = whisper.load_model("tiny.en", in_memory=True)
        result = model.transcribe(
            str(audio_path),
            word_timestamps=True,
            condition_on_previous_text=False,
            language="en",
        )
        words: list[dict[str, Any]] = []
        for seg in result.get("segments", []):
            for w in seg.get("words", []):
                words.append(w)
        if words:
            return _redistribute_zero_durations(words)
    except Exception:
        return None
    return None


def _uniform_align(audio_path: Path, voiceover_text: str) -> list[dict[str, Any]]:
    """Worst-case fallback. Produces drift on long voiceovers because real
    speech rate is non-uniform (pauses between sentences, longer words, etc.).
    """
    dur = max(0.5, _audio_duration_sec(audio_path))
    words = [w for w in voiceover_text.split() if w]
    if not words:
        return []
    per = dur / len(words)
    return [
        {"word": w, "start": i * per, "end": (i + 1) * per}
        for i, w in enumerate(words)
    ]


def align(audio_path: Path, voiceover_text: str) -> list[dict[str, Any]]:
    """Return word-level alignment.

    Tries edge-tts sidecar first (ground truth, default path), then whisper,
    then uniform distribution. Always returns a list (may be empty if every
    path fails).
    """
    out = _try_edge_tts_sidecar(audio_path)
    if out:
        return out
    out = _try_whisper(audio_path)
    if out:
        return out
    return _uniform_align(audio_path, voiceover_text)
