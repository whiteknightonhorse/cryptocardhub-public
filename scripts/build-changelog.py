#!/usr/bin/env python3
"""build-changelog.py — E-16: the one changelog page, from the event ledger.

WHAT THIS IS
------------
`scripts/intel-route.py` writes one line to `logs/intel/changelog.jsonl` per
fact an ACCEPTED intel event actually changed — date, fact, old -> new value,
the quote it was verified against, the issuer source URL, the verified date,
and how many pages that correction touched. It already holds every field this
page needs (E-07c's own law: never recompute downstream what the producer
already knew), so this script only sorts and renders them.

WHAT MAKES IT NOT IMITATION
----------------------------
**If the ledger is empty, no page is published** — the same refusal
`market-digest.py` uses for a quiet month. A page that must exist regardless
of whether anything happened is the "pages per week as a KPI" this site
already has a name for. Unlike the digest, this is not period-scoped: there
is exactly ONE URL (`/learn/ether-fi-cash-changelog/`), rebuilt from the full
ledger every run.

WHY `generated_at` NEVER DRIFTS ON A NO-OP RUN
------------------------------------------------
`generated_at` is set to the LATEST event's own `date`, never `datetime.now()`.
Two runs against an unchanged ledger therefore produce byte-identical output,
so `freshness.mjs` (which reads this field straight into the sitemap
`lastmod` / Article `dateModified`) only moves when a new event genuinely
lands — the project's standing rule that `dateModified` follows a real edit,
not a rebuild.

Usage:
    build-changelog.py            # what would it publish? writes nothing
    build-changelog.py --write    # publish if the ledger has anything to say
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from flags import pseo_enabled  # noqa: E402 -- LAW #ONE-PLACE kill-switch
import facts_lib as fl  # noqa: E402 -- CLASS_TITLE, reused rather than re-typed

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "seo-site" / "src" / "content" / "pseo"
CHANGELOG_PATH = ROOT / "logs" / "intel" / "changelog.jsonl"
LOG_DIR = ROOT / "logs" / "intel"

# --- the law, as literals (mirrored in scripts/tests/test_build_changelog.py) -
MIN_ITEMS = 1
BUCKET = "learn"
SLUG = "ether-fi-cash-changelog"


def read_rows(path: Optional[Path] = None) -> list[dict]:
    """`path` resolves at CALL time, not at def time: a default argument is
    evaluated once at import, so a test or rehearsal that reassigns
    `CHANGELOG_PATH` on the module would silently keep reading the real
    ledger (Y54 — this is that bug, avoided)."""
    path = path or CHANGELOG_PATH
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _sort_key(row: dict) -> tuple:
    return (row.get("date", ""), row.get("verified_at", ""),
            row.get("event_id", ""), row.get("fact_id", ""))


def refuse(reason: str, count: int, write: bool) -> int:
    """A zero carries its cause, on a surface someone reads (Y55)."""
    record = {"event": "changelog_refused", "reason": reason, "items": count,
              "ts": datetime.now(timezone.utc).isoformat()}
    print(json.dumps(record, ensure_ascii=False))
    if write:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / "changelog-build.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"[changelog] nothing to publish — {reason}", file=sys.stderr)
    return 0


def build(rows: list[dict]) -> dict:
    """The page, from the ledger. Every entry is one accepted, verified
    change: date, fact, was -> now, the quote, the issuer source, verified."""
    ordered = sorted(rows, key=_sort_key, reverse=True)
    last_date = ordered[0]["date"]
    first_date = ordered[-1]["date"]

    body = ["## Every verified change to ether.fi Cash's terms", ""]
    for r in ordered:
        label = fl.CLASS_TITLE.get(r.get("fact_class"), r.get("fact_id"))
        body.append(f"### {r['date']} — {label}")
        body.append("")
        body.append(f"- **Was:** {r['old']}")
        body.append(f"- **Now:** {r['new']}")
        body.append(f"- **Quote:** \"{r['quote']}\"")
        body.append(f"- **Source:** [{r['source_url']}]({r['source_url']})")
        body.append(f"- **Verified:** {r['verified_at']}")
        body.append(f"- **Pages updated:** {r.get('pages_affected', 0)}")
        body.append("")
    body += [
        "### How this page is made",
        "",
        "Every entry above is a change our editorial review verified against "
        "ether.fi's own page, quoted at the moment it was accepted. Nothing "
        "here is a forecast or an estimate — months in which nothing was "
        "verified add no entry.",
        "",
    ]

    return {
        "title": "ether.fi Cash Changelog — Every Verified Change",
        "description": ("Dated log of every verified change to ether.fi "
                        "Cash's rates, fees and terms, each quoted and "
                        "sourced to the issuer's own page."),
        "h1": "ether.fi Cash changelog",
        "slug": SLUG,
        "bucket": BUCKET,
        "primary_keyword": "ether.fi cash changelog",
        "supporting_keywords": ["ether.fi cash changes", "ether.fi cash updates"],
        "intro_paragraph": (
            "DefyCard publishes affiliate-linked reviews; we may earn a "
            "commission when you sign up through our links. **In short:** "
            "this is a dated, sourced log of every verified change to "
            "ether.fi Cash's rates, fees and terms — no estimates, only "
            "what the issuer's own pages state."),
        "snapshot_bullets": [f"**{len(ordered)} verified change(s) on record.**",
                            f"**Last verified:** {last_date}"],
        "what_to_watch": [],
        "bottom_line": [
            f"**{len(ordered)} change(s) recorded.** Each is dated, quoted "
            f"and sourced to ether.fi's own page.",
        ],
        "faq": [],
        "related_clusters": [],
        "body_markdown": "\n".join(body),
        "schema_article": {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": "ether.fi Cash Changelog — Every Verified Change",
            "description": ("Dated log of every verified change to ether.fi "
                            "Cash's rates, fees and terms."),
            "datePublished": first_date,
            "author": {"@type": "Organization", "name": "DefyCard",
                      "url": "https://defycard.com"},
            "publisher": {"@type": "Organization", "name": "DefyCard",
                         "logo": {"@type": "ImageObject",
                                 "url": "https://defycard.com/logo.png"}},
        },
        "generated_at": last_date,
        "model": "none (assembled from logs/intel/changelog.jsonl)",
        "pattern": "P-Changelog",
    }


def write_page(page: dict) -> Path:
    f = CONTENT / f"{page['slug']}.md"
    body = page.pop("body_markdown")
    lines = ["---"]
    for k, v in page.items():
        lines.append(f"{k}: {json.dumps(v, ensure_ascii=True)}")
    lines += ["---", "", body, ""]
    f.write_text("\n".join(lines), encoding="utf-8")
    return f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    if not pseo_enabled():
        print(json.dumps({"event": "skip", "reason": "pseo_disabled"}))
        print("[changelog] skip: pseo_disabled (PSEO_ENABLED=false)")
        return 0

    rows = read_rows()
    print(f"[changelog] {len(rows)} ledger entries")
    if len(rows) < MIN_ITEMS:
        return refuse("the event ledger has no accepted, applied changes",
                      len(rows), a.write)

    page = build(rows)
    if not a.write:
        print(f"[changelog] WOULD publish /{BUCKET}/{SLUG}/ with {len(rows)} entr(ies)")
        return 0

    path = write_page(page)
    print(json.dumps({"event": "changelog_published", "items": len(rows),
                      "path": str(path), "url": f"/{BUCKET}/{SLUG}/",
                      "ts": datetime.now(timezone.utc).isoformat()},
                     ensure_ascii=False))
    print(f"[changelog] wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
