#!/usr/bin/env python3
"""money_gate.py — G36 (E-11): every number in money copy must come from a fact.

WHY THIS EXISTS
---------------
Y63/Y69: "up to 15 % on dining/groceries" was published as a CURRENT property
of ether.fi Cash on 307 of 544 pages, months after the promo ended, because
nothing compared a number in our prose against a number we had verified. The
fact model (E-07a, `config/facts/etherfi.yaml`) made the verified numbers
machine-readable; this is the half that refuses a page stating one we never
verified.

THE LAW
-------
A money token = a number carrying a money unit (`%`, `$`/USD/USDC, `days`,
`months`, `SCR`). Every money token in generated copy must match, by value AND
unit:

  * a `current`/`beta` fact in `config/facts/etherfi.yaml` — and, if the text
    names a member of that fact's `applies_to` dimension within RADIUS chars
    (a tier, a currency), it must be THAT fact's member, not a sibling's; or
  * a number in `config/rates.yaml`'s `levels` / `lock_days` / `min_claim_usd`
    — never its `promos`, which is where the expired 15 % still lives; or
  * a number in `config/availability.yaml`; or
  * a number already on the parent page (page-updates.py strengthens a page
    that exists — its own body is a source, a brand-new page has none).

Anything else is `unbacked_number:<token>`: the caller refuses, logs the
reason, and does not write the file.

There is deliberately NO kill-switch env var (plan E-11: "флаг MONEY_GATE=off
отсутствует намеренно" — rollback is a revert of the commit).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import facts_lib  # noqa: E402 -- LAW #ONE-PLACE: the one fact loader

RATES_PATH = ROOT / "config" / "rates.yaml"
AVAILABILITY_PATH = ROOT / "config" / "availability.yaml"

RADIUS = 60          # chars each side of a token that count as "next to it"

# --- tokenizer --------------------------------------------------------------
# One number + one money unit. A unit is what makes a number a money claim:
# "3" is nothing, "3 %" is a rate we either verified or invented.
_NUM = r"\d[\d,]*(?:\.\d+)?"
_UNIT_RES: list[tuple[str, re.Pattern[str]]] = [
    ("%", re.compile(rf"({_NUM})\s*(?:%|percent\b|per cent\b)", re.I)),
    ("USD", re.compile(rf"\$\s?({_NUM})")),
    ("USD", re.compile(rf"({_NUM})\s*(?:USD|USDC|dollars?)\b", re.I)),
    ("SCR", re.compile(rf"({_NUM})\s*SCR\b")),
    # Only the qualifiers that really occur between the number and the unit —
    # any word would make "$30,000 a day" read as "30000 days".
    ("days", re.compile(rf"({_NUM})[\s-]*(?:business[\s-]+|working[\s-]+|calendar[\s-]+)?days?\b",
                        re.I)),
    ("months", re.compile(rf"({_NUM})[\s-]*months?\b", re.I)),
]
# "20–25 %", "1–2 business days", "$2,001 – $3,000": the regex above sees only
# the SECOND number (the unit sits after it), so the first is read off the text
# immediately before the match. One level back is all a range ever needs.
_RANGE_TAIL = re.compile(rf"(?:\$\s?)?({_NUM})\s*[-–—]\s*(?:\$\s?)?$")

# `unit:` values in the fact model -> the tokenizer's unit classes.
_FACT_UNITS = {"%": "%", "USD": "USD", "USD/day": "USD", "days": "days", "months": "months"}


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _fmt(value: float, unit: str) -> str:
    n = f"{value:g}"
    if unit == "%":
        return f"{n}%"
    if unit == "USD":
        return f"${n}"
    return f"{n} {unit}"


_JSON_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")


def tokens(text: str) -> list[tuple[float, str, int, int]]:
    """Every money token in `text` as (value, unit, start, end).

    `start`/`end` bound the matched slice, used for the applies_to radius.

    A page file's frontmatter is JSON, so its en-dashes arrive as the literal
    six characters `\\u2013` — and "2\\u20133 days" reads as the number 20133
    unless they are decoded first. Measured on the live corpus: 24 of 60
    sampled pages produced a phantom "20133 days" token.
    """
    text = _JSON_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)
    out: list[tuple[float, str, int, int]] = []
    for unit, rx in _UNIT_RES:
        for m in rx.finditer(text):
            out.append((_num(m.group(1)), unit, m.start(), m.end()))
            head = _RANGE_TAIL.search(text[:m.start()])
            if head:
                out.append((_num(head.group(1)), unit, head.start(), m.end()))
    return sorted(out, key=lambda t: t[2])


def _pairs(text: str) -> set[tuple[float, str]]:
    return {(v, u) for v, u, _s, _e in tokens(text)}


# --- backing sources --------------------------------------------------------

def fact_rows(facts: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Publishable facts with a numeric value, as {value, unit, applies_to}.

    `disputed`/`expired` never back anything — that is the whole point of the
    status field (facts_lib.PUBLISHABLE_STATUS is the one definition).
    """
    facts = facts_lib.load_all() if facts is None else facts
    rows = []
    for f in facts:
        if f["status"] not in facts_lib.PUBLISHABLE_STATUS:
            continue
        v = f.get("value")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        unit = _FACT_UNITS.get(f.get("unit") or "")
        if not unit:
            continue
        rows.append({"value": float(v), "unit": unit, "applies_to": f.get("applies_to") or {}})
    return rows


