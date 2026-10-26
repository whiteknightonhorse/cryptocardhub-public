#!/usr/bin/env python3
"""market-digest.py — Y74, stream 3: a dated market digest, or nothing at all.

WHAT THIS IS
------------
Once a month, "what changed in crypto cards in <month>" — assembled from the
fact journal that `availability-check.py` writes every day when the issuer's
own pages disagree with ours. Fable's point when specifying it: we already own
this dataset and do not treat it as one. The availability matrix, the rate
tables, the promos and their expiry dates are data ABOUT THE MARKET, and they
are maintained as a by-product of keeping our own pages honest. Their marginal
cost is zero.

WHAT MAKES IT NOT IMITATION
---------------------------
**If nothing changed, nothing is published.** A monthly page that must appear
whatever the month contained is exactly the "pages per week as a KPI" that this
site already has a name for: 199 double zeros. So the refusal is the default
and the page is the exception, and every refusal writes a JSONL line naming its
cause — a zero has to carry its reason (Y55), or it becomes silence.

Every item in a published digest carries the issuer URL it came from and the
date it was observed. Nothing is written that a human did not already verify
against a primary source: the journal only records divergences the checker
found against the issuer's own page.

Usage:
    market-digest.py                    # what would it publish? writes nothing
    market-digest.py --write            # publish if there is anything to say
    market-digest.py --month 2026-07    # a specific month
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from flags import pseo_enabled  # noqa: E402 -- LAW #ONE-PLACE kill-switch

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "seo-site" / "src" / "content" / "pseo"
LOG_DIR = ROOT / "logs" / "pseo"

# --- the law, as literals (mirrored in scripts/tests/test_market_digest.py) --
MIN_ITEMS = 1          # below this, publish nothing at all
WINDOW_DAYS = 31
BUCKET = "news"


def _checker():
    spec = importlib.util.spec_from_file_location(
        "availability_check", ROOT / "scripts" / "availability-check.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["availability_check"] = mod
    spec.loader.exec_module(mod)
    return mod


def _changelog_link() -> str | None:
    """Link to E-16's changelog page, but only if it actually exists — an
    empty event ledger means no changelog page is published, and a link to a
    URL that doesn't exist is a dead internal link (G20)."""
    spec = importlib.util.spec_from_file_location(
        "build_changelog", ROOT / "scripts" / "build-changelog.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_changelog"] = mod
    spec.loader.exec_module(mod)
    if not (CONTENT / f"{mod.SLUG}.md").is_file():
        return None
    return f"/{mod.BUCKET}/{mod.SLUG}/"


def slug_for(month: str) -> str:
    return f"crypto-card-market-changes-{month}"


def month_bounds(month: str) -> tuple[date, date]:
    y, m = (int(x) for x in month.split("-"))
    first = date(y, m, 1)
    nxt = date(y + (m == 12), (m % 12) + 1, 1)
    return first, nxt - timedelta(days=1)


def entries_for(month: str) -> list[dict]:
    """Journal lines observed inside that calendar month."""
    ac = _checker()
    first, last = month_bounds(month)
    out = []
    for row in ac.journal_since(WINDOW_DAYS * 2):
        try:
            when = date.fromisoformat(row.get("observed", ""))
        except ValueError:
            continue
        if first <= when <= last:
            out.append(row)
    return sorted(out, key=lambda r: r["observed"])


