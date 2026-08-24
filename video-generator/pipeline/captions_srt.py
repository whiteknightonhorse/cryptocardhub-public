"""Emit a YouTube-style SRT from word-level timestamps.

Used by `pipeline.main` post-render to attach an English caption track via
captions.insert (Y43). Source is the same word list `captions.align()` already
produced for burned-in caption rendering.

Cue strategy: 3-7 words per line, ≤3s per line, line break on >0.6s pause.
This matches what produces good readable Shorts captions in YT's player and
drives clean machine-translation to other languages.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path


def _ts(seconds: float) -> str:
    td = timedelta(seconds=max(0.0, float(seconds)))
    total = int(td.total_seconds())
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    ms = int(round((td.total_seconds() - total) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def words_to_srt(
    words: list[dict],
    *,
    max_words_per_line: int = 6,
    max_line_seconds: float = 3.0,
    pause_break_seconds: float = 0.6,
) -> str:
    """Group {word, start, end} dicts into SRT cues.

    Accepts either {word, start, end} or {text, offset, duration} shapes
    (Microsoft Edge WordBoundary uses the latter, with 100-ns ticks).
    """
    if not words:
        return ""

    norm: list[dict] = []
    for w in words:
        text = (w.get("word") or w.get("text") or "").strip()
        if not text:
            continue
        if "offset" in w:
            start = float(w["offset"]) / 10_000_000.0
            dur = float(w.get("duration", 0)) / 10_000_000.0
            norm.append({"word": text, "start": start, "end": start + dur})
        else:
            norm.append({
                "word": text,
                "start": float(w.get("start", 0)),
                "end": float(w.get("end", w.get("start", 0)) or 0),
            })

    if not norm:
        return ""

    cues: list[list[dict]] = []
    cur: list[dict] = [norm[0]]
    for prev, cur_w in zip(norm, norm[1:]):
        gap = cur_w["start"] - prev["end"]
        line_dur = cur_w["end"] - cur[0]["start"]
        if (
            len(cur) >= max_words_per_line
            or line_dur >= max_line_seconds
            or gap >= pause_break_seconds
        ):
            cues.append(cur)
            cur = [cur_w]
        else:
            cur.append(cur_w)
    if cur:
        cues.append(cur)

    out: list[str] = []
    for i, cue in enumerate(cues, start=1):
        text = " ".join(c["word"] for c in cue).strip()
        out.append(str(i))
        out.append(f"{_ts(cue[0]['start'])} --> {_ts(cue[-1]['end'])}")
        out.append(text)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def emit_srt_from_words(words: list[dict], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(words_to_srt(words), encoding="utf-8")
    return out_path
