"""CCH-AVAIL-03: no page promises physical delivery to a virtual-card-only country.

Issuer article 758780 (captured 2026-11-24): in these 12 jurisdictions the card
issued is VIRTUAL; physical delivery is not available. The law is read from the
built dist (html + md sidecars), literals typed here, never computed from the
table that made the edit. GATE_DIST points the same law at a scratch dist for
the mutation proof.
"""
from __future__ import annotations

import html
import os
import re
from pathlib import Path

DIST = Path(os.getenv("GATE_DIST") or Path(__file__).resolve().parent.parent / "dist")

NAMES = ["Bahrain", "Belgium", "Bermuda", "Ireland", "Isle of Man", "Liechtenstein",
         "Maldives", "Mauritius", "Mexico", "Oman", "Seychelles", "Vanuatu"]
PROMISES = ["ships to", "shipping to", "delivered to", "ship the physical card"]
NEGATION = re.compile(r"\bnot\b|\bno physical\b", re.I)


def _pages():
    for p in DIST.rglob("index.html"):
        t = re.sub(r"<(script|style)\b.*?</\1>", " ", p.read_text(encoding="utf-8"),
                   flags=re.S | re.I)
        yield p, html.unescape(re.sub(r"<[^>]+>", " ", t))
    for p in DIST.rglob("index.md"):
        yield p, p.read_text(encoding="utf-8")


def _has(name: str, s: str) -> bool:
    return re.search(r"(?<!New )\b" + re.escape(name) + r"\b", s) is not None


def violations() -> list[str]:
    out = []
    for p, text in _pages():
        for s in re.split(r"(?<=[.!?])\s+|\n+", text):
            s = re.sub(r"\s+", " ", s)
            low = s.lower()
            for n in NAMES:
                if not _has(n, s):
                    continue
                if "refuses outright" in low or (
                        any(k in low for k in PROMISES) and not NEGATION.search(s)):
                    out.append(f"{p}: {n}: {s[:120]}")
    return out


def test_no_promise_of_physical_delivery_to_a_virtual_only_country():
    assert DIST.is_dir(), "dist/ not built: the gate refuses to pass on nothing"
    assert violations() == []
