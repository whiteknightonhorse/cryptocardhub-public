"""G20 — every internal link resolves, in both serialisations.

Why: the writer prompt handed the model three example targets that did not
exist and told it Astro would drop dead links harmlessly. That was true of
`related_clusters` (RelatedLinks.astro filters them through the collection)
and false of body_markdown, which becomes a raw <a href>. 117 dead targets
and 218 dead links reached production, and crawlers walked them for weeks.

Reads the BUILT dist, because the defect lives between markdown and HTML and
neither source nor logs can see it: nginx undercounts (referrer-less crawls)
and overcounts (scanners forge Referer). Checks the md sidecars too — one page
is one law whatever the serialisation.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
REDIRECTS = ROOT / "redirects.map"

# The five paths already in crawler memory. Literals here on purpose: a gate
# that reads the law out of the file it is judging cannot fail (Y49/Y54).
EXPECTED_REDIRECTS = {
    "/compare/ether-fi-vs-crypto-com/": "/compare/ether-fi-cash-vs-crypto-com/",
    "/reviews/ether-fi-cash/": "/reviews/ether-fi-cash-review/",
    "/how-to/get-a-crypto-card/": "/how-to/how-to-get-a-crypto-card/",
    "/reviews/crypto-com-card/": "/reviews/crypto-com-card-review/",
    "/reviews/bybit-card/": "/reviews/bybit-card-review/",
}

_HREF = re.compile(r'href="(/[^"#?]*)"')
_MD_LINK = re.compile(r"\]\(<?(/[a-z0-9\-/]*/)>?\)")


def _parse_redirects() -> dict[str, str]:
    if not REDIRECTS.exists():
        pytest.fail(f"{REDIRECTS} missing — the 301s live in the repo, not only in nginx")
    out: dict[str, str] = {}
    for line in REDIRECTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) == 2:
            out[parts[0]] = parts[1]
    return out


def _served_paths() -> set[str]:
    if not DIST.is_dir():
        pytest.fail(f"{DIST} missing — build first: cd seo-site && pnpm build")
    out = set()
    for p in DIST.rglob("index.html"):
        rel = "/" + str(p.relative_to(DIST)).removesuffix("index.html")
        out.add(rel)
    for p in DIST.rglob("*.html"):
        if p.name != "index.html":
            out.add("/" + str(p.relative_to(DIST)))
    return out


def _is_asset(path: str) -> bool:
    return bool(re.search(r"\.(png|jpe?g|svg|ico|css|js|xml|txt|webmanifest|json|md|webp|avif)$", path))


# --- the law ----------------------------------------------------------------

def test_redirects_map_matches_the_expected_pairs():
    assert _parse_redirects() == EXPECTED_REDIRECTS


def test_every_redirect_target_exists():
    served = _served_paths()
    for src, dst in _parse_redirects().items():
        assert dst in served, f"{src} redirects to {dst}, which is not built — a 301 into a 404"


def test_no_html_page_links_to_a_path_that_does_not_exist():
    served, redirects = _served_paths(), _parse_redirects()
    dead: dict[str, str] = {}
    for p in DIST.rglob("*.html"):
        src = "/" + str(p.relative_to(DIST)).removesuffix("index.html")
        for m in _HREF.finditer(p.read_text(encoding="utf-8", errors="replace")):
            t = m.group(1)
            if _is_asset(t) or t in served or t in redirects:
                continue
            dead.setdefault(t, src)
    assert not dead, (
        f"{len(dead)} dead internal link target(s) in dist, e.g. "
        + ", ".join(f"{t} (from {s})" for t, s in list(dead.items())[:5])
    )


def test_no_markdown_sidecar_links_to_a_path_that_does_not_exist():
    """Same law, other serialisation — the AI-facing surface must not dead-end."""
    served, redirects = _served_paths(), _parse_redirects()
    dead: dict[str, str] = {}
    for p in DIST.rglob("index.md"):
        src = "/" + str(p.relative_to(DIST)).removesuffix("index.md")
        for m in _MD_LINK.finditer(p.read_text(encoding="utf-8", errors="replace")):
            t = m.group(1)
            if t in served or t in redirects:
                continue
            dead.setdefault(t, src)
    assert not dead, (
        f"{len(dead)} dead link target(s) in md sidecars, e.g. "
        + ", ".join(f"{t} (from {s})" for t, s in list(dead.items())[:5])
    )
