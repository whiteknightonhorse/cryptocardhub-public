"""CCH-SEO-32 — a video page either canonicalises to the article that embeds
it (hero) or is noindex with a self-canonical. Reads the BUILT dist."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
IDS = sorted(p.stem for p in (ROOT / "src" / "content" / "videos").glob("*.json"))
SITE = "https://defycard.com"


def _html(rel: str) -> str:
    p = DIST / rel
    if not p.exists():
        pytest.fail(f"{p} missing — build the site first: cd seo-site && pnpm build")
    return p.read_text(encoding="utf-8")


def _canon(html: str) -> str:
    return re.search(r'<link rel="canonical" href="([^"]+)"', html).group(1)


def _classify():
    hero, noidx = {}, []
    for vid in IDS:
        h = _html(f"videos/{vid}/index.html")
        c, n = _canon(h), 'content="noindex, follow"' in h
        if c != f"{SITE}/videos/{vid}/":
            assert not n, vid
            hero[vid] = c
        else:
            assert n, vid
            noidx.append(vid)
    return hero, noidx


def test_every_video_page_is_hero_or_noindex():
    hero, noidx = _classify()
    assert len(hero) >= 20 and len(noidx) >= 200
    assert len(hero) + len(noidx) == len(IDS)


def test_hero_canonical_target_really_embeds_the_video():
    hero, _ = _classify()
    for vid, canon in hero.items():
        art = _html(canon.removeprefix(SITE).strip("/") + "/index.html")
        blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', art, re.S)
        assert any("VideoObject" in b and vid in b for b in blocks), (vid, canon)


def test_literal_hero():
    assert _canon(_html("videos/xM8lcMhHOIU/index.html")) == f"{SITE}/faq/who-can-get-ether-fi-cash/"


def test_sitemaps():
    hero, _ = _classify()
    s0 = _html("sitemap-0.xml")
    assert f"{SITE}/videos/</loc>" in s0
    assert not re.search(r"/videos/(%s)/" % "|".join(map(re.escape, IDS)), s0)
    locs = re.findall(r"<loc>([^<]+)</loc>", _html("sitemap-videos.xml"))
    assert len(locs) == len(hero)
    assert not any("/videos/" in u for u in locs)