def _rates_pairs(path: Path = RATES_PATH) -> set[tuple[float, str]]:
    """Numbers from rates.yaml — `levels` and the two prose scalars ONLY.

    `promos` is excluded on purpose: the expired "15%" (and its "10% for
    organic sign-ups") lives there, and a gate that let an EXPIRED promo back
    a number in new copy would re-open Y63 by hand.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: set[tuple[float, str]] = set()
    for bands in (data.get("levels") or {}).values():
        for band in bands or []:
            for cell in band.values():
                out |= _pairs(str(cell))
    if data.get("lock_days") is not None:
        out.add((float(data["lock_days"]), "days"))
    if data.get("min_claim_usd") is not None:
        out.add((float(data["min_claim_usd"]), "USD"))
    return out


def _availability_pairs(path: Path = AVAILABILITY_PATH) -> set[tuple[float, str]]:
    return _pairs(path.read_text(encoding="utf-8"))


def _static_pairs() -> set[tuple[float, str]]:
    return _rates_pairs() | _availability_pairs()


# --- the applies_to radius --------------------------------------------------

def _phrase(dim: str, val: Any) -> str:
    """How a fact's applies_to member is written in prose: `core`, `EUR`,
    `tier 4`. A bare numeric member ("tier: 1") is never matched as a bare
    digit — every second number in the corpus would name it."""
    s = str(val).lower()
    return s if s[:1].isalpha() else f"{dim} {s}"


def _compatible(row: dict[str, Any], window: str, all_rows: list[dict[str, Any]]) -> bool:
    """False when the window names a DIFFERENT member of a dimension this fact
    is scoped to — "Core physical card fee $100" against a $100 fact that
    applies to Luxe. A window that names no member at all is compatible: the
    plan's own acceptance ("20–25 %" passes) is exactly that case.
    """
    w = window.lower()
    for dim, val in (row.get("applies_to") or {}).items():
        siblings = {_phrase(dim, r["applies_to"][dim]) for r in all_rows
                    if dim in (r.get("applies_to") or {})}
        named = {p for p in siblings if p in w}
        if named and _phrase(dim, val) not in named:
            return False
    return True


# --- the gate ---------------------------------------------------------------

def unbacked(text: str, parent_text: str = "", facts: list[dict[str, Any]] | None = None) -> list[str]:
    """Money tokens in `text` that nothing backs, in order, deduplicated.

    Empty list = the copy may be written. `parent_text` is the page being
    strengthened (page-updates.py); a new page passes "".
    """
    facts = facts_lib.load_all() if facts is None else facts
    rows = fact_rows(facts)
    # Dimension members come from EVERY fact, disputed ones included: "Coinbase"
    # must be a named foreign member even while all Coinbase facts are unpublishable.
    members = [{"applies_to": f.get("applies_to") or {}} for f in facts]
    # Decode here too: `tokens()` reports offsets into the DECODED text, and
    # the applies_to window is sliced out of it below.
    text = _JSON_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)
    flat = _static_pairs() | _pairs(parent_text)
    bad: list[str] = []
    for value, unit, start, end in tokens(text):
        if (value, unit) in flat:
            continue
        window = text[max(0, start - RADIUS):end + RADIUS]
        cands = [r for r in rows if r["value"] == value and r["unit"] == unit]
        if any(_compatible(r, window, members) for r in cands):
            continue
        token = _fmt(value, unit)
        if token not in bad:
            bad.append(token)
    return bad


def allowed_tokens(facts: list[dict[str, Any]] | None = None) -> list[str]:
    """Every money token the gate accepts on a NEW page, rendered as `unbacked()`
    renders them, sorted. The writer prompt prints this list, so the writer and
    the gate read ONE function (LAW #ONE-PLACE). Parent-page numbers are not
    included: a brand-new page has no parent."""
    pairs = _static_pairs() | {(r["value"], r["unit"]) for r in fact_rows(facts)}
    return sorted({_fmt(v, u) for v, u in pairs}, key=lambda t: (t[-1] in "%", t))


def reasons(bad: Iterable[str]) -> list[str]:
    """`unbacked_number:<token>` — the refusal string the plan names."""
    return [f"unbacked_number:{t}" for t in bad]


if __name__ == "__main__":
    # Read copy on stdin (or a file path as argv[1]) and print its refusals.
    src = Path(sys.argv[1]).read_text(encoding="utf-8") if len(sys.argv) > 1 else sys.stdin.read()
    for r in reasons(unbacked(src)):
        print(r)
