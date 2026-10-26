"""T-0142-11 — write-article's country-availability text must never carry its
own hardcoded list; it must defer to config/availability.yaml.

The regression this guards against already shipped once: write-article/SKILL.md
§1.6 copied etherfi/SKILL.md's PRE-E-06 blocklist (issuer article 262373,
"prohibited countries") and kept calling Bangladesh, Hungary and the
Philippines "prohibited" months after the issuer's real, current allow-list
(article 758780, mirrored in config/availability.yaml) added all three back.
Y49: the expected value is a literal here, not something read back out of the
skill file or the code under test — the stale list below is typed out by hand.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
SKILL = ROOT / ".claude" / "skills" / "write-article" / "SKILL.md"
AVAILABILITY = ROOT / "config" / "availability.yaml"

# The exact stale line this section used to hardcode, kept here as the literal
# mutation input. Three of these (Bangladesh, Hungary, Philippines) are on the
# issuer's real current allow-list — that overlap IS the defect.
STALE_PROHIBITED_COUNTRIES_LINE = (
    "- If the article targets a **prohibited country** (Belarus, Bangladesh, "
    "China, Cuba, Estonia, Finland, Hungary, India, Iraq, Israel, Nepal, "
    "Netherlands, North Korea, Philippines, Russia, Syria, Turkey, Ukraine, "
    "Venezuela, Vietnam) -> article must route the reader elsewhere.\n"
)


def _availability() -> dict:
    return yaml.safe_load(AVAILABILITY.read_text(encoding="utf-8"))


def _allowed_countries(avail: dict) -> set[str]:
    return set(avail["cards"]["etherfi"]["countries"])


def prohibited_countries_named_in_text(text: str) -> list[str]:
    """Extract the parenthetical list after '**prohibited country**', if any."""
    m = re.search(r"\*\*prohibited country\*\*\s*\(([^)]*)\)", text)
    if not m:
        return []
    return [c.strip() for c in m.group(1).split(",")]


def stale_overlap(names: list[str], allowed: set[str]) -> set[str]:
    """The defect: a name the text calls 'prohibited' that the issuer's own
    current allow-list actually includes."""
    return set(names) & allowed


# --- live checks ------------------------------------------------------------

def test_skill_text_names_no_country_that_is_actually_on_the_allowlist():
    text = SKILL.read_text(encoding="utf-8")
    named = prohibited_countries_named_in_text(text)
    overlap = stale_overlap(named, _allowed_countries(_availability()))
    assert not overlap, f"SKILL.md calls these 'prohibited' but the issuer allow-list includes them: {overlap}"


def test_skill_defers_to_availability_yaml_instead_of_hardcoding():
    text = SKILL.read_text(encoding="utf-8")
    assert "config/availability.yaml" in text
    assert "availability_lib.unavailable_for" in text


# --- mutation: reintroducing the historical stale list must go red ---------

def test_mutation_reintroducing_the_stale_list_is_red():
    text = SKILL.read_text(encoding="utf-8") + STALE_PROHIBITED_COUNTRIES_LINE
    named = prohibited_countries_named_in_text(text)
    overlap = stale_overlap(named, _allowed_countries(_availability()))
    assert overlap == {"Bangladesh", "Hungary", "Philippines"}, overlap
