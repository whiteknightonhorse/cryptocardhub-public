"""G37 — a URL the LIVE sitemap names must not become a bare 404.

LIVE_ROOT (the previous copy of the site) is the ledger: every <loc> path in its
sitemap-0.xml and sitemap-videos.xml must be served by the new dist OR be a
redirects.map source (301 or 410). No readable previous sitemap = a blind gate,
which fails; the only exception is URL_RETIREMENT_FIRST_DEPLOY=1, set by hand.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
REDIRECTS = ROOT / "redirects.map"
SITEMAPS = ("sitemap-0.xml", "sitemap-videos.xml")
# /articles/N/ is a derived listing page: consolidating 5 articles shrinks the last page
# away (CCH-SEO-09: /articles/30/). A redirect row there would shadow it when the corpus grows back.
_PAGINATION = re.compile(r"^/articles/\d+/$")


def _locs(xml: Path) -> set[str]:
    if not xml.is_file():
        return set()
    return {urlparse(u.strip()).path
            for u in re.findall(r"<loc>([^<]+)</loc>", xml.read_text(encoding="utf-8"))}


def _served(dist: Path) -> set[str]:
    out = set()
    for p in dist.rglob("index.html"):
        out.add("/" + str(p.relative_to(dist)).removesuffix("index.html"))
    for p in dist.rglob("*.html"):
        if p.name != "index.html":
            out.add("/" + str(p.relative_to(dist)))
    return out


def _sources(redirects: Path) -> set[str]:
    out = set()
    for line in redirects.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and not line.lstrip().startswith("#"):
            out.add(parts[0])
    return out


def retired_without_answer(live_root: Path, dist: Path, redirects: Path,
                           first_deploy: bool = False) -> set[str]:
    if not (live_root / "sitemap-0.xml").is_file():
        if first_deploy:
            return set()
        pytest.fail("no previous sitemap readable — a blind gate is not a gate")
    remembered: set[str] = set()
    for name in SITEMAPS:
        remembered |= _locs(live_root / name)
    return {p for p in remembered - _served(dist) - _sources(redirects) if not _PAGINATION.match(p)}


def test_no_remembered_url_becomes_a_404():
    live = Path(os.environ.get("LIVE_ROOT", "/var/www/defycard.com/html"))
    if not DIST.is_dir():
        pytest.fail(f"{DIST} missing — build first: cd seo-site && pnpm build")
    missing = retired_without_answer(
        live, DIST, REDIRECTS, os.environ.get("URL_RETIREMENT_FIRST_DEPLOY") == "1")
    assert not missing, (
        f"{len(missing)} URL(s) in the live sitemap would 404 — add a 301/410 row: "
        f"{sorted(missing)[:20]}")


# --- fixtures: the gate itself, on a tmp tree --------------------------------

def _tree(tmp_path: Path, with_sitemap: bool = True):
    live, dist = tmp_path / "live", tmp_path / "dist"
    live.mkdir()
    (dist / "a" / "b").mkdir(parents=True)
    (dist / "a" / "b" / "index.html").write_text("x")
    if with_sitemap:
        locs = "".join(f"<url><loc>https://defycard.com{p}</loc></url>"
                       for p in ("/a/b/", "/c/d/", "/e/f/"))
        (live / "sitemap-0.xml").write_text(f"<urlset>{locs}</urlset>")
    rm = tmp_path / "redirects.map"
    rm.write_text("/c/d/\t/a/b/\n")
    return live, dist, rm


def test_fixture_names_the_unanswered_path(tmp_path):
    live, dist, rm = _tree(tmp_path)
    assert retired_without_answer(live, dist, rm) == {"/e/f/"}


def test_blind_gate_fails(tmp_path):
    """Mutation: treat a missing sitemap as pass -> this goes red."""
    live, dist, rm = _tree(tmp_path, with_sitemap=False)
    with pytest.raises(pytest.fail.Exception, match="no previous sitemap readable"):
        retired_without_answer(live, dist, rm)


def test_first_deploy_flag_is_the_only_exception(tmp_path):
    live, dist, rm = _tree(tmp_path, with_sitemap=False)
    assert retired_without_answer(live, dist, rm, first_deploy=True) == set()


def test_vanished_pagination_page_is_not_a_retired_url(tmp_path):
    """Mutation: drop the _PAGINATION filter -> red. Content paths still count."""
    live, dist, rm = _tree(tmp_path)
    (live / "sitemap-0.xml").write_text(
        "<urlset><url><loc>https://defycard.com/articles/30/</loc></url>"
        "<url><loc>https://defycard.com/e/f/</loc></url></urlset>")
    assert retired_without_answer(live, dist, rm) == {"/e/f/"}
