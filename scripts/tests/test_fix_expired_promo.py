"""G31 — the money-copy editor is only allowed to be sure or silent.

This script rewrites financial claims on published pages. The failure it must
never have is the confident half-edit: a sentence that looks reviewed and says
something wrong. So every law below is about REFUSING, and the tests are
written from the side of leaving text alone.

The content directory is gitignored — there is no `git checkout` to undo a bad
run, only a tar. That is why structural safety is asserted rather than assumed.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fx = _load("fix_expired_promo", "scripts/fix-expired-promo.py")


def _stats():
    return {"found": 0, "dropped": 0, "manual": [], "unparsed": [], "rules": {},
            "date_bumped": 0, "no_date_field": [], "changed": 0}


# --- structural safety: arrays are data, never text ------------------------

def test_a_frontmatter_array_survives_as_valid_json():
    """The accident this prevents: a regex over the whole file ate the opening
    bracket of an array like this one and broke five pages."""
    line = ('snapshot_bullets: ["**Cashback:** Up to 3% standard, up to 15% on '
            'dining/groceries", "**FX fee:** 0% USD/EUR"]')
    out = fx.fix_frontmatter_line(line, _stats())
    key, _, raw = out.partition(": ")
    assert key == "snapshot_bullets"
    value = json.loads(raw)                     # would raise if broken
    assert isinstance(value, list) and len(value) == 2
    assert "15%" not in raw and "15 %" not in raw


def test_the_json_ld_block_is_parsed_not_regexed():
    """schema_faq is a JSON OBJECT. The first version of this script matched
    only `[...]` and fell through to raw text editing on it."""
    inner = {"@type": "Question", "name": "q",
             "acceptedAnswer": {"@type": "Answer",
                                "text": "Standard is up to 3 % and up to 15 % on food."}}
    line = "schema_faq: " + json.dumps({"@context": "https://schema.org",
                                        "mainEntity": [inner]})
    out = fx.fix_frontmatter_line(line, _stats())
    value = json.loads(out.partition(": ")[2])
    assert value["@context"] == "https://schema.org"
    assert "15 %" not in value["mainEntity"][0]["acceptedAnswer"]["text"]


def test_an_unparseable_value_is_left_exactly_alone():
    line = 'snapshot_bullets: ["broken", up to 15 % on food]'
    st = _stats()
    assert fx.fix_frontmatter_line(line, st) == line
    assert "snapshot_bullets" in st["unparsed"]


def test_a_key_not_on_the_list_is_never_touched():
    line = 'some_other_field: ["up to 15 % on dining"]'
    assert fx.fix_frontmatter_line(line, _stats()) == line


# --- refusing beats guessing -----------------------------------------------

def test_an_edit_that_leaves_debris_is_reverted_whole():
    """'3 % (standard) + 15 % (food)' minus the promo leaves a dangling '+'.
    A half-edited money sentence is worse than an untouched one: it looks
    reviewed."""
    text = "Cashback: up to 3 % (standard) + up to 15 % (food)."
    st = _stats()
    out = fx.fix_text(text, st)
    assert out == text                       # unchanged
    assert st["scarred"] == 1
    assert any("scar" in m for m in st["manual"])


def test_an_unrecognised_occurrence_is_reported_not_dropped():
    text = "Rates: Up to 3% standard, 15% promos."
    st = _stats()
    out = fx.fix_text(text, st)
    assert "15% promos" in out               # left as it was
    assert len(st["manual"]) == 1


def test_text_without_the_claim_is_returned_byte_identical():
    for text in ("Cashback is up to 3 % on all spend.",
                 "15 % of users abandon KYC.",          # 15 %, but not about food
                 "Physical card ships in 15+ business days."):
        assert fx.fix_text(text, _stats()) == text


# --- the correction must not create a new lie ------------------------------

def test_a_labelled_cashback_bullet_is_corrected_never_deleted():
    """Dropping the whole bullet left a page listing no cashback at all, which
    reads as 'this card pays none'."""
    line = 'snapshot_bullets: ["**Cashback:** Up to 15 % on dining and groceries", "**FX:** 1 %"]'
    out = fx.fix_frontmatter_line(line, _stats())
    value = json.loads(out.partition(": ")[2])
    assert len(value) == 2
    assert value[0].startswith("**Cashback:**")
    # spelling-agnostic on the space before %, because the replacement carries
    # no markdown and no padded percent sign: only the FACTS are the law here.
    assert re.search(r"\b3\s?%", value[0]) and re.search(r"0\.5\s?%", value[0])
    assert "15" not in value[0]
    assert "**" not in value[0][len("**Cashback:**"):], \
        "only the leading label renders; emphasis later reaches the reader as asterisks"


def test_the_replacement_states_the_step_down_not_just_the_headline():
    """'up to 3 %' alone is the overstatement this whole wave exists to end."""
    assert "0.5" in fx.CANONICAL_CASHBACK
    assert re.search(r"\b3\s?%", fx.CANONICAL_CASHBACK)
    assert "**" not in fx.CANONICAL_CASHBACK   # seen live as literal asterisks


# --- the date means a check happened ---------------------------------------

def test_the_verified_date_is_todays_and_only_where_something_changed():
    text = 'bullets: ["**Last verified:** 2026-05-19"]'
    out, bumped = fx.bump_verified(text)
    assert bumped and fx.TODAY in out and "2026-05-19" not in out
    # a page with no date field is reported, never given a fabricated one
    out2, bumped2 = fx.bump_verified("no date here")
    assert not bumped2 and out2 == "no date here"


def test_the_review_page_keeps_the_promo_history():
    """One dated sentence on the review page answers a query people still make;
    the same sentence on 306 others is dilution."""
    assert fx.HISTORY_PAGE == "ether-fi-cash-review"
