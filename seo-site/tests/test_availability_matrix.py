"""G24 (Y51 AEO-1) — the availability matrix states only what the YAML says,
and never hides how old that is.

Runs against the BUILT site. The failure mode this guards is a fact drifting
into the template: a country hard-coded in availability-matrix.astro cannot be
re-verified, cannot be dated, and cannot be corrected by whoever owns the data.
Deleting a country from config/availability.yaml must remove it from the page.

Laws are literals here (lesson Y49). The India row in particular is typed out:
`geo/best-crypto-card-india` is where 3 of the 10 clicks assistants have ever
sent this site landed, on a page that said "not available" while linking the
unavailable product.
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import date
from pathlib import Path

import pytest

SEO = Path(__file__).resolve().parent.parent
DIST = SEO / "dist"
MATRIX = DIST / "geo" / "availability-matrix" / "index.html"
MATRIX_MD = DIST / "geo" / "availability-matrix" / "index.md"
YAML_PATH = SEO.parent / "config" / "availability.yaml"

# LAW: this exact cell must exist. ether.fi Cash is not issuable in India.
FORBIDDEN_JURISDICTION = "India"
CARD_NAME = "ether.fi Cash"
NOT_AVAILABLE = "Not available"

# LAW: the age threshold, typed out. Mirrored in src/lib/availability.mjs and
# scripts/weekly-report.py.
STALE_AFTER_DAYS = 60
STALE_PHRASES = ("data from", "check overdue")

pytestmark = pytest.mark.skipif(
    not MATRIX.exists(), reason="dist/ not built — run pnpm build && node scripts/build-markdown.js")


@pytest.fixture(scope="module")
def html():
    return MATRIX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cards():
    import yaml
    return (yaml.safe_load(YAML_PATH.read_text(encoding="utf-8")) or {}).get("cards") or {}


def _row(html_text, jurisdiction):
    """The <tr> whose row header is this jurisdiction."""
    m = re.search(rf'<tr>\s*<th scope="row">{re.escape(jurisdiction)}</th>(.*?)</tr>',
                  html_text, re.S)
    return m.group(1) if m else None


# --- 1. the India cell ------------------------------------------------------

def test_india_is_marked_not_available_for_etherfi(html):
    assert CARD_NAME in html, "the ether.fi column is missing entirely"
    row = _row(html, FORBIDDEN_JURISDICTION)
    assert row is not None, f"no {FORBIDDEN_JURISDICTION} row in the matrix"
    cell = re.search(r'<td[^>]*data-card="etherfi"[^>]*>(.*?)</td>', row, re.S)
    assert cell, f"no etherfi cell in the {FORBIDDEN_JURISDICTION} row"
    assert NOT_AVAILABLE in cell.group(1), cell.group(1)


def test_a_blocked_country_links_to_its_alternatives_page(html):
    """A red cell that dead-ends the reader is worth nothing. India has an
    honest geo page naming cards that ARE issuable there."""
    row = _row(html, FORBIDDEN_JURISDICTION)
    assert "/geo/best-crypto-card-india/" in row, row


def test_a_country_absent_from_the_yaml_is_absent_from_the_page(html, cards):
    """The template states no facts of its own. Belgium is not on anyone's
    blocklist, so it must not appear as a row."""
    assert _row(html, "Belgium") is None
    assert "Belgium" not in html


# --- 2. every published card shows the date its list was checked ------------

def test_every_published_card_shows_its_verified_date_from_the_yaml(html, cards):
    published = {k: c for k, c in cards.items() if c.get("verified")}
    assert published, "no card has a verified date — the matrix would be empty"
    for key, card in published.items():
        assert f'data-verified="{card["verified"]}"' in html, \
            f"{key}: page does not carry the YAML verified date {card['verified']}"
        assert str(card["verified"]) in html


def test_the_page_states_the_age_of_every_card_in_words(html, cards):
    """Not just a machine attribute — a human reading the page is told."""
    for key, card in cards.items():
        if not card.get("verified"):
            continue
        age = (date.today() - date.fromisoformat(str(card["verified"]))).days
        if age <= STALE_AFTER_DAYS:
            assert f"Last verified: {card['verified']} ({age} days ago)" in html
        else:
            assert all(p in html for p in STALE_PHRASES)


# --- 3. the overdue rule, exercised without waiting 61 days -----------------

def _note(verified, today):
    """Call the real library function through node — the rule must be tested
    where it lives, not re-implemented in the test."""
    src = (
        f"import {{ availabilityNote }} from '{SEO}/src/lib/availability.mjs';"
        f"process.stdout.write(availabilityNote("
        f"{{verified:'{verified}'}}, new Date('{today}T12:00:00Z')));"
    )
    p = subprocess.run(["node", "--input-type=module", "-e", src],
                       capture_output=True, text=True, cwd=SEO)
    assert p.returncode == 0, p.stderr
    return p.stdout


def test_a_list_checked_61_days_ago_is_shown_as_overdue():
    note = _note("2026-06-11", "2026-08-11")     # exactly 61 days
    assert all(p in note for p in STALE_PHRASES), note
    assert "2026-06-11" in note


def test_a_list_checked_60_days_ago_is_not_yet_overdue():
    note = _note("2026-06-12", "2026-08-11")     # exactly 60 days
    assert "check overdue" not in note, note
    assert "Last verified" in note


def test_the_page_never_shows_a_date_without_its_age():
    """Silence about age is the failure. Both arms of the rule say how old."""
    fresh = _note("2026-08-01", "2026-08-11")
    stale = _note("2026-01-01", "2026-08-11")
    assert "10 days ago" in fresh, fresh
    assert "days old" in stale, stale


# --- 4. a card with no verified date is not published at all ----------------

def test_an_unverified_card_is_absent_from_the_table(html, cards):
    """A narrow honest table beats a wide imaginary one. cryptocom is staged
    in the YAML with no `verified` and therefore no column."""
    unverified = [k for k, c in cards.items() if not c.get("verified")]
    assert unverified, "fixture assumption: at least one card is staged unverified"
    for key in unverified:
        assert f'data-card="{key}"' not in html, \
            f"{key} has never been verified but is rendered as fact"


# --- 5. the markdown sidecar says the same thing ---------------------------

def test_the_markdown_sidecar_carries_the_same_india_verdict():
    """Assistants request Accept: text/markdown. If the sidecar and the HTML
    can disagree, we publish two different sets of facts."""
    assert MATRIX_MD.exists(), "no markdown sidecar for the matrix"
    md = MATRIX_MD.read_text(encoding="utf-8")
    row = [l for l in md.splitlines() if l.startswith(f"| {FORBIDDEN_JURISDICTION} |")]
    assert row, "no India row in the markdown matrix"
    assert NOT_AVAILABLE in row[0], row[0]
    assert CARD_NAME in md


def test_the_sidecar_also_states_the_source_and_the_check_date(cards):
    md = MATRIX_MD.read_text(encoding="utf-8")
    for key, card in cards.items():
        if card.get("verified"):
            assert str(card["verified"]) in md
            assert card["source"]["url"] in md


# --- geo pages point at it --------------------------------------------------

def test_geo_pages_link_to_the_matrix():
    """Geo is where assistants land, with 'can I even get this here' as the
    question. Every geo page carries the pointer."""
    page = DIST / "geo" / "best-crypto-card-india" / "index.html"
    assert page.exists()
    assert "/geo/availability-matrix/" in page.read_text(encoding="utf-8")
