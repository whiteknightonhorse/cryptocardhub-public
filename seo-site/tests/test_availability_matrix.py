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
    """The template states no facts of its own. Andorra is on the issuer's
    allow-list and has a row; Freedonia is in no list of ours and must not
    appear, however confidently a model could have written it in."""
    assert _row(html, "Andorra") is not None
    assert _row(html, "Freedonia") is None
    assert "Freedonia" not in html


# --- 1b. the allow-list, E-06 ----------------------------------------------
# LAWS ARE LITERALS (Y49). These four jurisdictions are the acceptance line of
# E-06 and are typed out rather than read back from the YAML the page renders.

def test_hungary_reads_as_available(html):
    """Hungary sat on the old blocklist and is on the issuer's current
    allow-list. 259 pages still call it unavailable (E-08 cleans those); the
    matrix, which is the machine-readable one, must not."""
    row = _row(html, "Hungary")
    assert row is not None, "no Hungary row — the allow-list is not rendered"
    assert "Available" in row and "Not available" not in row, row


def test_belgium_reads_as_not_available_by_the_issuers_own_cross(html):
    row = _row(html, "Belgium")
    assert row is not None, "Belgium carries an explicit ❌ and must be a row"
    assert NOT_AVAILABLE in row, row


def test_nevada_is_marked_not_available_for_etherfi(html):
    """The US-state twin of the India law, and the one with a page behind it.

    The pre-E-06 state branch hard-coded "states are always a blocklist", so
    every state absent from the issuer's list of 31 rendered as Available —
    including the four we publish an honest "you cannot get this here" page
    about. Asserting only that ABSENT states have no row does not catch that:
    Nevada has a row, because we route it, and the row is where the lie shows.
    """
    row = _row(html, "Nevada")
    assert row is not None, "no Nevada row — the state we route away has vanished"
    assert NOT_AVAILABLE in row, row


def test_a_us_state_the_issuer_does_not_list_is_never_shown_as_available(html):
    """Default-deny for a state with no page of its own. Mississippi is either
    an explicit Not-available row or no row at all; what it may never be is
    Available."""
    row = _row(html, "Mississippi")
    assert row is None or NOT_AVAILABLE in row, row


def test_a_country_the_issuer_does_not_list_is_never_shown_as_available(html):
    row = _row(html, "Vietnam")
    assert row is None or NOT_AVAILABLE in row, row


# --- 1c. Plasma One, "Not stated" vs "Not available" (T-0142-08) -----------
# LAWS ARE LITERALS (Y49). Plasma publishes NO global country allow-list
# (`countries: null` in the YAML) — a country we have no opinion on must read
# "Not stated", never the confident-wrong "Not available" a default-deny
# scan would produce if `null` were treated the same as an empty `[]` list.
# India is the control in the other direction: the issuer's own "countries
# that are blocked but may still be eligible" section names it explicitly, so
# it must read "Not available" despite the general list being unpublished.

def _cell(html_text, jurisdiction, card_key):
    row = _row(html_text, jurisdiction)
    assert row is not None, f"no {jurisdiction} row in the matrix"
    cell = re.search(rf'<td[^>]*data-card="{card_key}"[^>]*>(.*?)</td>', row, re.S)
    assert cell, f"no {card_key} cell in the {jurisdiction} row"
    return cell.group(1)


def test_plasma_column_is_published(html):
    assert 'data-card="plasma"' in html


def test_texas_is_available_for_plasma(html):
    cell = _cell(html, "Texas", "plasma")
    assert "Available" in cell and NOT_AVAILABLE not in cell, cell


def test_arizona_is_not_available_for_plasma_absent_from_its_31_states(html):
    cell = _cell(html, "Arizona", "plasma")
    assert NOT_AVAILABLE in cell, cell


def test_india_is_not_available_for_plasma_by_documented_block(html):
    """India is on the issuer's own 'blocked but may still be eligible'
    section — a stronger fact than the unpublished general allow-list, and it
    must win over the null-countries default of 'Not stated'."""
    cell = _cell(html, "India", "plasma")
    assert NOT_AVAILABLE in cell, cell


def test_netherlands_is_not_stated_for_plasma_not_a_cross(html):
    """Netherlands is on nobody's list — not the (absent) allow-list, not the
    document-blocked list. `countries: null` must render this as 'Not
    stated', not the false ❌ a default-deny scan over an empty list would
    produce."""
    cell = _cell(html, "Netherlands", "plasma")
    assert "Not stated" in cell, cell
    assert NOT_AVAILABLE not in cell, cell


