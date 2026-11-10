"""CCH-SEO-22 gate — built dist, not sources (Y50). Law literals in the test (Y49)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

DIST = Path(__file__).resolve().parent.parent / "dist"
pytestmark = pytest.mark.skipif(
    not (DIST / "robots.txt").is_file() or not (DIST / "index.html").is_file(),
    reason="seo-site/dist absent — run pnpm build first")


def _generic_group():
    lines, grp, on = (DIST / "robots.txt").read_text().splitlines(), [], False
    for ln in lines:
        if ln.startswith("User-agent:"):
            if on:
                break
            on = ln.strip() == "User-agent: *"
        if on:
            grp.append(ln.strip())
    return grp


def test_generic_group_allows_astro_assets():
    assert "Allow: /_astro/" in _generic_group()


def test_stylesheet_lives_under_astro():
    html = (DIST / "index.html").read_text()
    hrefs = re.findall(r'<link rel="stylesheet" href="(/[^"]+)"', html)
    assert hrefs and all(h.startswith("/_astro/") for h in hrefs)
