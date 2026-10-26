#!/usr/bin/env python3
"""intel-route.py — route ONE accepted intel event to its outputs (D4).

Spec: FABLE_DEFYCARD_PART2.md section D4 / E-07c. `intel-ingest.py --accept
<event_id>` is the human decision; this file is everything that follows from
it, and nothing here ever decides on its own.

WHAT AN ACCEPTED EVENT PRODUCES (D4's table, in order)
------------------------------------------------------
  pages_affected >= 1 -> a LITERAL table edit of the affected pages
                          (`corpus_edit.apply_table`, `--expect N`, refuse at
                          |changed - N| > 10 %) + changelog + link to it
  score >= 8          -> + an update candidate for the most-fetched affected
                          page
  score >= 12         -> + an FAQ question candidate
  score >= 12 and class in {availability, rate, affiliate}
                      -> + an H1 video topic candidate
  a `disputed` fact   -> NOTHING. Refusal, no edits, `CONFLICT:` on stdout.

THREE THINGS THIS DELIBERATELY DOES NOT DO
------------------------------------------
1. **It never regenerates a page.** Not even a page that isn't frozen. The
   only write to the corpus is a literal-for-literal replacement, which is why
   a FROZEN_SLUGS page (pseo-generate.py) is edited here exactly like any
   other page: the Y69 precedent is "frozen pages are CORRECTED by table, never
   rewritten", and excluding them from a money-copy correction would leave the
   20 measured converters as the only pages still stating the dead figure.
2. **It never writes `.env` or crontab.** Plan rule, sandbox.
3. **It never mints a URL.** The changelog is one file under `logs/`; E-16
   turns it into the single `/learn/ether-fi-cash-changelog/` page.

WHY THE TABLE IS FILTERED BEFORE `corpus_edit` SEES IT
-------------------------------------------------------
`corpus_edit.apply_table` refuses when a table entry matches nothing, because
its tables are HAND-WRITTEN and a literal that matches nothing there is a
typo. This table is DERIVED from an event's `extracted[]`, where a fact that
simply isn't quoted anywhere in the corpus is the normal case, not an error.
So entries that match no page are dropped here, counted, and reported as
`unmatched` — the ceiling still applies to everything that survives.

WHY A SEPARATE `logs/intel/changelog.jsonl`
--------------------------------------------
`events.jsonl` is append-only, so a decision is a SECOND record for the same
`event_id`; "the accepted events" is therefore already a derived projection of
that stream. The router holds every field E-16's page needs (date, fact,
old -> new, quote, source link, verified date, which pages changed) at the
moment it decides, so it writes them down once instead of making the page
builder re-derive them later. That is this project's own law from the
`slugify()` fortnight: never recompute downstream what the producer already
knew.

CLI:
    intel-route.py --event <event_id>            # dry-run: measure, write nothing
    intel-route.py --event <event_id> --write    # apply
    intel-route.py --event <event_id> --expect 7 --write
    intel-route.py --event <event_id> --content <copy> --write   # rehearsal

A rehearsal (`--content` pointing anywhere but the live corpus) keeps its own
ledgers beside that copy — see `ledger_paths`.
"""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import corpus_edit as ce  # noqa: E402
import facts_lib as fl  # noqa: E402
from slug_lib import slugify  # noqa: E402 -- LAW #ONE-PLACE canonical slugify

CONTENT = ROOT / "seo-site" / "src" / "content" / "pseo"
EVENTS_PATH = ROOT / "logs" / "intel" / "events.jsonl"
CHANGELOG_PATH = ROOT / "logs" / "intel" / "changelog.jsonl"
CANDIDATES_PATH = ROOT / "logs" / "intel" / "candidates.jsonl"
AEO_DIR = ROOT / "logs" / "aeo"

# E-18: the three ledgers a delivered candidate actually lands in — the
# pipelines that already read them (page-updates.py, faq-children.py's
# open_next_cohort pool, and the not-yet-built E-20 H1 video picker).
PAGE_UPDATES_PATH = ROOT / "logs" / "pseo" / "page-updates.jsonl"
FAQ_COHORTS_PATH = ROOT / "logs" / "pseo" / "faq-cohorts.json"
H1_QUEUE_PATH = ROOT / "video-generator" / "state" / "h1_queue.json"
UPDATE_SHARED_CAP = 5   # D4: "5/нед общий с Bing" -- page-updates.py's own WEEKLY_CAP

