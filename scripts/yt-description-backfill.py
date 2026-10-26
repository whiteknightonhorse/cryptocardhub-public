#!/usr/bin/env python3
"""yt-description-backfill.py — append a defycard.com deep link to public videos' descriptions.

CCH-SEO-13. Edits metadata of already-public videos only: no upload, no privacy,
title, tag, comment or deletion. Every API call goes through pipeline.publish.youtube._yt()
(Y45, per-channel SOCKS proxy). utm_medium is decided by duration (Y52).

  yt-description-backfill.py                 # dry-run: plan TSV + counts
  yt-description-backfill.py --apply --max 5 # first live run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEOS_DIR = ROOT / "seo-site/src/content/videos"
ABOUT_FILE = ROOT / "scripts/yt-channel-descriptions/DEFYCARD.txt"
PLAN_TSV = ROOT / "logs/youtube/description-backfill-plan.tsv"
LOG_JSONL = ROOT / "logs/youtube/description-backfill.jsonl"

SHORTS_MAX_S = 180   # Y52: <=180 s is a Short
LONGFORM_MIN_S = 185  # Y52: 5 s clearance from the boundary
MAX_DESC_CHARS = 5000
DAILY_UPDATE_CAP = 100
SITE = "https://defycard.com"

_DUR = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def duration_s(iso: str) -> int | None:
    m = _DUR.match(iso or "")
    if not m:
        return None
    h, mi, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + s


def medium_for(iso: str) -> str:
    s = duration_s(iso)
    if s is None:
        return "dead_zone"
    if s <= SHORTS_MAX_S:
        return "shorts"
    if s >= LONGFORM_MIN_S:
        return "longform"
    return "dead_zone"


def page_path(video_id: str, videos_dir: Path = VIDEOS_DIR) -> str | None:
    """The one place that decides where a video's link points."""
    f = videos_dir / f"{video_id}.json"
    if not f.exists():
        return None
    slug = json.loads(f.read_text()).get("target_pseo_slug")
    return f"/{slug}/" if slug else f"/videos/{video_id}/"


def needs_link(description: str, path: str) -> bool:
    return f"defycard.com{path}" not in description


def new_description(description: str, path: str, medium: str, video_id: str) -> str:
    return (f"{description}\n\n👉 Full breakdown: {SITE}{path}"
            f"?utm_source=youtube&utm_medium={medium}&utm_content=yt-backfill-{video_id}")


def classify(item: dict, videos_dir: Path = VIDEOS_DIR) -> dict:
    """item: {id, published, duration (ISO), privacy, description}. Returns plan row."""
    row = {"id": item["id"], "published": item.get("published", ""),
           "duration_s": duration_s(item.get("duration", "")), "medium": "", "path": "", "action": ""}
    if item.get("privacy") != "public":
        row["action"] = "not_public"
        return row
    path = page_path(item["id"], videos_dir)
    if path is None:
        row["action"] = "no_page"
        return row
    row["path"] = path
    row["medium"] = medium_for(item.get("duration", ""))
    if row["medium"] == "dead_zone":
        row["action"] = "dead_zone"
    elif not needs_link(item["description"], path):
        row["action"] = "skip_has_link"
    elif len(new_description(item["description"], path, row["medium"], item["id"])) > MAX_DESC_CHARS:
        row["action"] = "too_long"
    else:
        row["action"] = "update"
    return row


