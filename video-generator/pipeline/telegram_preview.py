"""Telegram preview gate.

Phase 1 (first 14 days): every rendered video → operator's TG bot for visual QA
before auto-publish. After 15-min idle, auto-approve.

Per polymarket ytl invariant #17: TG sendVideo MUST pass width/height/duration
or the mobile TG app crops vertical 1080×1920 to a square preview.
"""
from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import requests

API = "https://api.telegram.org"


def _probe(path: Path) -> dict[str, Any]:
    out = subprocess.check_output(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        timeout=20,
    )
    d = json.loads(out)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), {})
    return {
        "width":    int(v.get("width", 1080)),
        "height":   int(v.get("height", 1920)),
        "duration": int(float(d["format"]["duration"])),
    }


def send(video_path: Path, caption: str = "",
         parse_mode: str = "HTML",
         dry_run: bool = False) -> dict[str, Any]:
    token = os.environ.get("TG_BOT_TOKEN")
    chat = os.environ.get("TG_CHAT_ID")
    if dry_run or not token or not chat:
        return {"dry_run": True, "video_path": str(video_path)}
    meta = _probe(video_path)
    with video_path.open("rb") as f:
        r = requests.post(
            f"{API}/bot{token}/sendVideo",
            data={
                "chat_id": chat,
                "caption": caption,
                "parse_mode": parse_mode,
                "supports_streaming": "true",
                **{k: str(v) for k, v in meta.items()},
            },
            files={"video": f},
            timeout=300,
        )
    r.raise_for_status()
    return r.json()


def alert(text: str, dry_run: bool = False) -> None:
    """Quick text alert (publish failures, schedule misses)."""
    token = os.environ.get("TG_BOT_TOKEN")
    chat = os.environ.get("TG_CHAT_ID")
    if dry_run or not token or not chat:
        return
    requests.post(f"{API}/bot{token}/sendMessage",
                  data={"chat_id": chat, "text": text}, timeout=20)
