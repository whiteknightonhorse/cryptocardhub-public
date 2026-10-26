"""G41 (T-0142-07) — the `plasma` affiliate entry and its facts.

Every expected value below is a LITERAL (Y49: a gate that reads its
expectation from the code/config under test moves with the mutation and
stays green). Each check is a small pure function so the SAME function is
exercised once against the real repo data (must be green) and once against a
hand-built bad value (must be red) — the mutation proof lives in this file,
not in a copy of production logic.
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import facts_lib  # noqa: E402

AFFILIATES = ROOT / "config" / "affiliates.yaml"
AVAILABILITY = ROOT / "config" / "availability.yaml"
PLASMA_FACTS = ROOT / "config" / "facts" / "plasma.yaml"

BANNED_CTA_WORDS = ("free", "4%", "yield", "180")


def _affiliates() -> dict:
    return yaml.safe_load(AFFILIATES.read_text(encoding="utf-8"))["affiliates"]


def _availability() -> dict:
    return yaml.safe_load(AVAILABILITY.read_text(encoding="utf-8"))


def _plasma_facts() -> list[dict]:
    return facts_lib.load(PLASMA_FACTS)


# --- the checks themselves (each used live below AND mutated) ---------------

def fallback_is_cryptocom(avail: dict) -> bool:
    return avail["etherfi"]["fallback_partner"] == "cryptocom"


def cta_has_no_banned_words(cta: str) -> bool:
    low = cta.lower()
    return not any(word in low for word in BANNED_CTA_WORDS)


def commission_starts_with(commission: str, prefix: str) -> bool:
    return commission.startswith(prefix)


def no_current_fact_has_null_expires(facts: list[dict]) -> bool:
    return not any(f["status"] == "current" and f.get("expires") is None for f in facts)


# --- live checks --------------------------------------------------------

def test_fallback_partner_untouched():
    assert fallback_is_cryptocom(_availability())


def test_plasma_url_is_templated_not_hardcoded():
    assert _affiliates()["plasma"]["url"] == "${PLASMA_REF_URL}"


def test_plasma_url_fallback_host():
    url_fallback = _affiliates()["plasma"]["url_fallback"]
    assert urlparse(url_fallback).hostname == "www.plasma.org"


def test_plasma_cta_has_no_banned_words():
    assert cta_has_no_banned_words(_affiliates()["plasma"]["cta"])


def test_plasma_commission_is_the_lite_figure():
    assert commission_starts_with(_affiliates()["plasma"]["commission"], "0.25%")


def test_etherfi_commission_is_the_measured_figure():
    assert commission_starts_with(_affiliates()["etherfi"]["commission"], "0.1%")


def test_plasma_facts_load_and_validate():
    facts = _plasma_facts()
    assert facts, "config/facts/plasma.yaml has no facts"


def test_us_states_fact_is_31_texas_not_arizona():
    facts = _plasma_facts()
    by_id = {f["id"]: f for f in facts}
    us_states = by_id["plasma.availability.us_states"]["value"]
    assert len(us_states) == 31
    assert "Texas" in us_states
    assert "Arizona" not in us_states


def test_no_current_plasma_fact_has_null_expires():
    assert no_current_fact_has_null_expires(_plasma_facts())


# --- mutations: same check function, deliberately wrong input, must be red --

def test_mutation_fallback_partner_plasma_is_red():
    mutated = {"etherfi": {"fallback_partner": "plasma"}}
    assert not fallback_is_cryptocom(mutated)


def test_mutation_cta_free_visa_card_is_red():
    assert not cta_has_no_banned_words("Free Visa card")


def test_mutation_commission_reverted_to_one_percent_is_red():
    assert not commission_starts_with("1% of referral spending / year", "0.1%")
    assert not commission_starts_with("1% of referred card spend / year", "0.25%")


def test_mutation_expires_stripped_from_one_record_is_red():
    facts = [dict(f) for f in _plasma_facts()]
    facts[0]["expires"] = None  # first record is `current` (plasma.card.network)
    assert facts[0]["status"] == "current"
    assert not no_current_fact_has_null_expires(facts)
