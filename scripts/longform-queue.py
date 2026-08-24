#!/usr/bin/env python3
"""Build the long-form topic queue: state/longform_queue.json.

Y59. One pSEO page = one long-form video, for good. With 541 eligible pages and
a cadence of 2/week that is five years of runway, so there is no cooldown and no
repeat — dedup is simply "has this slug ever been published as longform".

Ranking is measured, not guessed: human sessions per landing page over 28 days
(GA4 counts humans who ran the JS; nginx counts bots, and that difference was
2.8x on this very site). Ties break on bucket priority, which comes from the
measured AEO fetch mix — answer engines pull how-to, faq and reviews far more
than anything else. No bucket may take more than 40% of the queue: a queue that
is nine-tenths one shape stops being a topic plan and becomes a rut.

Usage:
  python3 scripts/longform-queue.py             # build (GA4 if reachable)
  python3 scripts/longform-queue.py --dry-run   # print, write nothing
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PSEO = ROOT / "seo-site" / "src" / "content" / "pseo"
QUEUE = ROOT / "video-generator" / "state" / "longform_queue.json"

# Measured 2026-08-12 from 15 days of nginx logs: how-to 259, faq 240,
# reviews 234 fetches by answer agents; the rest trail far behind.
BUCKET_PRIORITY = ["reviews", "how-to", "faq", "compare", "geo",
                   "learn", "for", "help", "tools", "hub"]
MAX_SHARE_PER_BUCKET = 0.40
MIN_WORDS = 400


def _frontmatter(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        return {}
    try:
        import yaml
        return yaml.safe_load(raw.split("---", 2)[1]) or {}
    except Exception:  # noqa: BLE001
        return {}


def _eligible() -> list[dict]:
    """Pages with enough speakable words to fill the 185-260s band."""
    sys.path.insert(0, str(ROOT / "video-generator"))
    from pipeline.longform import frontmatter_words  # noqa: PLC0415

    out = []
    for f in sorted(PSEO.glob("*.md")):
        fm = _frontmatter(f)
        if not fm:
            continue
        try:
            words = frontmatter_words(fm)
        except Exception:  # noqa: BLE001
            continue
        if words < MIN_WORDS:
            continue
        out.append({"slug": fm.get("slug") or f.stem,
                    "bucket": fm.get("bucket") or "hub",
                    "words": words})
    return out


def _sessions_by_slug() -> dict[str, int]:
    """Human sessions per landing page, 28 days. Empty dict if GA4 is unreachable —
    an unavailable measurement must not silently become a zero ranking."""
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "weekly_report", ROOT / "scripts" / "weekly-report.py")
        wr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wr)  # type: ignore[union-attr]
        client = wr._ga4_client()  # noqa: SLF001
        until = date.today().isoformat()
        since = (date.today() - timedelta(days=28)).isoformat()
        rows = wr._run_report(client, ["landingPage"], ["sessions"],  # noqa: SLF001
                              since, until, limit=1000)
        out: dict[str, int] = {}
        for dims, mets in rows:
            slug = dims[0].strip("/").split("/")[-1]
            if slug:
                out[slug] = out.get(slug, 0) + int(mets[0] or 0)
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[longform-queue] GA4 unavailable ({type(e).__name__}: {e}) — "
              f"ranking on bucket priority alone", file=sys.stderr)
        return {}


def build() -> dict:
    pages = _eligible()
    sessions = _sessions_by_slug()

    def key(p: dict) -> tuple:
        prio = BUCKET_PRIORITY.index(p["bucket"]) if p["bucket"] in BUCKET_PRIORITY \
            else len(BUCKET_PRIORITY)
        return (-sessions.get(p["slug"], 0), prio, -p["words"], p["slug"])

    ranked = sorted(pages, key=key)
    cap = max(1, int(len(ranked) * MAX_SHARE_PER_BUCKET))
    taken: dict[str, int] = {}
    queue: list[str] = []
    for p in ranked:
        b = p["bucket"]
        if taken.get(b, 0) >= cap:
            continue
        taken[b] = taken.get(b, 0) + 1
        queue.append(p["slug"])
    return {
        "built_on": date.today().isoformat(),
        "ranked_by": "ga4_human_sessions_28d" if sessions else "bucket_priority_only",
        "eligible": len(pages),
        "per_bucket_cap": cap,
        "slugs": queue,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    blob = build()
    print(json.dumps({k: v for k, v in blob.items() if k != "slugs"}, indent=2))
    print(f"first 10: {blob['slugs'][:10]}")
    if not args.dry_run:
        QUEUE.parent.mkdir(parents=True, exist_ok=True)
        QUEUE.write_text(json.dumps(blob, indent=2), encoding="utf-8")
        print(f"written: {QUEUE} ({len(blob['slugs'])} slugs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
