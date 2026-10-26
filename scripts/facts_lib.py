#!/usr/bin/env python3
"""facts_lib.py — load / validate / render `config/facts/etherfi.yaml`.

Schema: FABLE_DEFYCARD_PART2.md section D2. One fact = one record. The writer
(pseo-generate.py) and any rendered skill text may only ever see `current` and
`beta` facts — `disputed` and `expired` exist in the file to be tracked, never
to be published (this is the same rule Y50/Y56 paid for: a fact's status is a
property of the fact, and the only place that gets to override it is the
record itself, not whoever reads it).

CLI: scripts/facts-render.py.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FACTS_PATH = ROOT / "config" / "facts" / "etherfi.yaml"

VALID_STATUS = {"current", "expired", "disputed", "beta"}
VALID_CLASS = {
    "availability", "rate", "fee", "limit", "affiliate", "feature", "promo", "marketing",
}
VALID_CONFIDENCE = {"issuer_help", "issuer_legal", "issuer_blog", "secondary"}
REQUIRED_FIELDS = (
    "id", "value", "unit", "applies_to", "source_url", "source_quote",
    "verified_at", "expires", "status", "class", "confidence",
)
MAX_QUOTE_LEN = 200
# Only these statuses may ever reach a render — disputed/expired are tracked,
# never printed (plan: "писатель видит только `current`; `beta` печатается с
# пометкой; `disputed` не печатается").
PUBLISHABLE_STATUS = {"current", "beta"}
MARKER_BEGIN = "<!-- facts:begin -->"
MARKER_END = "<!-- facts:end -->"

CLASS_ORDER = [
    "affiliate", "rate", "fee", "limit", "availability", "feature", "marketing", "promo",
]
CLASS_TITLE = {
    "affiliate": "Affiliate program",
    "rate": "Cashback & FX rates",
    "fee": "Fees",
    "limit": "Limits",
    "availability": "Availability",
    "feature": "Features",
    "marketing": "Marketing",
    "promo": "Promotions",
}


class FactError(ValueError):
    """A fact record fails schema validation."""


def _validate(fact: dict[str, Any]) -> None:
    fid = fact.get("id", "<no id>")
    missing = [f for f in REQUIRED_FIELDS if f not in fact]
    if missing:
        raise FactError(f"{fid}: missing required field(s) {missing}")
    if fact["status"] not in VALID_STATUS:
        raise FactError(f"{fid}: invalid status {fact['status']!r}")
    if fact["class"] not in VALID_CLASS:
        raise FactError(f"{fid}: invalid class {fact['class']!r}")
    if fact["confidence"] not in VALID_CONFIDENCE:
        raise FactError(f"{fid}: invalid confidence {fact['confidence']!r}")
    # A promo-class row or a beta-status row can fall out of date on its own —
    # both require an `expires` (re-check-by, for beta) date or the row is a
    # defect that can never be caught later (Y63: this is exactly how "up to
    # 15% on food" survived four months past its own end date).
    if (fact["class"] == "promo" or fact["status"] == "beta") and not fact.get("expires"):
        raise FactError(f"{fid}: class/status requires 'expires' but none is set")
    quote = fact.get("source_quote") or ""
    if len(quote) > MAX_QUOTE_LEN:
        raise FactError(f"{fid}: source_quote exceeds {MAX_QUOTE_LEN} chars ({len(quote)})")
    fact.setdefault("history", [])
    fact.setdefault("promo_restricted", [])


def load(path: Path = DEFAULT_FACTS_PATH) -> list[dict[str, Any]]:
    """Load and validate every fact record. Raises FactError on the first bad row."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    facts = data.get("facts") or []
    seen: set[str] = set()
    for fact in facts:
        _validate(fact)
        if fact["id"] in seen:
            raise FactError(f"{fact['id']}: duplicate id")
        seen.add(fact["id"])
    return facts


