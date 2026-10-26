"""ElevenLabs TTS adapter — same `synthesize(...)` contract as tts_edge.py.

Shared-tenant quota (E-19, FABLE_DEFYCARD_PART2.md §8.1 / OPERATOR_ANSWERS.md
§8): `ELEVENLABS_API_KEY` and its 41,502 char/month limit are shared with
another tenant on this machine (other voices already sit in the account
library). The guard below reads the LIVE `/v1/user/subscription`
`character_count` before every call — never a local counter, because a local
counter cannot see the other tenant's usage (Y49's law: the instrument must
be the source of truth, not a copy of it).

Voice: one fixed brand voice, no cloning, no A/B (OPERATOR_ANSWERS.md §8.2).
On quota exhaustion the slot is refused — NOT silently rerouted to Edge; the
brand voice is singular.
"""
from __future__ import annotations
import base64
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import requests

from . import telegram_preview

API = "https://api.elevenlabs.io"
MODEL_ID = "eleven_multilingual_v2"
OUTPUT_FORMAT = "mp3_44100_128"
QUOTA_RESERVE = 5_000


def _http_get(url: str, headers: dict[str, str]) -> requests.Response:
    return requests.get(url, headers=headers, timeout=20)


def _http_post(url: str, headers: dict[str, str], body: dict[str, Any],
               params: dict[str, str]) -> requests.Response:
    return requests.post(url, headers=headers, json=body, params=params, timeout=120)


def _headers(api_key: str) -> dict[str, str]:
    return {"xi-api-key": api_key, "Content-Type": "application/json"}


def _check_quota(api_key: str, text_len: int) -> dict[str, int]:
    """Live subscription check. Raises RuntimeError("eleven_quota: ...") when
    the reserve would be eaten. No local counter — see module docstring."""
    r = _http_get(f"{API}/v1/user/subscription", _headers(api_key))
    r.raise_for_status()
    sub = r.json()
    limit = int(sub["character_limit"])
    count = int(sub["character_count"])
    remaining = limit - count
    if remaining < text_len + QUOTA_RESERVE:
        msg = (
            f"eleven_quota: {remaining} chars remaining (limit={limit}, "
            f"count={count}, quota shared with another tenant) — need "
            f"{text_len} + {QUOTA_RESERVE} reserve"
        )
        telegram_preview.alert(f"[tts_eleven] {msg}")
        raise RuntimeError(msg)
    return {"limit": limit, "count": count, "remaining": remaining}


def _words_from_alignment(alignment: dict[str, Any]) -> list[dict[str, Any]]:
    """Group ElevenLabs' character-level `alignment` into word boundaries —
    the same shape tts_edge.py's WordBoundary sidecar emits, so captions.py
    reads either provider's sidecar unmodified."""
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    words: list[dict[str, Any]] = []
    buf = ""
    w_start = 0.0
    w_end = 0.0
    for ch, cs, ce in zip(chars, starts, ends):
        if ch.isspace():
            if buf:
                words.append({"word": buf, "start": round(w_start, 4), "end": round(w_end, 4)})
                buf = ""
            continue
        if not buf:
            w_start = cs
        buf += ch
        w_end = ce
    if buf:
        words.append({"word": buf, "start": round(w_start, 4), "end": round(w_end, 4)})
    return words


def synthesize(voiceover: str, platform: str, out_path: Path,
               voice: str | None = None, dry_run: bool = False,
               dry_run_seconds: float = 1.0) -> dict[str, Any]:
    """Same signature as tts_edge.synthesize(). `platform` is accepted for
    interface parity only — ElevenLabs uses one fixed brand voice, no
    per-platform rate override.

    `voice=None` reads `ELEVENLABS_VOICE_ID` from the environment. Refuses
    before any network call if that resolves empty, and again (still before
    the TTS call) if the live quota can't cover `len(voiceover) + 5000`.
    """
    ts_path = out_path.with_suffix(".timestamps.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if dry_run:
        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
               "-t", f"{max(0.1, dry_run_seconds):.3f}",
               "-ar", "44100", "-ac", "1", "-q:a", "9", str(out_path)]
        subprocess.run(cmd, check=True, capture_output=True, timeout=30)
        ts_path.write_text("[]", encoding="utf-8")
        return {"voice": voice, "path": str(out_path),
                "timestamps_path": str(ts_path), "dry_run": True}

    voice_id = voice or os.environ.get("ELEVENLABS_VOICE_ID")
    if not voice_id:
        raise RuntimeError("eleven_no_voice: ELEVENLABS_VOICE_ID is unset — refusing before any network call")
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("eleven_no_key: ELEVENLABS_API_KEY is unset — refusing before any network call")

    quota = _check_quota(api_key, len(voiceover))

    r = _http_post(
        f"{API}/v1/text-to-speech/{voice_id}/with-timestamps",
        _headers(api_key),
        {"text": voiceover, "model_id": MODEL_ID},
        {"output_format": OUTPUT_FORMAT},
    )
    r.raise_for_status()
    payload = r.json()
    out_path.write_bytes(base64.b64decode(payload["audio_base64"]))

    words = _words_from_alignment(payload.get("alignment") or {})
    ts_path.write_text(json.dumps(words, indent=2), encoding="utf-8")

    chars_before = quota["count"]
    chars_after = chars_before + len(voiceover)
    print(f"[tts_eleven] chars before={chars_before} after={chars_after} "
          f"limit={quota['limit']} (shared quota)")

    return {
        "voice": voice_id,
        "path": str(out_path),
        "timestamps_path": str(ts_path),
        "word_count": len(words),
        "chars_before": chars_before,
        "chars_after": chars_after,
        "quota_limit": quota["limit"],
    }