def refuse(month: str, reason: str, count: int, write: bool) -> int:
    """A zero carries its cause, on a surface someone reads."""
    record = {"event": "digest_refused", "month": month, "reason": reason,
              "items": count,
              "ts": datetime.now(timezone.utc).isoformat()}
    print(json.dumps(record, ensure_ascii=False))
    if write:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / f"market-digest-{date.today().isoformat()}.jsonl").open(
                "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"[digest] {month}: nothing to publish — {reason}", file=sys.stderr)
    return 0


HUMAN = {"availability": "Where the card is available",
         "rates": "Cashback rates",
         "promo_expired": "Promotions"}


def build(month: str, rows: list[dict]) -> dict:
    """The page, from the journal. No prose is invented: every bullet is one
    observed divergence plus the source it was observed against."""
    first, last = month_bounds(month)
    pretty = first.strftime("%B %Y")
    by_kind: dict[str, list[dict]] = {}
    for r in rows:
        by_kind.setdefault(r.get("kind", "other"), []).append(r)

    bullets = []
    for kind, items in by_kind.items():
        for r in items:
            bullets.append(f"**{HUMAN.get(kind, kind)} — {r['observed']}:** "
                           f"{r['summary']}")

    body = [f"## What changed in {pretty}", ""]
    link = _changelog_link()
    if link:
        body.append("For every verified change to ether.fi Cash's own rates "
                    f"and terms, see the full [ether.fi Cash changelog]({link}).")
        body.append("")
    for kind, items in by_kind.items():
        body.append(f"### {HUMAN.get(kind, kind)}")
        body.append("")
        for r in items:
            src = r.get("source") or ""
            line = f"- **{r['observed']}** — {r['summary']}"
            if src:
                line += f" ([issuer source]({src}))"
            body.append(line)
        body.append("")
    body += [
        "### How this page is made",
        "",
        "Every line above is a difference our daily checker found between an "
        "issuer's own help page and the figures we publish, on the date shown. "
        "Nothing here is an estimate or a forecast. Months in which nothing "
        "changed do not get a page.",
        "",
    ]

    return {
        "title": f"Crypto Card Market Changes — {pretty}",
        "description": (f"Dated log of what changed for crypto cards in "
                        f"{pretty}: availability, cashback rates and promotions, "
                        f"each with the issuer source it was verified against."),
        "h1": f"What changed for crypto cards in {pretty}",
        "slug": slug_for(month),
        "bucket": BUCKET,
        "primary_keyword": f"crypto card changes {first.strftime('%B %Y').lower()}",
        "supporting_keywords": ["crypto card rate changes", "crypto card availability"],
        "intro_paragraph": (
            "DefyCard publishes affiliate-linked reviews; we may earn a commission "
            "when you sign up through our links. **In short:** this is a dated log "
            f"of every change we recorded for crypto cards in {pretty} — "
            "availability, cashback rates and promotions — each verified against "
            "the issuer's own help pages on the day it was observed."),
        "snapshot_bullets": bullets[:6] + [f"**Last verified:** {date.today().isoformat()}"],
        "what_to_watch": [],
        "bottom_line": [
            f"**{len(rows)} change(s) recorded in {pretty}.** Each is dated and "
            f"sourced to the issuer's own page.",
        ],
        "faq": [],
        "related_clusters": [],
        "body_markdown": "\n".join(body),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": "none (assembled from the fact journal)",
        "pattern": "P-Digest",
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
    ap.add_argument("--month", default=None,
                    help="YYYY-MM (default: the month that just ended)")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    # E-02: checked before EVERYTHING else, including the no-write preview
    # mode -- see the same check in pseo-generate.py's _main() for why.
    if not pseo_enabled():
        print(json.dumps({"event": "skip", "reason": "pseo_disabled"}))
        print("[digest] skip: pseo_disabled (PSEO_ENABLED=false)")
        return 0

    if a.month:
        month = a.month
    else:
        first_of_this = date.today().replace(day=1)
        month = (first_of_this - timedelta(days=1)).strftime("%Y-%m")

    if not re.match(r"^\d{4}-\d{2}$", month):
        print(f"[digest] bad --month {month!r}", file=sys.stderr)
        return 2

    slug = slug_for(month)
    if (CONTENT / f"{slug}.md").exists():
        return refuse(month, "a digest for this month already exists", 0, a.write)

    rows = entries_for(month)
    print(f"[digest] {month}: {len(rows)} journal entries in the window")
    if len(rows) < MIN_ITEMS:
        return refuse(month, "the fact journal recorded no market changes",
                      len(rows), a.write)

    page = build(month, rows)
    if not a.write:
        print(f"[digest] WOULD publish /{BUCKET}/{slug}/ with {len(rows)} item(s):")
        for r in rows:
            print(f"    {r['observed']}  {r.get('kind')}  {r['summary'][:90]}")
        return 0

    path = write_page(page)
    print(json.dumps({"event": "digest_published", "month": month,
                      "items": len(rows), "path": str(path),
                      "url": f"/{BUCKET}/{slug}/",
                      "ts": datetime.now(timezone.utc).isoformat()},
                     ensure_ascii=False))
    print(f"[digest] wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
