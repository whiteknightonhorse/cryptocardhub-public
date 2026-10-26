#!/usr/bin/env python3
"""promote-candidates.py — Y-freeze-lift: CANDIDATES.md -> the keyword bank.

Fable lifted the keyword-transfer freeze on 2026-09-01: it had rested on the
654 row, which was half vulnerability scanners (see aeo-aggregate.py). The
gate this script enforces is CODE, not a human's judgement call:

  * dedup against the CURRENT bank by NORMALIZED text — a query already
    covered, reworded, never gets a second row (LAW #ONE-PLACE-ish: one
    concept, one page);
  * a candidate needs CONFIRMED Bing impressions > 0 — bing-keywords.py
    already enforces its own MIN_IMPRESSIONS floor when it writes
    CANDIDATES.md, this is a second, cheap, defensive check at the door
    a reader with different code must not have to trust the writer blindly;
  * the ban on RECOMBINING existing bank keys stays in force — nothing here
    invents a query. Every row this script can possibly write came verbatim
    out of CANDIDATES.md, which is itself Bing's own answer, an external
    fact, never a splice of two bank rows;
  * CEILING: at most 10 promotions per ISO week, tracked by counting this
    file's own ledger (logs/pseo/keyword-promotions.jsonl), so re-running
    the script mid-week tops up rather than re-promoting past the cap.

Every run appends to the ledger — what, from where, how many impressions —
even when nothing moves: a zero week writes a `nothing_eligible` line, never
silence (mirrors the FAQ/market-digest streams' three-outcome discipline).

`CANDIDATES.md`'s table format is pinned by
scripts/tests/test_promote_candidates.py BEFORE this file trusted it — the
writer (bing-keywords.py) and this reader are the two "siblings" that could
drift apart exactly like the apibase queue/parser split once did.

Usage:
    promote-candidates.py            # promote, write SKILL.md + ledger
    promote-candidates.py --dry-run  # show what would move, write nothing
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES = ROOT / ".claude" / "skills" / "keywords" / "CANDIDATES.md"
BANK = ROOT / ".claude" / "skills" / "keywords" / "SKILL.md"
LEDGER = ROOT / "logs" / "pseo" / "keyword-promotions.jsonl"

PSEO_DIR = ROOT / "seo-site" / "src" / "content" / "pseo"

WEEKLY_CAP = 10


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod   # pseo-generate.py's @dataclass needs this pre-registered
    spec.loader.exec_module(mod)
    return mod


pg = _load("pseo_generate", "scripts/pseo-generate.py")
bk = _load("bing_keywords", "scripts/bing-keywords.py")
normalize = bk.normalize   # LAW #ONE-PLACE: one normalize(), bing-keywords.py owns it (it aggregates with it first)

# ---------------------------------------------------------------------------
# Read CANDIDATES.md — the table format bing-keywords.py's render() writes.
# ---------------------------------------------------------------------------

_ROW_RE = re.compile(
    r"^\|\s*(?P<query>[^|]+?)\s*\|\s*(?P<impressions>\d+)\s*\|\s*(?P<clicks>\d+)\s*\|"
    r"\s*`(?P<slug>[^`]+)`\s*\|\s*(?P<measurement>[^|]*)\|\s*$"
)
_UPDATE_ROW_RE = re.compile(
    r"^\|\s*(?P<query>[^|]+?)\s*\|\s*(?P<impressions>\d+)\s*\|\s*(?P<clicks>\d+)\s*\|"
    r"\s*`(?P<path>[^`]+)`\s*\|\s*(?P<page_impressions>\d+)\s*\|\s*(?P<page_clicks>\d+)\s*\|"
    r"\s*(?P<position>[\d.]+)\s*\|\s*$"
)
_FETCHED_RE = re.compile(r"^-\s*fetched:\s*`([^`]+)`")


def _section(text: str, heading: str) -> str | None:
    """Text between an H2 `heading` and the next H2 (or EOF). None if absent."""
    m = re.search(rf"^## {re.escape(heading)}\s*$", text, re.M)
    if not m:
        return None
    rest = text[m.end():]
    end = re.search(r"^## ", rest, re.M)
    return rest[:end.start()] if end else rest


def parse_candidates(path: Path = CANDIDATES) -> dict[str, Any]:
    """-> {"fetched_at": str | None, "rows": [{"query", "impressions",
    "clicks", "slug", "measurement"}]}. Never raises on an empty/absent
    file — an empty candidate list is a legitimate answer, not an error.

    Reads ONLY the `## New-page candidates` block (Fable A4 step 2) when that
    heading exists, so an `## Update candidates` block sitting right below it
    can never be mistaken for a new-page row. A file with neither heading —
    the format before 2026-09-14 — is scanned whole, unchanged: the pinned
    fixture in test_promote_candidates.py is exactly that shape.
    """
    if not path.is_file():
        return {"fetched_at": None, "rows": []}
    text = path.read_text(encoding="utf-8")
    fetched_at = None
    for line in text.splitlines():
        fm = _FETCHED_RE.match(line.strip())
        if fm:
            fetched_at = fm.group(1)
            break
    section = _section(text, "New-page candidates")
    if section is None:
        section = text
    rows: list[dict[str, Any]] = []
    for line in section.splitlines():
        m = _ROW_RE.match(line)
        if not m:
            continue
        rows.append({
            "query": m.group("query").strip(),
            "impressions": int(m.group("impressions")),
            "clicks": int(m.group("clicks")),
            "slug": m.group("slug").strip(),
            "measurement": m.group("measurement").strip(),
        })
    return {"fetched_at": fetched_at, "rows": rows}


def parse_update_candidates(path: Path = CANDIDATES) -> list[dict[str, Any]]:
    """-> [{"query", "impressions", "clicks", "path", "page_impressions",
    "page_clicks", "position"}] from the `## Update candidates` block.
    Empty (never raises) if the heading or the file is absent — a page-updates
    run with nothing to strengthen is a legitimate, silent-free zero."""
    if not path.is_file():
        return []
    section = _section(path.read_text(encoding="utf-8"), "Update candidates")
    if section is None:
        return []
    rows: list[dict[str, Any]] = []
    for line in section.splitlines():
        m = _UPDATE_ROW_RE.match(line)
        if not m:
            continue
        rows.append({
            "query": m.group("query").strip(),
            "impressions": int(m.group("impressions")),
            "clicks": int(m.group("clicks")),
            "path": m.group("path").strip(),
            "page_impressions": int(m.group("page_impressions")),
            "page_clicks": int(m.group("page_clicks")),
            "position": float(m.group("position")),
        })
    return rows


# ---------------------------------------------------------------------------
# Bucket classification — deterministic, no invented queries, no guessing
# which of the 12 existing buckets a NEW-TO-US query belongs in beyond what
# its own words say.
# ---------------------------------------------------------------------------

BRANDS = ("ether.fi", "etherfi", "crypto.com", "cryptocom", "coinbase",
         "binance", "bybit", "gnosis", "redotpay", "bleap", "nexo", "wirex",
         "metamask", "kast", "uphold", "gemini")


def classify_bucket(query: str) -> str:
    """-> one of pg.BUCKET_META's letters. Order matters; first match wins."""
    q = query.lower()
    words = q.split()
    brand_hits = sum(1 for b in BRANDS if b in q)
    if q.startswith("how to "):
        return "F"                                    # how-to / tutorial
    if " vs " in q or " vs. " in q:
        return "B" if brand_hits >= 2 else "I"          # brand pair vs concept pair
    if ("top" in words or "best" in words or "picks" in q) and \
       ("cashback" in q or "rebate" in q or "card" in q):
        return "A"                                    # hub / listicle
    if brand_hits >= 1 and len(words) <= 4:
        return "C"                                    # bare/short brand mention -> review
    if " for " in q or q.startswith("for "):
        return "G"                                    # use-case
    return "J"                                        # decision/pain-point catch-all


