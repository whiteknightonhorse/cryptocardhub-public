"""Shared fixtures. Every test runs against tmp_path — no test may read or
write the live state/ or logs/ of a running channel."""
from __future__ import annotations
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipeline import analytics, pattern_selector


def write_stub_media(path, seconds: float):
    """Write a short silent audio placeholder to `path` — codec chosen by
    extension so ffmpeg's muxer accepts it (mp3 wants libmp3lame, mp4 wants
    aac). One place for every test that mocks a TTS/render step but still
    needs a real file on disk for a downstream ffprobe-duration gate
    (T-0142-02, G39) — a second hand-rolled copy is the slugify trap."""
    path = Path(path)
    codec = "aac" if path.suffix == ".mp4" else "libmp3lame"
    args = ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", f"{seconds}", "-c:a", codec]
    if codec == "libmp3lame":
        args += ["-q:a", "9"]
    args.append(str(path))
    subprocess.run(args, capture_output=True, check=True, timeout=120)
    return path


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Redirect every file analytics/pattern_selector touch into tmp_path."""
    state_dir = tmp_path / "state"
    log_dir = tmp_path / "logs" / "analytics"
    state_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)

    monkeypatch.setattr(analytics, "_ROOT", tmp_path)
    monkeypatch.setattr(analytics, "_VIDEO_LOG_PATH", state_dir / "video_pattern_log.jsonl")
    monkeypatch.setattr(analytics, "_LOG_DIR", log_dir)
    monkeypatch.setattr(pattern_selector, "_STATE_PATH", state_dir / "creative_patterns_state.json")
    # No Telegram from tests, ever.
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TG_CHAT_ID", raising=False)
    return tmp_path


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def write_scored_events(log_dir, records, base_ts=None):
    """Append synthetic `scored` events the way analytics._log() writes them."""
    base_ts = base_ts or now_utc()
    path = log_dir / f"{base_ts.strftime('%Y-%m-%d')}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for i, r in enumerate(records):
            rec = {
                "ts": (base_ts - timedelta(minutes=i)).isoformat(),
                "event": "scored",
                "platform": r.get("platform", "youtube"),
                "video_id": r["video_id"],
                "views": r["views"],
                "retention": r.get("retention", 0.0),
                "success": r.get("success", False),
            }
            fh.write(json.dumps(rec) + "\n")
    return path


def read_video_log(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def read_events(log_dir, event):
    out = []
    for p in sorted(log_dir.glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("event") == event:
                out.append(rec)
    return out
