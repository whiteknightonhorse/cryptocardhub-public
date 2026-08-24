"""Instagram Reels publisher — dlvr.it Phase-1 transport.

Phase-2 path (Meta Graph API direct) is documented in instagram1/SKILL.md §13
but not implemented here; flag IG_PUBLISH_TRANSPORT=graph would route through it.

dlvr.it accepts a public HTTPS video URL — pipeline must upload the rendered mp4
to a transient bucket (e.g., Cloudflare R2 with signed URL) before this call.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any

import requests


def upload(*, public_url: str, title: str, caption: str,
           dry_run: bool = False) -> dict[str, Any]:
    """Send to dlvr.it route configured for Instagram Reels."""
    api_key = os.environ.get("DLVRIT_API_KEY")
    route = os.environ.get("DLVRIT_ROUTE_IG")
    if dry_run or not api_key or not route:
        return {"dry_run": True, "public_url": public_url,
                "caption_chars": len(caption), "title": title}
    r = requests.post(
        "https://api.dlvrit.com/2.0/items/create",
        data={
            "api_key": api_key,
            "route_ids": route,
            "url": public_url,
            "title": title[:100],
            "description": caption,
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()