def inventory(yt) -> list[dict]:
    """channels.list -> uploads playlist -> playlistItems (50/page) -> videos.list (50/call)."""
    ch = yt.channels().list(part="contentDetails", mine=True).execute()
    uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, token = [], None
    while True:
        r = yt.playlistItems().list(part="contentDetails", playlistId=uploads,
                                    maxResults=50, pageToken=token).execute()
        ids += [i["contentDetails"]["videoId"] for i in r.get("items", [])]
        token = r.get("nextPageToken")
        if not token:
            break
    items = []
    for i in range(0, len(ids), 50):
        r = yt.videos().list(part="snippet,status,contentDetails", id=",".join(ids[i:i + 50])).execute()
        for v in r.get("items", []):
            items.append({"id": v["id"], "published": v["snippet"].get("publishedAt", ""),
                          "duration": v["contentDetails"].get("duration", ""),
                          "privacy": v["status"].get("privacyStatus"),
                          "description": v["snippet"].get("description", ""),
                          "snippet": v["snippet"]})
    return items


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _is_quota(e: Exception) -> bool:
    resp = getattr(e, "resp", None)
    return getattr(resp, "status", None) == 403 and b"quotaExceeded" in (getattr(e, "content", b"") or b"")


def update_body(snippet: dict, description: str) -> dict:
    """videos.update wipes absent snippet fields: send the FULL snippet, only description replaced."""
    return {"snippet": {**snippet, "description": description}}


def apply_updates(yt, items: list[dict], rows: list[dict], max_n: int, log_path: Path = LOG_JSONL) -> tuple[int, int, bool]:
    """Returns (done, remaining, quota_hit)."""
    by_id = {i["id"]: i for i in items}
    todo = [r for r in rows if r["action"] == "update"]
    done = 0
    for r in todo[:max_n]:
        it = by_id[r["id"]]
        desc = new_description(it["description"], r["path"], r["medium"], r["id"])
        body = update_body(it["snippet"], desc)
        body["id"] = r["id"]
        try:
            yt.videos().update(part="snippet", body=body).execute()
        except Exception as e:
            if _is_quota(e):
                return done, len(todo) - done, True
            raise
        done += 1
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as f:
            f.write(json.dumps({"id": r["id"], "before_sha256": _sha(it["description"]),
                                "after_sha256": _sha(desc),
                                "ts": datetime.now(timezone.utc).isoformat()}) + "\n")
    return done, len(todo) - done, False


def updates_today(log_path: Path = LOG_JSONL) -> int:
    if not log_path.exists():
        return 0
    today = datetime.now(timezone.utc).date().isoformat()
    return sum(1 for ln in log_path.read_text().splitlines() if f'"ts": "{today}' in ln)


def about_step(yt, apply: bool, about_file: Path = ABOUT_FILE) -> str:
    """GET-merge-PUT brandingSettings; never built from scratch."""
    ch = yt.channels().list(part="brandingSettings", mine=True).execute()["items"][0]
    bs = ch["brandingSettings"]
    if SITE in bs.get("channel", {}).get("description", ""):
        return "about: ok"
    if not apply:
        return "about: missing_link (would update with --apply)"
    bs.setdefault("channel", {})["description"] = about_file.read_text().strip()
    yt.channels().update(part="brandingSettings", body={"id": ch["id"], "brandingSettings": bs}).execute()
    return "about: updated"


def write_plan(rows: list[dict], path: Path = PLAN_TSV) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["id", "published", "duration_s", "medium", "path", "action"]
    path.write_text("\n".join("\t".join(str(r[c]) for c in cols) for r in [dict(zip(cols, cols))] + rows) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max", type=int, default=100)
    a = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    sys.path.insert(0, str(ROOT / "video-generator"))
    from pipeline.publish.youtube import _yt
    yt = _yt()
    items = inventory(yt)
    rows = [classify(i) for i in items]
    write_plan(rows)
    print(json.dumps(Counter(r["action"] for r in rows), sort_keys=True))
    if not a.apply:
        print(about_step(yt, False))
        return 0
    n = max(0, min(a.max, DAILY_UPDATE_CAP - updates_today()))
    done, remaining, quota = apply_updates(yt, items, rows, n)
    print(f"updated {done}, remaining update rows {remaining}")
    print(about_step(yt, True))
    if quota:
        print(f"quotaExceeded: stopped, {remaining} updates remaining")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
