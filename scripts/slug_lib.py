#!/usr/bin/env python3
"""slug_lib.py — the ONE slugify function for this repo.

LAW #ONE-PLACE: a rule written in more than one place survives its own
repeal. Four scripts each carried their own slugify() — pseo-generate.py and
faq-children.py used it to name real pages on disk, bing-keywords.py and
bing-expand.py used a second, DIFFERENT blind-truncation version to check
whether a page already exists. When the two disagreed, the dedup check
compared against a slug the writer would never produce, so it could judge a
keyword "new" when the canonical page for it already existed on disk.

Canonical form: truncate at a WORD BOUNDARY, not a blind slice. `[:60]` alone
can cut mid-word (`...supported-coun`) — a URL nobody types and nobody links,
and it is what 3 of 20 stuck faq-children candidates got stamped with.
"""
from __future__ import annotations

import re
from pathlib import Path

MAX_LEN = 60
REDIRECTS_MAP = Path(__file__).resolve().parent.parent / "seo-site" / "redirects.map"


def slugify(text: str) -> str:
    """lowercase, non-alnum runs -> '-', trimmed, truncated at a word boundary."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(s) <= MAX_LEN:
        return s
    cut = s[:MAX_LEN]
    return cut[:cut.rindex("-")] if "-" in cut else cut


def retired_slugs() -> set[str]:
    """Last path segment of every redirects.map source: a retired URL's slug
    must never be printed again (a deleted loser would reopen its cluster)."""
    out: set[str] = set()
    if REDIRECTS_MAP.exists():
        for line in REDIRECTS_MAP.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and not line.lstrip().startswith("#"):
                out.add(parts[0].rstrip("/").rsplit("/", 1)[-1])
    return out


def _demo() -> None:
    assert slugify("What countries support the card?") == "what-countries-support-the-card"
    # a phrase whose blind [:60] cut lands mid-word must back off to the last '-'
    long_q = "what countries support the card for daily spending limits abroad"
    s = slugify(long_q)
    assert len(s) <= MAX_LEN
    assert not s.endswith("-") and "-coun" not in s[-6:]
    assert slugify("  Multiple   --- dashes!! ") == "multiple-dashes"
    print("slug_lib self-check OK")


if __name__ == "__main__":
    _demo()
