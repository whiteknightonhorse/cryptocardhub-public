"""CCH-SEO-24 — every /videos/<id>/ page in the BUILT dist has a meta
description, og:description, og:image, and a link to its resolving article.
Reads the build in DIST_DIR (default seo-site/dist); skips if none exists."""
import json
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
DIST = Path(os.environ.get("DIST_DIR", ROOT / "seo-site" / "dist"))
IDS = {r.stem for r in (ROOT / "seo-site/src/content/videos").glob("*.json")}
PAGES = [p for p in sorted((DIST / "videos").glob("*/index.html")) if p.parent.name in IDS] if DIST.exists() else []
pytestmark = pytest.mark.skipif(not PAGES, reason="no built dist/videos")


def _bad(pattern):
    return [p.parent.name for p in PAGES if not re.search(pattern, p.read_text())]


def test_meta_description_non_empty():
    assert _bad(r'<meta name="description" content="[^"]') == []


def test_og_description_non_empty():
    assert _bad(r'<meta property="og:description" content="[^"]') == []


def test_og_image_present():
    assert _bad(r'<meta property="og:image" content="https?://[^"]') == []


def test_resolving_guide_link_present():
    missing = []
    for rec in (ROOT / "seo-site/src/content/videos").glob("*.json"):
        slug = json.loads(rec.read_text()).get("target_pseo_slug")
        page = DIST / "videos" / rec.stem / "index.html"
        if slug and (DIST / slug / "index.html").exists() and page.exists():
            if f'href="/{slug}/"' not in page.read_text():
                missing.append(rec.stem)
    assert missing == []