def bucket_weight(letter: str) -> int:
    """-> the pipeline weight of the URL segment this bucket letter writes
    into. 0 means pseo-generate.py's build_clusters() will NEVER produce a
    page for it (BUCKET_WEIGHTS["for"] = BUCKET_WEIGHTS["tools"] = 0) —
    promoting a query into a dead bucket buries it in the bank forever while
    queue_funnel() still counts it "covered" (Fable A1: exactly what happened
    to G81, "binance card usage for everyday purchases", on 2026-09-01)."""
    segment = pg.BUCKET_META.get(letter, (None, None))[0]
    return pg.BUCKET_WEIGHTS.get(segment, 1)


# ---------------------------------------------------------------------------
# The bank: dedup set + where/how to insert a new row
# ---------------------------------------------------------------------------

def existing_normalized(rows: list) -> set[str]:
    return {normalize(r.keyword) for r in rows}


def bucket_section_span(text: str, letter: str) -> tuple[int, int] | None:
    matches = list(pg.BUCKET_HEADER_RE.finditer(text))
    for i, m in enumerate(matches):
        if m.group(1) == letter:
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            return start, end
    return None


def next_code(text: str, letter: str) -> int:
    span = bucket_section_span(text, letter)
    if span is None:
        return 1
    section = text[span[0]:span[1]]
    nums = [int(m.group(1)[len(letter):]) for m in pg.TABLE_ROW_RE.finditer(section)
           if re.match(rf"^{letter}\d+$", m.group(1))]
    return (max(nums) + 1) if nums else 1


