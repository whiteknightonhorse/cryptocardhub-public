"""G41 (T-0142-05) — the ARD manifest (ai-catalog.json) is honest and reachable.

Runs against the BUILT site (seo-site/dist), not the source under public/:
Astro copies public/ verbatim, but only the dist copy proves the file survived
the build and that the URLs it advertises resolve to real files, not 404s.

THE LAW IS A LITERAL HERE (Y49): the three entries (identifier, type, url) are
typed out below, not read back from the file under test and compared to
itself — a test that derives its expectation from the same JSON it is
checking moves with every mutation of that JSON and can never go red.

The rule this gate exists to enforce (apibase ruling 0173 p.4, carried over
2026-09-24): ai-catalog.json is a surface third parties cache — it may never
carry a number ("3200 pages", "30 articles/day"). A page count or cadence
figure baked in here goes stale the moment the corpus grows and nobody
notices, because nothing re-reads a cached manifest to check it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

DIST = Path(__file__).resolve().parent.parent / "dist"
CATALOG = DIST / ".well-known" / "ai-catalog.json"

# LAW — the three entries this manifest must carry (type, dist-relative path
# derived from the url). Order not asserted; count and content are.
EXPECTED_ENTRIES = {
    "application/json": DIST / ".well-known" / "agent-skills" / "index.json",
    "text/markdown": DIST / "llms.txt",
    "application/linkset+json": DIST / ".well-known" / "api-catalog",
}

_DIGIT_RE = re.compile(r"\d")


def _load():
    if not CATALOG.exists():
        pytest.fail(
            f"{CATALOG} missing — build the site first: "
            f"cd seo-site && set -a && . ../.env && set +a && pnpm build"
        )
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def test_valid_json_with_required_shape():
    data = _load()
    assert data["specVersion"] == "1.0"
    assert data["host"]["identifier"] == "did:web:defycard.com"
    assert isinstance(data["entries"], list) and len(data["entries"]) == 3


def test_entries_match_the_three_advertised_surfaces():
    data = _load()
    types_seen = {e["type"] for e in data["entries"]}
    assert types_seen == set(EXPECTED_ENTRIES), (
        f"ai-catalog.json entries changed shape: got {sorted(types_seen)}, "
        f"expected {sorted(EXPECTED_ENTRIES)}"
    )


def test_no_digit_in_description_or_representative_queries():
    data = _load()
    for entry in data["entries"]:
        assert not _DIGIT_RE.search(entry["description"]), (
            f"{entry['identifier']}: description contains a digit — this "
            f"surface is cached by third parties and must carry no numbers"
        )
        for q in entry["representativeQueries"]:
            assert not _DIGIT_RE.search(q), (
                f"{entry['identifier']}: representativeQueries contains a digit"
            )


def test_every_advertised_url_resolves_to_a_real_file_in_dist():
    data = _load()
    for entry in data["entries"]:
        url_path = urlparse(entry["url"]).path
        dist_path = DIST / url_path.lstrip("/")
        assert dist_path == EXPECTED_ENTRIES[entry["type"]], (
            f"{entry['identifier']}: url {entry['url']!r} does not resolve "
            f"to the expected dist path for type {entry['type']}"
        )
        assert dist_path.exists(), (
            f"{dist_path} does not exist in dist/ — ai-catalog.json "
            f"advertises a URL that would 404"
        )
