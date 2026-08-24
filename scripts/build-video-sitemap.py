#!/usr/bin/env python3
"""
build-video-sitemap.py — emit sitemap-videos.xml + register it in sitemap-index.xml.

Runs in deploy.sh AFTER `pnpm build`. Reads the videos content collection
(seo-site/src/content/videos/*.json) — same source the Astro pages use —
and writes:

  1. dist/sitemap-videos.xml  (Google Video Sitemap protocol)
  2. dist/sitemap-index.xml   (rewritten to include the new sub-sitemap)

Per https://developers.google.com/search/docs/crawling-indexing/sitemaps/video-sitemaps
the page in <loc> MUST contain the actual video player. We point each entry
at /videos/<id>/ which has the iframe. When a video is also embedded on a
pSEO article (target_pseo_slug set) we emit a SECOND <url> so both pages
are indexed as carrying the video.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
VIDEOS_DIR = ROOT / "seo-site" / "src" / "content" / "videos"
DIST = ROOT / "seo-site" / "dist"
SITE = "https://defycard.com"


def _load_videos() -> list[dict]:
    out: list[dict] = []
    for p in sorted(VIDEOS_DIR.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[video-sitemap] skip {p.name}: {e}", file=sys.stderr)
    return out


def _video_url_block(page_url: str, v: dict) -> str:
    """Return a <url> element with full <video:video> metadata."""
    lastmod = v.get("published_at") or datetime.now(timezone.utc).isoformat()
    title = escape(v["title"])[:100]
    description = escape(v.get("description") or v["title"])[:2048]
    thumb = escape(v["thumbnail_url"])
    player = escape(v["embed_url"])
    pubdate = escape(v["published_at"])

    parts = [
        '  <url>',
        f'    <loc>{escape(page_url)}</loc>',
        f'    <lastmod>{escape(lastmod)}</lastmod>',
        '    <video:video>',
        f'      <video:thumbnail_loc>{thumb}</video:thumbnail_loc>',
        f'      <video:title>{title}</video:title>',
        f'      <video:description>{description}</video:description>',
        f'      <video:player_loc allow_embed="yes">{player}</video:player_loc>',
        f'      <video:publication_date>{pubdate}</video:publication_date>',
        '      <video:family_friendly>yes</video:family_friendly>',
        '      <video:requires_subscription>no</video:requires_subscription>',
        '      <video:live>no</video:live>',
    ]
    if v.get("duration_s"):
        parts.append(f'      <video:duration>{int(v["duration_s"])}</video:duration>')
    for tag in (v.get("hashtags") or [])[:32]:  # Google caps at 32 tags
        parts.append(f'      <video:tag>{escape(tag)}</video:tag>')
    parts.append(f'      <video:uploader info="https://www.youtube.com/@defycard">DefyCard</video:uploader>')
    parts.append('    </video:video>')
    parts.append('  </url>')
    return "\n".join(parts)


def _build_video_sitemap(videos: list[dict]) -> str:
    blocks: list[str] = []
    for v in videos:
        # Primary canonical: /videos/<id>/
        blocks.append(_video_url_block(f"{SITE}/videos/{v['id']}/", v))
        # If the video is embedded on a pSEO article, register that page too
        if v.get("target_pseo_slug"):
            slug = v["target_pseo_slug"].strip("/")
            blocks.append(_video_url_block(f"{SITE}/{slug}/", v))

    body = "\n".join(blocks)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n'
        '        xmlns:video="http://www.google.com/schemas/sitemap-video/1.1">\n'
        f'{body}\n'
        '</urlset>\n'
    )


def _rewrite_sitemap_index(now_iso: str) -> None:
    """Add sitemap-videos.xml to sitemap-index.xml (idempotent).

    Astro emits an index referencing only sitemap-0.xml; we append our
    additional sub-sitemap. The format is forgiving — Google parses the
    union, and adding a second <sitemap> never breaks the original.
    """
    idx_path = DIST / "sitemap-index.xml"
    if not idx_path.exists():
        print(f"[video-sitemap] WARN: {idx_path} missing — Astro build incomplete?", file=sys.stderr)
        return
    existing = idx_path.read_text(encoding="utf-8")
    if "sitemap-videos.xml" in existing:
        return  # already registered
    new_entry = (
        f'<sitemap><loc>{SITE}/sitemap-videos.xml</loc>'
        f'<lastmod>{now_iso}</lastmod></sitemap>'
    )
    rewritten = existing.replace("</sitemapindex>", f"{new_entry}</sitemapindex>")
    idx_path.write_text(rewritten, encoding="utf-8")


def main() -> int:
    if not DIST.exists():
        print(f"[video-sitemap] dist/ missing at {DIST} — skip", file=sys.stderr)
        return 0
    videos = _load_videos()
    if not videos:
        print("[video-sitemap] no videos — emit empty sitemap-videos.xml")
    out = DIST / "sitemap-videos.xml"
    out.write_text(_build_video_sitemap(videos), encoding="utf-8")

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    _rewrite_sitemap_index(now_iso)

    print(
        f"[video-sitemap] wrote {out} with {len(videos)} videos; "
        f"index-of-sitemaps now references both sub-sitemaps"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