def last_row_column_count(text: str, letter: str) -> int:
    """Match the existing table's shape exactly (5, 6 or 7 columns) rather
    than guessing one layout for every bucket."""
    span = bucket_section_span(text, letter)
    if span is None:
        return 5
    section = text[span[0]:span[1]]
    last = None
    for line in section.splitlines():
        if re.match(rf"^\|\s*{letter}\d+\s*\|", line):
            last = line
    if last is None:
        return 5
    return len([c for c in last.strip().strip("|").split("|")])


def render_row(code: str, keyword: str, impressions: int, clicks: int,
              window_end: str, n_cols: int, today: str) -> str:
    cells = [code, keyword, f"{impressions:,} (bing, confirmed)"]
    if n_cols >= 4:
        cells.append("—")
    if n_cols >= 5:
        cells.append("")
    if n_cols >= 6:
        cells.append(f"promoted {today}, bing impressions={impressions} "
                     f"clicks={clicks} window ..{window_end}")
    while len(cells) < n_cols:
        cells.append("")
    return "| " + " | ".join(cells) + " |"


def insert_row(text: str, letter: str, row: str) -> str:
    span = bucket_section_span(text, letter)
    if span is None:
        raise ValueError(f"bucket {letter} not found in {BANK}")
    section = text[span[0]:span[1]]
    lines = section.split("\n")
    last_idx = None
    for i, line in enumerate(lines):
        if re.match(rf"^\|\s*{letter}\d+\s*\|", line):
            last_idx = i
    if last_idx is None:
        raise ValueError(f"bucket {letter} has no existing rows to anchor after")
    lines.insert(last_idx + 1, row)
    new_section = "\n".join(lines)
    return text[:span[0]] + new_section + text[span[1]:]


# ---------------------------------------------------------------------------
# Cannibalisation law (CCH-KEYWORDS-1019 ruling-1): a keyword already assigned
# to a page — as its primary OR one of its supporting — is not a new cluster.
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_PRIMARY_RE = re.compile(r'^primary_keyword:\s*(".*")\s*$', re.M)
_SUPPORTING_RE = re.compile(r"^supporting_keywords:\s*(\[.*\])\s*$", re.M)


def page_keywords(pseo_dir: Path | None = None) -> tuple[set[str], list[str]]:
    """-> (normalized primary+supporting keywords of every page, files whose
    frontmatter does not have the expected one-line JSON shape). Both fields
    are one-line JSON today; anything else is reported, never guessed at.
    (page-updates.split_frontmatter is not imported: page-updates loads this
    module, so that would be a cycle.)"""
    pseo_dir = pseo_dir or PSEO_DIR
    covered: set[str] = set()
    bad: list[str] = []
    for f in sorted(pseo_dir.glob("*.md")):
        m = _FM_RE.match(f.read_text(encoding="utf-8"))
        pm = sm = None
        if m:
            pm, sm = _PRIMARY_RE.search(m.group(1)), _SUPPORTING_RE.search(m.group(1))
        try:
            primary = json.loads(pm.group(1))
            supporting = json.loads(sm.group(1))
            assert isinstance(primary, str) and isinstance(supporting, list)
        except (AttributeError, ValueError, AssertionError):
            bad.append(f.name)
            continue
        covered.update(normalize(k) for k in [primary, *supporting])
    return covered, bad


# ---------------------------------------------------------------------------
# The weekly ceiling — read straight from the ledger this script itself owns
# ---------------------------------------------------------------------------

def iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def promoted_this_week(week: str) -> int:
    if not LEDGER.is_file():
        return 0
    n = 0
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("week") == week and row.get("event") == "promoted":
            n += 1
    return n


