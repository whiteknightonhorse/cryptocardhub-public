"""Jamendo CC-BY licensed music fetcher.

License filter: cc-by or cc-by-sa ONLY (commercial-use clean).
Genre: upbeat, electronic.
Duck to -18 dB under voice (config.yaml:music.duck_to_db).
"""
from __future__ import annotations
import hashlib
import os
import random
import subprocess
from pathlib import Path
from typing import Any

import requests

_API = "https://api.jamendo.com/v3.0"
_ALLOWED_LICENSES = {"ccby", "ccbysa"}


def _key() -> str | None:
    return os.environ.get("JAMENDO_CLIENT_ID")


def _silent_track(out_path: Path, seconds: int = 30) -> None:
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-t", str(seconds), "-q:a", "9", str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=30)


def fetch(out_path: Path, target_seconds: int = 45,
          dry_run: bool = False, day_seed: str | None = None) -> dict[str, Any]:
    """Pick one Jamendo CC-BY track. Deterministic per UTC day (ytl invariant #7).

    Returns {path, license, track_id, name}. dry_run / no-key → silent stereo mp3.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if dry_run or not _key():
        _silent_track(out_path, target_seconds)
        return {"path": str(out_path), "license": "synthetic_silence",
                "track_id": "0", "name": "(dry-run)", "dry_run": True}

    seed = int(hashlib.sha256((day_seed or "").encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)

    r = requests.get(f"{_API}/tracks/", params={
        "client_id": _key(),
        "format": "json",
        "limit": 50,
        "fuzzytags": "upbeat+electronic",
        "audioformat": "mp32",
        "include": "musicinfo+licenses",
        "speed": "medium+high",
        "boost": "popularity_total",
    }, timeout=20)
    r.raise_for_status()
    candidates = []
    for t in r.json().get("results", []):
        license_url = (t.get("license_ccurl") or "").lower()
        # Heuristic: keep only cc-by / cc-by-sa
        if "by-nc" in license_url or "nd" in license_url:
            continue
        if not license_url:
            continue
        if t.get("audiodownload"):
            candidates.append(t)
    if not candidates:
        _silent_track(out_path, target_seconds)
        return {"path": str(out_path), "license": "synthetic_silence",
                "track_id": "0", "name": "(no Jamendo match)"}

    track = rng.choice(candidates)
    audio_url = track.get("audiodownload") or track.get("audio")
    audio = requests.get(audio_url, timeout=60)
    audio.raise_for_status()
    out_path.write_bytes(audio.content)
    return {
        "path": str(out_path),
        "license": track.get("license_ccurl"),
        "track_id": str(track.get("id")),
        "name": track.get("name"),
        "artist": track.get("artist_name"),
    }
