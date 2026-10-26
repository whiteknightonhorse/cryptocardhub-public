"""G40 (T-0142-01) — an H1-produced video attaches to its own pSEO page.

Runs against the BUILT site (seo-site/dist), not the JSON sync artifacts:
sync-yt-videos.py can write a correct video-collection JSON and the render
still show nothing if the renderer's match key disagrees with the writer's
format — a field exists, no reader proves it arrived (Y53/Y54 shape). Only
the built HTML proves the embed actually reached the page.

THE LAW IS A LITERAL HERE (Y49): the two video IDs and the two page paths
are typed out, not read back from published.db / video-generator/output —
a test that re-derives its expectation from the same source the writer reads
moves with every mutation of that source and can never go red.
"""
from __future__ import annotations

from pathlib import Path

import pytest

DIST = Path(__file__).resolve().parent.parent / "dist"

# LAW — H1 videos published 2026-09-21 / 2026-09-23, each targeting its own
# pSEO page (T-0142 ruling, T-0142-01 acceptance row).
H1_ATTACHMENTS = [
    ("faq/who-can-get-ether-fi-cash", "xM8lcMhHOIU"),
    ("learn/how-does-ether-fi-cash-work", "v-Ef8lq0V4w"),
]


def _html(slug: str) -> str:
    page = DIST / slug / "index.html"
    if not page.exists():
        pytest.fail(
            f"{page} missing — build the site first: "
            f"cd seo-site && set -a && . ../.env && set +a && pnpm build"
        )
    return page.read_text(encoding="utf-8")


@pytest.mark.parametrize("slug,video_id", H1_ATTACHMENTS)
def test_h1_video_attaches_with_video_object(slug, video_id):
    html = _html(slug)
    assert video_id in html, f"{slug}/index.html does not embed {video_id} at all"
    assert '"@type":"VideoObject"' in html, f"{slug}/index.html has no VideoObject JSON-LD"
    # The VideoObject that carries THIS video's id, not merely any VideoObject
    # elsewhere on the page.
    idx = html.find('"@type":"VideoObject"')
    block = html[idx: idx + 2000]
    assert video_id in block, f"{slug}'s VideoObject block does not reference {video_id}"