def log(entries: list[dict]) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="show, write nothing")
    a = ap.parse_args()

    today = date.today()
    week = iso_week(today)
    now = datetime.now(timezone.utc).isoformat()

    data = parse_candidates(CANDIDATES)  # explicit, not the default arg: tests/monkeypatch rely on this
    bank_rows = pg.parse_keyword_bank(BANK)
    known = existing_normalized(bank_rows)

    page_kw, bad_pages = page_keywords()
    seen_this_run: set[str] = set()
    eligible = []
    refused_covered: list[dict] = []
    refused_dead_bucket: list[dict] = []
    for c in data["rows"]:
        norm = normalize(c["query"])
        if norm in known or norm in seen_this_run:
            continue
        if c["impressions"] <= 0:                      # no confirmed impressions -> does not travel
            continue
        if bad_pages or norm in page_kw:
            refused_covered.append({**c, "reason": "page_keyword_unreadable" if bad_pages
                                    else "covered_by_page_keyword"})
            continue
        letter = classify_bucket(c["query"])
        if bucket_weight(letter) <= 0:
            refused_dead_bucket.append({**c, "bucket": letter})
            continue
        seen_this_run.add(norm)
        eligible.append(c)
    eligible.sort(key=lambda c: -c["impressions"])

    if refused_dead_bucket:
        print(f"[promote] refused (bucket_weight_zero): {len(refused_dead_bucket)}"
             + (" (dry run, not logged)" if a.dry_run else ""))
        for rc in refused_dead_bucket:
            print(f"  {rc['bucket']}  {rc['impressions']:>4} imp  {rc['query']}")
        if not a.dry_run:
            log([{"event": "refused", "reason": "bucket_weight_zero", "week": week, "date": now,
                 "query": rc["query"], "bucket": rc["bucket"], "impressions": rc["impressions"]}
                 for rc in refused_dead_bucket])

    if refused_covered:
        print(f"[promote] refused (covered_by_page_keyword): {len(refused_covered)}"
              + (" (dry run, not logged)" if a.dry_run else ""))
        for rc in refused_covered:
            print(f"  {rc['impressions']:>4} imp  {rc['query']}  [{rc['reason']}]")
        if not a.dry_run:
            log([{"event": "refused", "reason": rc["reason"], "week": week, "date": now,
                  "query": rc["query"], "impressions": rc["impressions"]}
                 for rc in refused_covered])

    already = promoted_this_week(week)
    room = max(0, WEEKLY_CAP - already)
    batch = eligible[:room]

    if not batch:
        reason = ("no candidate cleared dedup/impressions" if not eligible
                  else f"weekly cap reached ({already}/{WEEKLY_CAP} already promoted this week)")
        if not a.dry_run:
            log([{"event": "nothing_eligible", "week": week, "date": now, "reason": reason}])
        print(f"[promote] nothing_eligible — {reason}" + (" (dry run, not logged)" if a.dry_run else ""))
        return 0

    bank_text = BANK.read_text(encoding="utf-8")
    ledger_entries = []
    promoted = []
    for c in batch:
        letter = classify_bucket(c["query"])
        code_n = next_code(bank_text, letter)
        code = f"{letter}{code_n}"
        n_cols = last_row_column_count(bank_text, letter)
        row = render_row(code, c["query"], c["impressions"], c["clicks"],
                         data["fetched_at"] or today.isoformat(), n_cols, today.isoformat())
        bank_text = insert_row(bank_text, letter, row)
        promoted.append((code, c))
        ledger_entries.append({
            "event": "promoted", "week": week, "date": now,
            "query": c["query"], "bucket": letter, "code": code,
            "impressions": c["impressions"], "clicks": c["clicks"],
            "slug": c["slug"],
            "from": f"CANDIDATES.md (fetched {data['fetched_at']})",
            "instrument": "bing-webmaster",
        })

    if a.dry_run:
        print(f"[promote] DRY RUN — would promote {len(batch)} "
             f"(cap {WEEKLY_CAP}/wk, {already} already this week):")
        for code, c in promoted:
            print(f"  {code:>4}  {c['impressions']:>4} imp  {c['query']}")
        return 0

    BANK.write_text(bank_text, encoding="utf-8")
    log(ledger_entries)
    print(f"[promote] promoted {len(batch)} keyword(s) into {BANK}:")
    for code, c in promoted:
        print(f"  {code:>4}  {c['impressions']:>4} imp  {c['query']}")
    if len(eligible) > len(batch):
        print(f"[promote] {len(eligible) - len(batch)} more eligible, held by the "
             f"{WEEKLY_CAP}/week cap for next week")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
