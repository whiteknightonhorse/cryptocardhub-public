"""G24 (Y51 AEO-1, machine half) — the reconciler finds disagreements and
changes nothing.

`verified` is a load-bearing date: the page shows it, and past 60 days it turns
into a visible overdue badge. So the only thing that may move it forward is a
check that actually passed. Three cases, and two of them must NOT move it:

    lists match        -> bump
    lists differ       -> alert, no bump      (a human decides the new fact)
    page unreadable    -> alert, no bump      (silence is not agreement)

The third is the one that rots quietly. A checker that treats "I could not
read the issuer's page" as "nothing changed" manufactures freshness, which is
exactly what the dated badge exists to prevent.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "availability_check", ROOT / "scripts" / "availability-check.py")
ac = importlib.util.module_from_spec(_SPEC)
sys.modules["availability_check"] = ac
_SPEC.loader.exec_module(ac)


def _page(countries, states=("Arizona",)):
    """Minimal shape of an Intercom help article: a heading, then table cells."""
    def cells(names):
        return "".join(f'{{"type":"paragraph","text":"{n}","class":"no-margin"}}'
                       for n in names)
    return (
        '{"type":"subheading","text":"Country"}' + cells(countries)
        + '{"type":"subheading","text":"Unsupported U.S. states"}' + cells(states)
    )


CARD = {
    "countries": ["India", "Turkey"],
    "us_states_blocked": ["Arizona"],
    "source": {"parser": "intercom_tables",
               "sections": {"countries": "country",
                            "us_states_blocked": "unsupported u.s. states"}},
    "verified": "2026-08-11",
}


def test_identical_lists_are_a_match():
    assert ac.compare(CARD, _page(["India", "Turkey"]))["status"] == "match"


def test_order_does_not_matter():
    assert ac.compare(CARD, _page(["Turkey", "India"]))["status"] == "match"


def test_an_added_country_is_a_difference_not_a_match():
    res = ac.compare(CARD, _page(["India", "Turkey", "Brazil"]))
    assert res["status"] == "differs"
    assert "Brazil" in res["summary"]


def test_a_removed_country_is_a_difference_not_a_match():
    res = ac.compare(CARD, _page(["India"]))
    assert res["status"] == "differs"
    assert "Turkey" in res["summary"]


def test_a_renamed_heading_reads_as_unparseable_not_as_an_empty_list():
    """If the issuer re-words a heading, 'section not found' is the truth.
    An empty list would read as 'every restriction was lifted'."""
    page = _page(["India", "Turkey"]).replace('"Country"', '"Restricted countries"')
    assert ac.compare(CARD, page)["status"] == "unparseable"


def test_an_unknown_parser_is_unparseable():
    card = {**CARD, "source": {**CARD["source"], "parser": "wordpress"}}
    assert ac.compare(card, _page(["India", "Turkey"]))["status"] == "unparseable"


def test_the_us_state_list_is_compared_too():
    res = ac.compare(CARD, _page(["India", "Turkey"], states=("Arizona", "Ohio")))
    assert res["status"] == "differs"
    assert "Ohio" in res["summary"]


def test_a_loose_heading_match_would_grab_the_wrong_table():
    """The article also carries 'Countries where card spending is unsupported',
    a different list. Section lookup is exact so the two cannot be confused."""
    page = (_page(["India", "Turkey"])
            + '{"type":"subheading","text":"Countries where card spending is unsupported"}'
            + '{"type":"paragraph","text":"Iran","class":"no-margin"}')
    res = ac.compare(CARD, page)
    assert res["status"] == "match", res
    assert "Iran" not in res["summary"]


# --- the bump is line surgery on exactly one card --------------------------

YAML_FIXTURE = """\
source:
  verified: "2020-01-01"
cards:
  etherfi:
    name: "ether.fi Cash"
    verified: "2026-06-01"
  cryptocom:
    name: "Crypto.com Visa"
    verified: "2026-06-02"
unavailable_slugs:
  - best-crypto-card-india
"""


def test_bump_touches_only_the_named_card():
    out = ac.bump_verified(YAML_FIXTURE, "etherfi", "2026-08-11")
    assert '    verified: "2026-08-11"' in out
    assert '    verified: "2026-06-02"' in out      # cryptocom untouched
    assert 'source:\n  verified: "2020-01-01"' in out  # the top-level one untouched


def test_bump_preserves_the_comments_that_explain_the_file():
    text = "# why this file exists\n" + YAML_FIXTURE
    out = ac.bump_verified(text, "etherfi", "2026-08-11")
    assert out.startswith("# why this file exists\n")


def test_bumping_an_unknown_card_raises_rather_than_writing_something():
    with pytest.raises(KeyError):
        ac.bump_verified(YAML_FIXTURE, "gnosispay", "2026-08-11")