# --- D4 thresholds and caps, as literals -----------------------------------
THRESH_UPDATE = 8
THRESH_FAQ = 12
VIDEO_CLASSES = frozenset({"availability", "rate", "affiliate"})
# (cap, period key) — the period is what `_period_key` stamps on each row.
CAPS: dict[str, tuple[int, str]] = {
    "update": (5, "week"),   # D4: "5/нед общий с Bing"
    "faq": (3, "day"),       # D4: "1–3/день общий"
    "video": (3, "week"),    # D4: "3/нед"
}
# D4's own words: "потолок = N ± 10 %". Floor, not round: with N = 7 that is a
# tolerance of 0, i.e. exactly the 7 pages the measurement found. A looser
# reading would let a table that hits 9 pages pass a `--expect 7`.
CEILING_FRACTION = 0.1


# =============================================================================
# ledgers
# =============================================================================

def _append_jsonl(path: Path, rows: list[dict]) -> int:
    """Append under flock — the same discipline pseo-generate.py's FAQ ledger
    needed after a hand edit raced the live cron."""
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return len(rows)


def _read_jsonl(path: Path) -> list[dict]:
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


def ledger_paths(content_dir: Path) -> tuple[Path, Path]:
    """(changelog, candidates) for a run over `content_dir`.

    A rehearsal against a COPY of the corpus (`--content <tmp>`) gets its own
    pair beside that copy. The real `changelog.jsonl` is the source of a
    published page (E-16) and the real `candidates.jsonl` is what the weekly
    caps are counted from — a rehearsal appending to either publishes a change
    nobody accepted and silently spends the week's allowance."""
    if content_dir.resolve() == CONTENT.resolve():
        return CHANGELOG_PATH, CANDIDATES_PATH
    rehearsal = content_dir.parent / f"{content_dir.name}-intel-ledgers"
    return rehearsal / "changelog.jsonl", rehearsal / "candidates.jsonl"


def delivery_paths(content_dir: Path) -> dict[str, Path]:
    """Where a DELIVERED candidate's target ledger lives — same production /
    rehearsal branch as `ledger_paths`, for the same reason: a `--content`
    rehearsal must not spend the real page-updates cap, add a hypothesis to
    the real FAQ cohort, or queue a real H1 video topic."""
    if content_dir.resolve() == CONTENT.resolve():
        return {"update": PAGE_UPDATES_PATH, "faq": FAQ_COHORTS_PATH, "video": H1_QUEUE_PATH}
    rehearsal = content_dir.parent / f"{content_dir.name}-intel-ledgers"
    return {"update": rehearsal / "page-updates.jsonl",
            "faq": rehearsal / "faq-cohorts.json",
            "video": rehearsal / "h1_queue.json"}


def _period_key(now: datetime, period: str) -> str:
    return now.strftime("%G-W%V") if period == "week" else now.strftime("%Y-%m-%d")


# =============================================================================
# the literal table, derived from the event
# =============================================================================

def disputed_fact_ids(event: dict, facts: list[dict]) -> list[str]:
    """Fact ids this event touches whose record is `disputed`.

    D4: a disputed fact routes to nothing at all. Two issuer surfaces
    disagreeing is precisely the state in which rewriting 300 pages to one of
    the two readings is the money bug, not the fix — a human resolves the
    dispute in `config/facts/etherfi.yaml` first."""
    by_id = {f["id"]: f for f in facts}
    return [
        fid for fid in (item.get("fact_id") for item in event.get("extracted") or [])
        if (by_id.get(fid) or {}).get("status") == "disputed"
    ]


def build_table(event: dict, facts: list[dict]) -> tuple[list[tuple[str, str]], list[dict]]:
    """`old_literal -> new_literal` for every extracted item that names a fact
    we actually track and states a real change. Returns (table, skipped)."""
    known = {f["id"] for f in facts}
    table: list[tuple[str, str]] = []
    skipped: list[dict] = []
    for item in event.get("extracted") or []:
        fid = item.get("fact_id")
        old, new = item.get("old"), item.get("new")
        if fid not in known:
            # D4's "class: marketing без факта ⇒ digest-запись, ничего больше":
            # a reading we have no fact record for gets written down, never
            # applied to published copy.
            skipped.append({"fact_id": fid, "reason": "no_such_fact"})
        elif not old or not new or str(old) == str(new):
            skipped.append({"fact_id": fid, "reason": "no_literal_change"})
        else:
            table.append((str(old), str(new)))
    return table, skipped


