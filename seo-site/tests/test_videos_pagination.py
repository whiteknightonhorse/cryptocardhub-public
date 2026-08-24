"""G27 — /videos/ is paginated, and every video stays reachable.

Why this gate exists: the index rendered all 206 cards into one 150 KB
document and grew with every upload. Pagination is cheap to get subtly wrong
in ways that look fine on page 1 — the same twenty cards repeated on every
page, or pages 2..N built but empty — and both failures are invisible unless
something counts the union.

Reads the BUILT dist, not the source: what the crawler receives is the only
thing that counts (the law R53 of the sibling project, learned the hard way
here too). Expectations are literals.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "videos"
CONTENT = ROOT / "src" / "content" / "videos"

PAGE_SIZE = 20  # must equal PAGE_SIZE in src/pages/videos/[...page].astro


def _html(rel: str) -> str:
    p = DIST / rel
    if not p.exists():
        pytest.fail(f"{p} missing — build the site first: cd seo-site && pnpm build")
    return p.read_text(encoding="utf-8")


def _card_ids(html: str) -> list[str]:
    """Video ids linked as cards, in document order, de-duplicated."""
    out: list[str] = []
    for m in re.finditer(r'href="/videos/([A-Za-z0-9_-]{11})/"', html):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def _collection_ids() -> set[str]:
    ids = set()
    for f in CONTENT.glob("*.json"):
        try:
            ids.add(json.loads(f.read_text(encoding="utf-8"))["id"])
        except Exception:  # noqa: BLE001
            pass
    if not ids:  # collection may store .md/.yaml — fall back to stems
        ids = {f.stem for f in CONTENT.iterdir() if f.is_file()}
    return ids


def _page_dirs() -> list[int]:
    return sorted(int(p.name) for p in DIST.iterdir() if p.is_dir() and p.name.isdigit())


# --- the page-size law ------------------------------------------------------

def test_first_page_holds_at_most_one_page_size():
    ids = _card_ids(_html("index.html"))
    assert 0 < len(ids) <= PAGE_SIZE, (
        f"/videos/ shows {len(ids)} cards; the whole point of this gate is that "
        f"it never again shows all of them"
    )


def test_second_page_exists_and_is_different():
    p2 = _card_ids(_html("2/index.html"))
    p1 = _card_ids(_html("index.html"))
    assert p2, "/videos/2/ has no cards"
    assert set(p1) & set(p2) == set(), (
        "pages 1 and 2 share cards — the classic paginate-that-does-not-slice bug"
    )


# --- nothing may be lost ----------------------------------------------------

def test_every_video_appears_exactly_once_across_all_pages():
    seen: list[str] = []
    seen += _card_ids(_html("index.html"))
    for n in _page_dirs():
        seen += _card_ids(_html(f"{n}/index.html"))
    assert len(seen) == len(set(seen)), "a video is listed on more than one page"
    assert set(seen) == _collection_ids(), (
        f"pagination lost or invented videos: {len(seen)} listed vs "
        f"{len(_collection_ids())} in the collection"
    )


# --- the crawl path ---------------------------------------------------------

def test_page_two_links_to_its_neighbours():
    html = _html("2/index.html")
    assert 'href="/videos/"' in html, "page 2 does not link back to page 1"
    assert 'href="/videos/3/"' in html, "page 2 does not link on to page 3"


def test_pages_are_self_canonical():
    """Canonicalising 2..N onto the first page would hide the tail from crawlers."""
    assert 'rel="canonical" href="https://defycard.com/videos/2/"' in _html("2/index.html")
    assert 'rel="canonical" href="https://defycard.com/videos/"' in _html("index.html")


def test_later_pages_are_indexable():
    """noindex on 2..N eventually severs the path to the oldest videos."""
    assert "noindex" not in _html("2/index.html").lower()


def test_later_pages_have_their_own_title():
    assert "page 2" in _html("2/index.html").lower()