def test_spain_is_not_stated_for_plasma(html):
    cell = _cell(html, "Spain", "plasma")
    assert "Not stated" in cell, cell


def test_countries_null_vs_empty_list_are_different_facts_for_cellFor():
    """The mutation: replacing `countries: null` with `countries: []` must
    turn an unlisted country's verdict from 'Not stated' (null) to 'Not
    available' (false) — the exact distinction T-0142-08 exists to draw."""
    def cell_for(card_json, name):
        src = (
            f"import {{ cellFor }} from '{SEO}/src/lib/availability.mjs';"
            f"process.stdout.write(JSON.stringify(cellFor({card_json}, '{name}', 'country')));"
        )
        p = subprocess.run(["node", "--input-type=module", "-e", src],
                           capture_output=True, text=True, cwd=SEO)
        assert p.returncode == 0, p.stderr
        return json.loads(p.stdout)

    not_stated_card = json.dumps({"mode": "allowlist", "countries": None,
                                   "countries_document_blocked": ["India"]})
    empty_card = json.dumps({"mode": "allowlist", "countries": [],
                              "countries_document_blocked": ["India"]})
    assert cell_for(not_stated_card, "Netherlands") is None
    assert cell_for(empty_card, "Netherlands") is False
    # the documented block wins over the unpublished/empty list either way
    assert cell_for(not_stated_card, "India") is False
    assert cell_for(empty_card, "India") is False


def test_the_page_says_what_an_absent_jurisdiction_means(html):
    """The format of the issuer's list is itself a fact, and it inverted. A
    page that kept saying 'an absent state means not blocked' would be
    confidently wrong about every jurisdiction it does not list."""
    assert "not listed cannot apply" in html, \
        "the page still describes the issuer's list as a blocklist"


def test_the_rows_are_exactly_what_the_yaml_names(html, cards):
    """The row universe is three YAML-sourced sets and nothing else: the
    allow-list, the issuer's own ❌ rows, and the jurisdictions we publish an
    honest 'not available here' page about. The third is load-bearing — India
    is absent from the issuer's page, and dropping it would delete the link to
    the highest-converting object on this site (the Y50 money bug, re-opened
    through a shape change)."""
    import yaml
    cfg = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8")) or {}
    allowed = set()
    for c in cards.values():
        for key in ("countries", "countries_explicit_no", "countries_document_blocked",
                    "us_states", "us_states_explicit_no", "us_states_blocked"):
            allowed.update(c.get(key) or [])
    allowed.update(j["name"] for j in (cfg.get("slug_jurisdictions") or {}).values())
    import html as _html
    rendered = {_html.unescape(x)
                for x in re.findall(r'<th scope="row">([^<]+)</th>', html)}
    assert rendered, "no rows at all"
    assert rendered <= allowed, sorted(rendered - allowed)
    assert {"Hungary", "Belgium", FORBIDDEN_JURISDICTION} <= rendered


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


def _md_cell(md_text, jurisdiction, card_name):
    """Value of one (jurisdiction, card) cell in whichever `| Country | ... |`
    table contains that jurisdiction's row — the same fact the HTML asserts,
    read from the plain-text surface an assistant actually fetches."""
    header = next(l for l in md_text.splitlines() if l.startswith("| Country |")
                  or l.startswith("| US state |"))
    cols = [c.strip() for c in header.strip("|").split("|")]
    idx = cols.index(card_name)
    row = next(l for l in md_text.splitlines() if l.startswith(f"| {jurisdiction} |"))
    return [c.strip() for c in row.strip("|").split("|")][idx]


def test_the_sidecar_agrees_with_the_html_on_plasma_not_stated_vs_not_available():
    """G42: the md sidecar is built from the same `cellFor()` as the HTML
    (build-markdown.js) — this pins that it actually says the same thing,
    not just that it could."""
    md = MATRIX_MD.read_text(encoding="utf-8")
    assert "Plasma One" in md
    assert _md_cell(md, "Texas", "Plasma One") == "Available"
    assert _md_cell(md, "Arizona", "Plasma One") == "Not available"
    assert _md_cell(md, "India", "Plasma One") == "Not available"
    assert _md_cell(md, "Netherlands", "Plasma One") == "Not stated"
    assert _md_cell(md, "Spain", "Plasma One") == "Not stated"


# --- geo pages point at it --------------------------------------------------

def test_geo_pages_link_to_the_matrix():
    """Geo is where assistants land, with 'can I even get this here' as the
    question. Every geo page carries the pointer."""
    page = DIST / "geo" / "best-crypto-card-india" / "index.html"
    assert page.exists()
    assert "/geo/availability-matrix/" in page.read_text(encoding="utf-8")
