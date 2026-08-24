#!/usr/bin/env python3
"""
upload-yt-captions.py — emit SRT from voice.timestamps.json + captions.insert.

Burned-in captions in our renders are great for autoplay-muted Shorts viewing,
but they are PIXELS — YouTube can't read them. A separate caption track
unlocks:
  - YT search indexing of spoken content
  - automated language translation (we get ES/PT/DE captions free for SEO)
  - accessibility (mandatory for some discovery surfaces)
  - audio-description signal for the algorithm

We already capture word-level timestamps via Microsoft Edge TTS WordBoundary
events (per Y23 — `voice.timestamps.json` sidecar in every render dir). Group
words into 3-7-word lines (~2-3s each), emit SRT, upload via captions API.

For existing publishes: scan publish_log + match output dir per topic_id.
For the trailer: hardcoded path.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VG = ROOT / "video-generator"


def _ts(seconds: float) -> str:
    """SRT timestamp: HH:MM:SS,mmm"""
    td = timedelta(seconds=seconds)
    total = int(td.total_seconds())
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    ms = int(round((td.total_seconds() - total) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def words_to_srt(words: list[dict], max_words_per_line: int = 6,
                 max_line_seconds: float = 3.0) -> str:
    """Group word-timestamps into SRT cues. Reset on long pauses (>0.6s)."""
    if not words:
        return ""
    cues: list[list[dict]] = []
    cur: list[dict] = []
    cur_start: float | None = None
    for w in words:
        word = w.get("word") or w.get("text") or ""
        start = float(w.get("start", w.get("t", 0)))
        end = float(w.get("end", start + 0.4))
        if not word.strip():
            continue
        norm = {"word": word, "start": start, "end": end}
        if cur_start is None:
            cur_start = start
            cur = [norm]
            continue
        prev_end = cur[-1]["end"]
        gap = start - prev_end
        line_dur = end - cur_start
        if (
            len(cur) >= max_words_per_line
            or line_dur >= max_line_seconds
            or gap >= 0.6
        ):
            cues.append(cur)
            cur = [norm]
            cur_start = start
        else:
            cur.append(norm)
    if cur:
        cues.append(cur)

    out: list[str] = []
    for i, cue in enumerate(cues, start=1):
        text = " ".join(c["word"] for c in cue).strip()
        out.append(str(i))
        out.append(f"{_ts(cue[0]['start'])} --> {_ts(cue[-1]['end'])}")
        out.append(text)
        out.append("")
    return "\n".join(out).strip() + "\n"


def find_render_dir(channel: str, ts: str, topic_id: int) -> Path | None:
    from datetime import datetime, timedelta as _td
    pub = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    for d in (pub.date(), (pub - _td(seconds=86400)).date()):
        p = VG / "output" / channel / d.isoformat() / f"topic_{topic_id}"
        if p.is_dir():
            return p
    return None


def upload_caption(yt, video_id: str, srt_text: str, language: str = "en",
                   name: str = "English") -> bool:
    """Upload an SRT track via captions.insert. Returns True on success."""
    from googleapiclient.http import MediaInMemoryUpload
    from googleapiclient.errors import HttpError
    body = {
        "snippet": {
            "videoId": video_id,
            "language": language,
            "name": name,
            "isDraft": False,
        }
    }
    media = MediaInMemoryUpload(srt_text.encode("utf-8"), mimetype="text/plain")
    try:
        resp = yt.captions().insert(
            part="snippet",
            body=body,
            media_body=media,
            sync=False,  # we're providing pre-aligned SRT, don't ask YT to re-sync
        ).execute()
        return True
    except HttpError as e:
        msg = str(e)
        # Idempotency: re-runs report 400 "Caption track already exists for this language"
        if "captionExists" in msg or "already exists" in msg.lower() or "409" in msg:
            print(f"    (caption already exists for {video_id}/{language}, skip)")
            return True
        print(f"    ! {video_id}: {msg[:200]}")
        return False


def main() -> int:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)

    # Auto-mode publishes from DB
    db = sqlite3.connect(str(VG / "state" / "published.db"))
    db.row_factory = sqlite3.Row
    rows = list(db.execute(
        "SELECT external_id, channel, ts, topic_id FROM publish_log WHERE status='ok'"
    ))

    targets: list[tuple[str, Path]] = []
    for r in rows:
        rd = find_render_dir(r["channel"], r["ts"], int(r["topic_id"]))
        if rd is None:
            print(f"  - {r['external_id']}: render dir gone — skip")
            continue
        ts_path = rd / "voice.timestamps.json"
        if not ts_path.exists():
            print(f"  - {r['external_id']}: no voice.timestamps.json — skip")
            continue
        targets.append((r["external_id"], ts_path))

    # Trailer (manual upload, special path)
    trailer_path = VG / "output" / "youtube" / "2026-05-08" / "trailer" / "voice.timestamps.json"
    if trailer_path.exists():
        targets.append(("L612uSs17T8", trailer_path))

    print(f"\nfound {len(targets)} caption candidates")

    ok = fail = 0
    for vid, ts_path in targets:
        # Verify video still on YT
        cur = yt.videos().list(part="status", id=vid).execute()
        if not cur.get("items"):
            print(f"  - {vid}: not on YT (deleted) — skip")
            continue
        words = json.loads(ts_path.read_text(encoding="utf-8"))
        # WordBoundary entries can be {word, offset, duration} (ms) or
        # {word, start, end} (sec). Normalize both shapes.
        norm: list[dict] = []
        for w in (words.get("words") if isinstance(words, dict) else words):
            if "offset" in w:  # ms shape
                start_s = float(w["offset"]) / 10_000_000  # 100-ns ticks
                dur_s = float(w.get("duration", 0)) / 10_000_000
                norm.append({"word": w.get("text") or w.get("word") or "",
                             "start": start_s, "end": start_s + dur_s})
            else:
                norm.append({"word": w.get("word") or w.get("text") or "",
                             "start": float(w.get("start", 0)),
                             "end": float(w.get("end", 0))})
        srt = words_to_srt(norm)
        if not srt.strip():
            print(f"  - {vid}: empty SRT — skip")
            continue
        # Persist sidecar so we can audit / re-use
        srt_path = ts_path.parent / "captions.en.srt"
        srt_path.write_text(srt, encoding="utf-8")
        result = upload_caption(yt, vid, srt)
        if result:
            ok += 1
            print(f"  ✓ {vid}: {len(srt.splitlines())} SRT lines uploaded")
        else:
            fail += 1
    print(f"\ndone: {ok} ok / {fail} fail")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
