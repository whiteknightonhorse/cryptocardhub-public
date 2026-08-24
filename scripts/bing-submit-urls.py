#!/usr/bin/env python3
"""Y62 — spend the daily Bing URL-submission quota, round-robin across buckets.

Bing gives 100 URLs/day. Submitting them accelerates crawling, which is what
the keyword bank is waiting on: no query data exists until Bing has actually
seen the pages. Round-robin rather than bucket-order because the first run
spent all 100 on how-to purely because it sorted first — a quota spent on one
shape teaches Bing about one shape.

Remembers what it has already sent in state/bing-submitted.json so a page is
not re-submitted while others wait.
"""
from __future__ import annotations

import json
import os
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "seo-site" / "dist"
STATE = ROOT / "video-generator" / "state" / "bing-submitted.json"
SITE = "https://defycard.com"
DAILY = 100


def main() -> int:
    key = os.environ.get("BING_WEBMASTER_API_KEY")
    if not key:
        print("[bing-submit] BING_WEBMASTER_API_KEY not set — nothing done")
        return 0
    sent = set(json.loads(STATE.read_text())) if STATE.exists() else set()

    by_bucket: dict[str, list[str]] = defaultdict(list)
    for p in sorted(DIST.glob("*/*/index.html")):
        parts = str(p.relative_to(DIST)).split("/")
        url = f"{SITE}/{parts[0]}/{parts[1]}/"
        if url not in sent:
            by_bucket[parts[0]].append(url)

    batch: list[str] = []
    while len(batch) < DAILY and any(by_bucket.values()):
        for b in list(by_bucket):
            if not by_bucket[b]:
                del by_bucket[b]
                continue
            batch.append(by_bucket[b].pop(0))
            if len(batch) >= DAILY:
                break
    if not batch:
        print(json.dumps({"event": "bing_submit", "sent": 0,
                          "reason": "every built page has already been submitted"}))
        return 0

    body = json.dumps({"siteUrl": SITE, "urlList": batch}).encode()
    req = urllib.request.Request(
        f"https://ssl.bing.com/webmaster/api.svc/json/SubmitUrlBatch?apikey={key}",
        data=body, headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"event": "bing_submit", "sent": 0, "error": type(e).__name__}))
        return 1
    sent |= set(batch)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(sorted(sent), indent=0))
    spread: dict[str, int] = defaultdict(int)
    for u in batch:
        spread[u.split("/")[3]] += 1
    print(json.dumps({"event": "bing_submit", "sent": len(batch),
                      "total_known": len(sent), "spread": dict(spread)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
