"""availability_lib — the one place Python answers "can this jurisdiction get it".

WHY IT IS A LIBRARY AND NOT A FUNCTION IN EACH CALLER
Four independent copies of `slugify()` cost this project 15 days of a cron
re-writing the same page. The rule that came out of that is one implementation,
everyone imports it. This file is that rule applied to the availability
question, which now has two Python consumers (pseo-generate, the checker) and
its own gate.

THE SHAPE OF THE TRUTH CHANGED (2026-09-14)
The issuer used to publish a blocklist: "these countries cannot". It now
publishes an allow-list with the sentence "If your jurisdiction is not listed,
you will not be able to apply for a Cash card at this time." So the default
flipped from allow to DENY, and a jurisdiction nobody has an opinion about is
unavailable — not "probably fine". Reading the old blocklist against the new
page would have called every country on earth available except nineteen.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
YAML_PATH = ROOT / "config" / "availability.yaml"

CARD = "etherfi"


def load(path: Path | None = None) -> dict[str, Any]:
    import yaml
    return yaml.safe_load((path or YAML_PATH).read_text(encoding="utf-8")) or {}


def card(cfg: dict[str, Any] | None = None, key: str = CARD) -> dict[str, Any]:
    return ((cfg or load()).get("cards") or {}).get(key) or {}


def available_jurisdictions(cfg: dict[str, Any] | None = None,
                            key: str = CARD) -> set[str]:
    """Everything the issuer's allow-list names, countries and US states in one
    set. They are one namespace for this question: a reader asks "can I get it
    where I live", not "is my jurisdiction a country"."""
    c = card(cfg, key)
    return set(c.get("countries") or []) | set(c.get("us_states") or [])


def unavailable_for(jurisdiction: str, cfg: dict[str, Any] | None = None,
                    key: str = CARD) -> bool:
    """DEFAULT-DENY. Not on the allow-list means cannot apply.

    Belgium and Ireland are on the issuer's page with an explicit ❌; Vietnam
    and Mississippi are simply absent. Both answers are True here and that is
    the point — the explicit list is editorially useful, never load-bearing.
    A blocklist card keeps its old meaning so a second issuer can be added
    without rewriting this.
    """
    c = card(cfg, key)
    if c.get("mode") == "blocklist":
        return jurisdiction in set(c.get("countries") or []) | set(
            c.get("us_states_blocked") or [])
    return jurisdiction not in available_jurisdictions(cfg, key)


def slug_jurisdictions(cfg: dict[str, Any] | None = None) -> dict[str, dict[str, str]]:
    """slug -> {name, kind}. The join table between our URLs and the issuer's
    list, written out because it is not derivable: `best-crypto-card-georgia`
    is the US state and `best-crypto-card-uk` is "United Kingdom"."""
    return (cfg or load()).get("slug_jurisdictions") or {}


def unavailable_slugs(cfg: dict[str, Any] | None = None) -> list[str]:
    """The slugs the site declares as routing away from ether.fi."""
    return list((cfg or load()).get("unavailable_slugs") or [])


def jurisdiction_of(slug: str, cfg: dict[str, Any] | None = None) -> str | None:
    entry = slug_jurisdictions(cfg).get(slug)
    return entry.get("name") if entry else None
