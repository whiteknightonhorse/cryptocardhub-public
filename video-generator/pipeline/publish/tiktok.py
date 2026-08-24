"""TikTok publisher — dlvr.it Phase-1 transport.

Phase-2 path (TikTok Content Posting API direct) is documented in tiktok1/SKILL.md §13
but not implemented here; flag TT_PUBLISH_TRANSPORT=direct would route through it.
"""
from __future__ import annotations
import os
from typing import Any

import requests


def upload(*, public_url: str, title: str, caption: str,
           dry_run: bool = False) -> dict[str, Any]:
    api_key = os.environ.get("DLVRIT_API_KEY")
    route = os.environ.get("DLVRIT_ROUTE_TIKTOK")
    if dry_run or not api_key or not route:
        return {"dry_run": True, "public_url": public_url,
                "caption_chars": len(caption), "title": title}
    r = requests.post(
        "https://api.dlvrit.com/2.0/items/create",
        data={
            "api_key": api_key,
            "route_ids": route,
            "url": public_url,
            "title": title[:80],
            "description": caption,
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()