def load_all(facts_dir: Path = DEFAULT_FACTS_PATH.parent) -> list[dict[str, Any]]:
    """Every record of every `facts_dir/*.yaml`, sorted by filename. Ids are unique
    across files (FactError on a duplicate) — the gate and the writer prompt read
    this one function, so a brand's facts can never be visible to one and not the other."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(facts_dir.glob("*.yaml")):
        for fact in load(path):
            if fact["id"] in seen:
                raise FactError(f"{fact['id']}: duplicate id across files ({path.name})")
            seen.add(fact["id"])
            out.append(fact)
    return out


def _format_value(fact: dict[str, Any]) -> str:
    unit = fact.get("unit") or ""
    sep = "" if unit in ("", "%") else " "
    val = f"{fact['value']}{sep}{unit}"
    if fact["status"] == "beta":
        val += " (beta)"
    return val


def render(facts: list[dict[str, Any]]) -> str:
    """Render publishable facts as markdown tables, grouped by class.

    Only `current`/`beta` facts are emitted — see PUBLISHABLE_STATUS. Output
    is wrapped in the `<!-- facts:begin -->` / `<!-- facts:end -->` markers so
    it can be dropped into a skill file and re-generated idempotently.
    """
    by_class: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        if fact["status"] not in PUBLISHABLE_STATUS:
            continue
        by_class.setdefault(fact["class"], []).append(fact)

    lines = [
        MARKER_BEGIN,
        "*Generated by `scripts/facts-render.py` from `config/facts/etherfi.yaml` "
        "— do not hand-edit inside this block.*",
        "",
    ]
    for cls in CLASS_ORDER:
        rows = by_class.get(cls)
        if not rows:
            continue
        lines.append(f"### {CLASS_TITLE.get(cls, cls.title())}")
        lines.append("")
        lines.append("| Fact | Value | Verified | Source |")
        lines.append("|---|---|---|---|")
        for fact in sorted(rows, key=lambda f: f["id"]):
            lines.append(
                f"| `{fact['id']}` | {_format_value(fact)} | {fact['verified_at']} "
                f"| {fact['source_url']} |"
            )
        lines.append("")
    lines.append(MARKER_END)
    return "\n".join(lines)


def _numeric_tokens_for_statuses(facts: list[dict[str, Any]], statuses: set[str]) -> list[str]:
    """Shared body for `disputed_numeric_tokens`/`stale_numeric_tokens` — one
    formatting rule for both, so the two never drift into disagreeing on what
    a token looks like."""
    tokens = []
    for fact in facts:
        if fact["status"] not in statuses:
            continue
        value = fact["value"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        token = f"{int(value):,}"
        unit = fact.get("unit") or ""
        if "/" in unit:
            token += "/" + unit.split("/", 1)[1]
        tokens.append(token)
    return tokens


def disputed_numeric_tokens(facts: list[dict[str, Any]]) -> list[str]:
    """Comma-formatted digit strings for every `disputed` fact with a numeric
    value — e.g. `30000` USD/day -> `"30,000/day"`. A hand-maintained ban list
    (like `facts-render.py`'s `_PROSE_BANNED`) only catches a contradiction
    someone already noticed; this derives the ban straight from the fact
    record, so a disputed number stated as settled fact in prose is caught
    even if nobody thought to list it (ruling-3, T-w1-07a-skill: the
    daily-spend-limit figures were `disputed` in the model but still printed
    as fact at SKILL.md:23 and :217-219, and `--check` had no way to see
    that). The bare comma-formatted number is unsafe alone — `50,000` is also
    a legitimate, unrelated `current` cashback-band boundary elsewhere in the
    same file — so a unit with a `/` (a rate, e.g. `USD/day`) has its suffix
    appended to disambiguate; a bare unit falls back to the plain number."""
    return _numeric_tokens_for_statuses(facts, {"disputed"})


def stale_numeric_tokens(facts: list[dict[str, Any]]) -> list[str]:
    """Both `disputed` and `expired` — for a gate that must refuse EITHER
    kind of unsettled claim (E-20's video G35), not just a disputed one. Same
    token shape as `disputed_numeric_tokens`, just a wider status set."""
    return _numeric_tokens_for_statuses(facts, {"disputed", "expired"})


def prose_leaks_disputed_values(skill_text: str, facts: list[dict[str, Any]]) -> list[str]:
    """Disputed numeric tokens (see above) that still appear in `skill_text`
    outside the generated facts:begin/end block. Non-empty return means a
    disputed fact is being asserted as settled somewhere in the document."""
    block = extract_marker_block(skill_text)
    outside = skill_text.replace(block, "", 1) if block else skill_text
    return [t for t in disputed_numeric_tokens(facts) if t in outside]


def extract_marker_block(text: str) -> str | None:
    """Return the exact text between the markers (markers included), or None if absent."""
    start = text.find(MARKER_BEGIN)
    end = text.find(MARKER_END)
    if start == -1 or end == -1 or end < start:
        return None
    return text[start:end + len(MARKER_END)]


def is_in_sync(skill_text: str, rendered: str) -> bool:
    """True if the marker block already present in `skill_text` matches `rendered`
    byte-for-byte. False if markers are missing OR the content has drifted
    (including a hand-edit inside the markers — the render is not idempotent
    against a manual change, by design)."""
    current = extract_marker_block(skill_text)
    return current is not None and current == rendered


def apply_markers(skill_text: str, rendered: str, insert_after: str) -> str:
    """Replace the marker block in `skill_text` with `rendered`, or insert it
    right after `insert_after` if no markers exist yet. Raises FactError if
    `insert_after` cannot be found."""
    current = extract_marker_block(skill_text)
    if current is not None:
        return skill_text.replace(current, rendered, 1)
    idx = skill_text.find(insert_after)
    if idx == -1:
        raise FactError(f"insert_after anchor not found: {insert_after[:60]!r}")
    idx += len(insert_after)
    return skill_text[:idx] + "\n\n" + rendered + "\n" + skill_text[idx:]
