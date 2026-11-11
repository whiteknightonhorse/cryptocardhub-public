#!/usr/bin/env python3
"""flags.py — shared kill-switch flags for the pSEO pipelines.

LAW #ONE-PLACE (slug_lib.py's own rule, restated): one function, every
consumer imports it, so the flag can never read two different ways in two
scripts.
"""
from __future__ import annotations

import os


def pseo_enabled() -> bool:
    """Fleet-wide pSEO kill-switch. Read fresh on every call (no caching at
    import time) so a long-running probabilistic-gate cron sees a `.env`
    edit without needing a restart.

    Unset means ENABLED — the opposite of VIDEO_GENERATOR_ENABLED (unset =
    off). Fleet has no write access to `.env` in this sandbox (E-02's own
    plan says so); if unset meant "disabled", pSEO printing would already be
    stopped and could never resume until a human added the line by hand.
    Unset = enabled makes the switch usable in both directions: a human sets
    `PSEO_ENABLED=false` to stop, and removes the line (or sets `true`) to
    resume, without that removal itself being a silent re-enable Fleet can't
    see coming.
    """
    return os.environ.get("PSEO_ENABLED", "true").strip().lower() != "false"


# New-URL freeze (DEFY-SEO-1110 B8). Literals on purpose: no env override and
# no date-based auto-lift -- lifting is a new card that edits this line after an
# operator decision. Editing existing pages is NOT covered by the freeze.
NEW_URLS_FROZEN = True
NEW_URL_FREEZE_SINCE = "2026-11-11"
NEW_URL_FREEZE_REVIEW = "2027-01-10"
NEW_URL_FREEZE_BY = "DEFY-SEO-1110 B8, operator 2026-11-11"


def new_urls_frozen() -> bool:
    return NEW_URLS_FROZEN


def freeze_skip_row() -> dict:
    return {"event": "skip", "reason": "new_urls_frozen",
            "since": NEW_URL_FREEZE_SINCE, "review": NEW_URL_FREEZE_REVIEW,
            "by": NEW_URL_FREEZE_BY}


def _demo() -> None:
    os.environ.pop("PSEO_ENABLED", None)
    assert pseo_enabled() is True, "unset must mean enabled"
    os.environ["PSEO_ENABLED"] = "false"
    assert pseo_enabled() is False
    os.environ["PSEO_ENABLED"] = "FALSE"
    assert pseo_enabled() is False, "comparison must be case-insensitive"
    os.environ["PSEO_ENABLED"] = "true"
    assert pseo_enabled() is True
    os.environ.pop("PSEO_ENABLED", None)
    print("flags self-check OK")


if __name__ == "__main__":
    _demo()