def _corpus_texts(content_dir: Path) -> dict[Path, str]:
    return {f: f.read_text(encoding="utf-8") for f in sorted(content_dir.glob("*.md"))}


def _old_forms(old: str, new: str) -> list[str]:
    """Both serializations of the literal being searched for — the prose form
    and the JSON-escaped frontmatter form. Same helper the applier uses, so
    the count and the edit can never disagree about what "present" means."""
    return [o for o, _ in ce.serializations(old, new)]


def measure(texts: dict[Path, str], table: list[tuple[str, str]]
            ) -> tuple[list[Path], set[str]]:
    """-> (files carrying at least one of the literals, literals that matched
    at least one file)."""
    affected: list[Path] = []
    matched: set[str] = set()
    for path, text in texts.items():
        hit = False
        for old, new in table:
            if any(form in text for form in _old_forms(old, new)):
                matched.add(old)
                hit = True
        if hit:
            affected.append(path)
    return affected, matched


# =============================================================================
# E-18: the "Changed on <date> ... see changelog" line on affected pages
# =============================================================================

def _changelog_link(content_dir: Path) -> Optional[str]:
    """The same guard `market-digest.py`'s `_changelog_link()` uses: a link to
    E-16's page ONLY if it actually exists on disk. An empty event ledger
    means no changelog page is published yet, and a `[text](url)` to a URL
    that doesn't exist is a dead internal link (G20) — so the mention stays
    plain text until the page is real."""
    spec = importlib.util.spec_from_file_location(
        "build_changelog_for_route", ROOT / "scripts" / "build-changelog.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not (content_dir / f"{mod.SLUG}.md").is_file():
        return None
    return f"/{mod.BUCKET}/{mod.SLUG}/"


def _fact_labels(event: dict, facts: list[dict], matched: set[str]) -> list[str]:
    """One human-readable label per extracted fact that the table edit
    actually applied to the corpus (`matched`) — a fact quoted in the event
    but found nowhere in any page has nothing to announce on a page.

    The label names WHAT changed (its class title), never the old value: the
    stale literal that was just scrubbed off this exact page (Y64 — a dead
    figure lingering in prose) would otherwise walk right back onto it inside
    the very sentence announcing the correction. The old -> new detail lives
    on E-16's changelog page, which this line links to."""
    by_id = {f["id"]: f for f in facts}
    seen, out = set(), []
    for item in event.get("extracted") or []:
        if str(item.get("old")) not in matched:
            continue
        fact = by_id.get(item.get("fact_id")) or {}
        label = fl.CLASS_TITLE.get(fact.get("class"), item.get("fact_id"))
        if label not in seen:
            seen.add(label)
            out.append(label)
    return out


def _watch_line(now: datetime, fact_labels: list[str], link: Optional[str]) -> str:
    ref = f"[changelog]({link})" if link else "changelog"
    return f"Changed on {now.date().isoformat()}: {'; '.join(fact_labels)} — see {ref}"


def apply_watch_lines(files: list[Path], line: str, write: bool) -> int:
    """Insert `line` into every affected page's `what_to_watch` array.
    Returns how many pages actually changed (already carrying the exact line
    is a no-op, not a duplicate)."""
    added = 0
    for f in files:
        text = f.read_text(encoding="utf-8")
        new_text, changed = ce.append_what_to_watch(text, line)
        if changed:
            added += 1
            if write:
                f.write_text(new_text, encoding="utf-8")
    return added


# =============================================================================
# candidates (D4's thresholds; caps counted from this router's own ledger)
# =============================================================================

def _verified_by_page() -> dict[str, int]:
    files = sorted(AEO_DIR.glob("*-W*.json"))
    if not files:
        return {}
    try:
        return json.loads(files[-1].read_text(encoding="utf-8")).get("verified_by_page") or {}
    except (OSError, json.JSONDecodeError):
        return {}


def hottest_page(paths: list[str]) -> Optional[str]:
    """The most-fetched affected page (D4: the update candidate goes to "самой
    фетчимой затронутой страницы"). With no fetch data at all every page ties
    at 0 and the sort falls back to the path itself, so the choice stays
    deterministic instead of depending on directory order."""
    if not paths:
        return None
    by_page = _verified_by_page()
    return sorted(paths, key=lambda p: (-by_page.get(p, 0), p))[0]


def candidate_kinds(score: float, fact_class: Optional[str]) -> list[str]:
    kinds = []
    if score >= THRESH_UPDATE:
        kinds.append("update")
    if score >= THRESH_FAQ:
        kinds.append("faq")
        if fact_class in VIDEO_CLASSES:
            kinds.append("video")
    return kinds


def _emit_candidates(event: dict, fact_class: Optional[str], paths: list[str],
                     now: datetime, *, write: bool,
                     candidates_path: Optional[Path] = None) -> list[dict]:
    """One row per candidate kind that both clears its D4 threshold and has
    room left under its cap. Delivery into the consuming ledgers
    (page-updates, the FAQ cohort pool, h1_queue) is E-18's step; what is
    decided here — which kinds fire, and the cap that stops the fourth video
    topic of the week — is decided ONCE, here, and recorded."""
    candidates_path = candidates_path or CANDIDATES_PATH
    target = hottest_page(paths)
    rows: list[dict] = []
    existing = _read_jsonl(candidates_path)
    for kind in candidate_kinds(float(event.get("score") or 0), fact_class):
        cap, period = CAPS[kind]
        key = _period_key(now, period)
        already = sum(1 for r in existing
                      if r.get("kind") == kind and r.get("period_key") == key)
        row = {
            "ts": now.isoformat(timespec="seconds"),
            "kind": kind,
            "period": period,
            "period_key": key,
            "event_id": event.get("event_id"),
            "score": event.get("score"),
            "fact_class": fact_class,
            "target_path": target,
            "capped": already >= cap,
        }
        if row["capped"]:
            # Recorded, not silent: a candidate dropped by a cap is a decision
            # someone may have to explain later (Y55 — the zero carries its
            # cause). It is written with capped=true and consumed by nobody.
            row["reason"] = f"cap {cap}/{period} already reached for {key}"
        else:
            existing.append(row)
        rows.append(row)
    if write:
        _append_jsonl(candidates_path, rows)
    return rows


# =============================================================================
# E-18: delivery — turn a non-capped D4 candidate into a row in the ledger
# the CONSUMING pipeline actually reads, not a private copy of the decision.
# =============================================================================

def slug_for_path(path: Optional[str]) -> Optional[str]:
    return path.strip("/").split("/")[-1] if path else None


def _intel_question(event: dict, facts: list[dict]) -> str:
    by_id = {f["id"]: f for f in facts}
    item = (event.get("extracted") or [{}])[0]
    fact = by_id.get(item.get("fact_id")) or {}
    label = fl.CLASS_TITLE.get(fact.get("class"), item.get("fact_id") or "this")
    return f"What is ether.fi Cash's {label.lower()} now?"


def _deliver_update(path: Path, row: dict, event: dict, now: datetime,
                    *, write: bool) -> tuple[bool, Optional[str]]:
    """One `queued` row in the SAME ledger `page-updates.py`'s own
    `applied_this_week()` reads — the D4 cap ("5/нед общий с Bing") is real
    only because both scripts count rows in one file, never two counters."""
    week = _period_key(now, "week")
    usage = sum(1 for r in _read_jsonl(path)
               if r.get("week") == week and r.get("event") in ("applied", "queued"))
    if usage >= UPDATE_SHARED_CAP:
        return False, f"page-updates cap {UPDATE_SHARED_CAP}/week reached (shared with Bing)"
    entry = {
        "event": "queued", "source": "intel", "week": week,
        "date": now.isoformat(), "event_id": event.get("event_id"),
        "fact_class": row.get("fact_class"), "slug": slug_for_path(row.get("target_path")),
        "path": row.get("target_path"),
    }
    if write:
        _append_jsonl(path, [entry])
    return True, None


def _deliver_faq(path: Path, row: dict, event: dict, facts: list[dict],
                 content_dir: Path, *, write: bool) -> tuple[bool, Optional[str]]:
    """Append one hypothesis item, shaped exactly like `open_next_cohort`'s
    own items (question/parent/parent_fetches/slug), onto the CURRENTLY OPEN
    cohort. Never opens a new cohort — that decision belongs to
    `open_next_cohort`'s own tap (faq-verdict.py's `tap_is_open`), which this
    delivery step has no business overriding."""
    led = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"cohorts": []}
    cohorts = led.get("cohorts") or []
    if not cohorts:
        return False, "no_open_cohort"
    items = cohorts[-1]["items"]
    question = _intel_question(event, facts)
    slug = slugify(question)
    existing = {i.get("slug") for i in items} | {p.stem for p in content_dir.glob("*.md")}
    if not slug or slug in existing:
        return False, "already_a_page_or_cohort_item"
    items.append({
        "question": question, "parent": row.get("target_path"),
        "parent_fetches": _verified_by_page().get(row.get("target_path"), 0),
        "slug": slug, "source": "intel", "event_id": event.get("event_id"),
    })
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(led, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return True, None


def _deliver_video(path: Path, row: dict, event: dict, now: datetime,
                   *, write: bool) -> tuple[bool, Optional[str]]:
    """Append one topic onto `video-generator/state/h1_queue.json` (E-20's
    future input; that step is not built yet, so this only queues)."""
    entries = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    if not isinstance(entries, list):
        return False, "h1_queue.json is not a list"
    entry = {
        "event_id": event.get("event_id"), "target_path": row.get("target_path"),
        "target_pseo_slug": slug_for_path(row.get("target_path")),
        "fact_class": row.get("fact_class"), "score": row.get("score"),
        "week": _period_key(now, "week"), "added_at": now.isoformat(), "source": "intel",
    }
    entries.append(entry)
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return True, None


def deliver_candidates(rows: list[dict], event: dict, facts: list[dict], now: datetime,
                       *, write: bool, content_dir: Path) -> list[dict]:
    """Every row `_emit_candidates` did not cap gets delivered into the real
    ledger its consuming pipeline reads; every row is marked `delivered` and,
    when it wasn't, `delivery_reason` (Y55: a zero carries its cause) —
    in-memory only, `candidates.jsonl` itself keeps the schema `_emit_candidates`
    already writes and the existing tests already pin."""
    paths = delivery_paths(content_dir)
    for row in rows:
        if row["capped"]:
            row["delivered"], row["delivery_reason"] = False, "capped"
            continue
        kind = row["kind"]
        if kind == "update":
            delivered, reason = _deliver_update(paths["update"], row, event, now, write=write)
        elif kind == "faq":
            delivered, reason = _deliver_faq(paths["faq"], row, event, facts,
                                             content_dir, write=write)
        elif kind == "video":
            delivered, reason = _deliver_video(paths["video"], row, event, now, write=write)
        else:
            delivered, reason = False, f"unknown kind {kind!r}"
        row["delivered"] = delivered
        if not delivered:
            row["delivery_reason"] = reason
    return rows


# =============================================================================
# routing
# =============================================================================

def _changelog_rows(event: dict, facts: list[dict], now: datetime,
                    pages_affected: int, changed: list[str]) -> list[dict]:
    by_id = {f["id"]: f for f in facts}
    rows = []
    for item in event.get("extracted") or []:
        fact = by_id.get(item.get("fact_id")) or {}
        rows.append({
            "date": now.date().isoformat(),
            "event_id": event.get("event_id"),
            "fact_id": item.get("fact_id"),
            "fact_class": fact.get("class"),
            "old": item.get("old"),
            "new": item.get("new"),
            "quote": item.get("quote"),
            "source_url": event.get("url"),
            "verified_at": now.date().isoformat(),
            "pages_affected": pages_affected,
            "pages": changed,
        })
    return rows


def route(event: dict, facts: list[dict], *, content_dir: Optional[Path] = None,
          expect: Optional[int] = None, write: bool = False,
          now: Optional[datetime] = None) -> dict:
    """Route one event. Nothing is written unless `write` is true, and nothing
    at all is written if any step refuses — `intel-ingest.py --accept` runs
    this once with `write=False` as a pre-flight for exactly that reason, so a
    ceiling refusal cannot leave `facts.yaml` saying one thing while the
    corpus still says the other."""
    now = now or datetime.now(timezone.utc)
    content_dir = content_dir or CONTENT
    changelog_path, candidates_path = ledger_paths(content_dir)
    out: dict[str, Any] = {
        "event_id": event.get("event_id"), "refused": None, "pages_affected": 0,
        "files": [], "applied": 0, "generated_bumped": 0, "changelog": 0,
        "changelog_path": str(changelog_path), "watch_lines": 0,
        "candidates": [], "skipped": [], "unmatched": [], "conflicts": [],
    }

    if event.get("decision") != "accepted":
        out["refused"] = f"not_accepted (decision={event.get('decision')!r})"
        return out

    disputed = disputed_fact_ids(event, facts)
    if disputed:
        out["refused"] = f"disputed: {disputed}"
        by_id = {f["id"]: f for f in facts}
        out["conflicts"] = [
            f"CONFLICT: {fid}: on file {by_id[fid].get('value')!r} — "
            f"resolve in config/facts/etherfi.yaml before accepting"
            for fid in disputed]
        return out

    table, skipped = build_table(event, facts)
    out["skipped"] = skipped

    texts = _corpus_texts(content_dir)
    affected, matched = measure(texts, table)
    out["pages_affected"] = len(affected)
    out["unmatched"] = [old for old, _ in table if old not in matched]
    live_table = [(old, new) for old, new in table if old in matched]

    if live_table:
        expect_n = len(affected) if expect is None else expect
        stat = ce.apply_table(
            sorted(texts), live_table, write=write, expect=expect_n,
            tolerance=int(expect_n * CEILING_FRACTION), bump_generated=True)
        if stat["refused"]:
            out["refused"] = stat["refused"]
            return out
        out["files"] = stat["changed"]
        out["applied"] = stat["applied"]
        out["generated_bumped"] = stat["generated_bumped"]

        # E-18: "Changed on <date>: <fact> -- see changelog" on every page the
        # table edit actually touched. Base tier (D4 row 1) -- fires whenever
        # pages_affected >= 1, independent of the score thresholds below.
        fact_labels = _fact_labels(event, facts, matched)
        if fact_labels:
            line = _watch_line(now, fact_labels, _changelog_link(content_dir))
            changed_files = [content_dir / f"{stem}.md" for stem in out["files"]]
            out["watch_lines"] = apply_watch_lines(changed_files, line, write)

    fact_class = next(
        (f["class"] for f in facts
         for item in event.get("extracted") or []
         if f["id"] == item.get("fact_id")), None)
    paths = [p for p in (ce.page_path_from_text(texts[f], f.stem) for f in affected) if p]

    rows = _changelog_rows(event, facts, now, out["pages_affected"], out["files"])
    out["changelog"] = _append_jsonl(changelog_path, rows) if write else len(rows)
    candidates = _emit_candidates(event, fact_class, paths, now, write=write,
                                  candidates_path=candidates_path)
    out["candidates"] = deliver_candidates(candidates, event, facts, now,
                                           write=write, content_dir=content_dir)
    return out


def load_event(event_id: str, events_path: Optional[Path] = None) -> Optional[dict]:
    """The LAST record carrying this id — a decision is appended as a new
    record, so the latest one is the current state of the event.

    `events_path` resolves at CALL time, not at def time: a default argument
    is evaluated once at import, so `EVENTS_PATH = <somewhere else>` (a
    rehearsal, a test) would silently keep reading the real ledger."""
    found = None
    for row in _read_jsonl(events_path or EVENTS_PATH):
        if row.get("event_id") == event_id:
            found = row
    return found


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True)
    ap.add_argument("--expect", type=int, default=None,
                    help="how many pages this event is supposed to change "
                         "(default: the measured count)")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--content", type=Path, default=CONTENT)
    args = ap.parse_args(argv)

    event = load_event(args.event)
    if event is None:
        print(json.dumps({"event": "refused", "reason": "no_such_event",
                          "event_id": args.event}))
        return 1

    result = route(event, fl.load(), content_dir=args.content,
                   expect=args.expect, write=args.write)
    for line in result["conflicts"]:
        print(line)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["refused"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
