#!/usr/bin/env python3
"""page-updates.py — Fable A3 channel 2 (FABLE_DEFYCARD_ANSWER.md, 2026-09-14):
strengthen an existing page instead of printing a near-duplicate new one.

WHY THIS EXISTS
---------------
A2.3 of the ruling: six keywords promoted 2026-09-01 turned out to be
near-duplicates of pages Bing already ranked for the same query — one of them
("crypto card cashback: top picks") cannibalised a converting page's own
impressions. A query where Bing already shows one of OUR pages is not demand
for a new URL; it is a task to make that page answer the query better. This
script is that second, lower-risk channel: it never mints a page, it only
inserts one short, fact-gated section into a page that already exists.

INPUT: the `## Update candidates` block of CANDIDATES.md
(scripts/bing-keywords.py --write), parsed via promote-candidates.py's
parse_update_candidates() — the same "two siblings" discipline as the bank
promotion (writer and reader must agree on the table shape, pinned by
scripts/tests/test_promote_candidates.py).

GATE: the exact same two literals faq-children.py enforces for a NEW page
(judge(): 8-gram shingle overlap with the PAGE'S OWN existing body <= 25%,
>= 5 new facts) — a section that just restates what is already on the page is
refused, never padded to length (Y58's rule).

WHAT THIS NEVER DOES
---------------------
* Never creates a URL. Only appends markdown inside an existing .md file.
* Never touches a FROZEN_SLUGS page (pseo-generate.py) — those are measured
  converters; Fable's A2.3 table shows one is a frozen page (uphold-card-review).
* Never writes above WEEKLY_CAP applications, tracked in its own ledger under
  fcntl.flock — same pattern as pseo-generate.py's `_faq_ledger_txn`.
* Never bumps `generated_at` unless a section was actually inserted.

Usage:
    page-updates.py                # apply update candidates, write pages + ledger
    page-updates.py --dry-run      # show what would change, write nothing
    page-updates.py --verdict      # +14/+28 GetQueryPageStats readback for past updates
    page-updates.py --verdict --dry-run   # compute verdicts, print only
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from flags import pseo_enabled  # noqa: E402 -- LAW #ONE-PLACE kill-switch
import money_gate  # noqa: E402 -- G36: a number in money copy must come from a fact
import humanize_gate  # noqa: E402 -- G37: copy-edit AI tells before G36 sees the text
import telegram_lib  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "logs" / "pseo" / "page-updates.jsonl"
ETHERFI_FACTS = ROOT / ".claude" / "skills" / "etherfi" / "SKILL.md"
FACTS_BYTE_LIMIT = 7000    # Y69: pseo-generate.py's own writer sees only SKILL.md[:7000]; match it

WEEKLY_CAP = 5


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod   # pseo-generate.py's @dataclass needs this pre-registered
    spec.loader.exec_module(mod)
    return mod


pg = _load("pseo_generate", "scripts/pseo-generate.py")
pc = _load("promote_candidates", "scripts/promote-candidates.py")
fc = _load("faq_children", "scripts/faq-children.py")
bk = _load("bing_keywords", "scripts/bing-keywords.py")


def iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def slug_for_path(path: str) -> str:
    return path.strip("/").split("/")[-1]


def _ledger_append(entries: list[dict]) -> None:
    if not entries:
        return
    import fcntl
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _ledger_rows() -> list[dict]:
    if not LEDGER.is_file():
        return []
    out = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def applied_this_week(week: str) -> int:
    """Counts this script's own `applied` rows AND the `queued` rows E-18's
    `intel-route.py` writes into this SAME ledger for its own update
    candidates. D4 states the cap as "5/нед общий с Bing" — one counter
    reading one file is what makes it actually shared, instead of two
    independent counters that can drift apart (the schedule/cadence "brother
    of slugify" precedent, .claude/CLAUDE.md)."""
    return sum(1 for r in _ledger_rows()
              if r.get("week") == week and r.get("event") in ("applied", "queued"))


# ---------------------------------------------------------------------------
# The page: split, insert, bump
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^(---\n.*?\n---\n)(.*)$", re.S)
_GENERATED_AT_RE = re.compile(r'^generated_at:\s*"[^"]*"\s*$', re.M)
_FAQ_HEADING_RE = re.compile(
    r"^## (?:FAQ|Frequently Asked Questions|Risk & Disclosure)\s*$", re.M | re.I)


def split_frontmatter(text: str) -> tuple[str, str]:
    """-> (frontmatter INCLUDING both '---' delimiters, body). Raises
    ValueError if the file is not an Astro content-collection document —
    refuse loudly rather than guess at a shape."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError("not a frontmatter document")
    return m.group(1), m.group(2)


def bump_generated_at(frontmatter: str, when: datetime) -> str:
    if not _GENERATED_AT_RE.search(frontmatter):
        return frontmatter
    return _GENERATED_AT_RE.sub(f'generated_at: "{when.isoformat()}"', frontmatter)


def insert_section(body: str, section: str) -> str:
    """Before the first FAQ / Risk & Disclosure H2 if one exists, else at the end."""
    m = _FAQ_HEADING_RE.search(body)
    block = section.strip() + "\n\n---\n\n"
    if m:
        return body[:m.start()] + block + body[m.start():]
    return body.rstrip() + "\n\n---\n\n" + section.strip() + "\n"


# ---------------------------------------------------------------------------
# The writer call — reuses pseo-generate.py's call_claude_cli (same Haiku
# cost gate, same CLI transport). This is NOT a new page: no JSON schema, no
# image pipeline, no frontmatter — one markdown section, gated afterward by
# the same two literals a brand-new FAQ child is judged against.
# ---------------------------------------------------------------------------

def load_facts() -> str:
    if not ETHERFI_FACTS.is_file():
        return ""
    return ETHERFI_FACTS.read_text(encoding="utf-8")[:FACTS_BYTE_LIMIT]


def build_prompt(query: str, page_body: str, facts: str) -> tuple[str, str]:
    system = (
        "You are the DefyCard pSEO content writer, in STRENGTHEN mode. You do "
        "not write a new page. You write ONE short markdown section (120-220 "
        "words: one '### <heading>' line plus 2-4 short paragraphs or a bullet "
        "list) that directly answers a real search query, for a page that "
        "already exists. Use ONLY facts drawn from the reference sheet below "
        "and the page's own existing content — never invent a number, price, "
        "country or date. Never restate a sentence already on the page "
        "verbatim. Output ONLY the markdown section: no JSON, no code fences, "
        "no front matter, no commentary before or after it.\n"
        + humanize_gate.voice_block() + "\n"
        f"## Reference facts (ether.fi Cash, authoritative)\n{facts}\n"
    )
    user = (
        f"Search query this page must directly answer: {query!r}\n\n"
        f"--- existing page content ---\n{page_body}\n--- end existing page content ---\n"
    )
    return system, user


def write_update_section(query: str, page_body: str) -> str:
    system, user = build_prompt(query, page_body, load_facts())
    raw, _usage, _cost = pg.call_claude_cli(system_prompt=system, user_message=user)
    return pg._strip_code_fences(raw).strip()


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def apply_updates(dry_run: bool, writer=write_update_section,
                  now: datetime | None = None, humanizer=humanize_gate.humanize) -> dict[str, Any]:
    """`writer` and `humanizer` are injectable so tests never call the real Claude CLI."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    week = iso_week(today)

    candidates = pc.parse_update_candidates()
    already = applied_this_week(week)
    room = max(0, WEEKLY_CAP - already)

    entries: list[dict] = []
    applied_count = 0
    done_pairs = {(r.get("slug"), r.get("query")) for r in _ledger_rows()
                  if r.get("event") == "applied"}
    for c in candidates:
        if applied_count >= room:
            break
        slug = slug_for_path(c["path"])
        # Law (KW-01): a (slug, query) pair applied once is never applied again.
        if (slug, c["query"]) in done_pairs:
            entries.append({"event": "refused", "reason": "already_applied", "week": week,
                            "date": now.isoformat(), "query": c["query"], "slug": slug})
            continue
        if slug in pg.FROZEN_SLUGS:
            entries.append({"event": "refused", "reason": "frozen", "week": week,
                            "date": now.isoformat(), "query": c["query"], "slug": slug})
            continue
        f = pg.CONTENT_DIR / f"{slug}.md"
        if not f.is_file():
            entries.append({"event": "refused", "reason": "page_not_found", "week": week,
                            "date": now.isoformat(), "query": c["query"], "slug": slug})
            continue
        text = f.read_text(encoding="utf-8")
        try:
            frontmatter, body = split_frontmatter(text)
        except ValueError:
            entries.append({"event": "refused", "reason": "not_frontmatter_doc", "week": week,
                            "date": now.isoformat(), "query": c["query"], "slug": slug})
            continue

        section = writer(c["query"], body)
        # G37 (CCH-HUM-05): copy-edit flagged sentences BEFORE G36, so G36 judges
        # the text that will actually be written. The write below uses `section`
        # AFTER this rebind -- never the writer's raw output.
        hres = humanizer({"body_markdown": section})
        if hres.refused_reason:
            entries.append({"event": "refused", "reason": hres.refused_reason, "gate": "G37",
                            "week": week, "date": now.isoformat(), "query": c["query"], "slug": slug})
            if not dry_run:
                telegram_lib.alert(f"refused: {hres.refused_reason} — {slug} not updated (G37)")
            continue
        section = hres.payload["body_markdown"]
        # G36 (E-11), before the write: a number the reader will see must come
        # from a verified fact, from rates/availability — or from THIS page,
        # which is the branch that exists for exactly this channel (we are
        # strengthening a page, not minting one, so its own body is a source).
        unbacked = money_gate.unbacked(section, parent_text=text)
        if unbacked:
            reasons = money_gate.reasons(unbacked)
            entries.append({"event": "refused", "reason": reasons[0], "gate": "G36",
                            "reasons": reasons, "week": week,
                            "date": now.isoformat(), "query": c["query"], "slug": slug})
            if not dry_run:
                telegram_lib.alert(
                    f"refused: {reasons[0]} — {slug} not updated "
                    f"({len(unbacked)} unbacked token(s), G36)")
            continue

        verdict = fc.judge(section, body)
        if not verdict["passed"]:
            entries.append({"event": "refused", "reason": "delta_gate", "week": week,
                            "date": now.isoformat(), "query": c["query"], "slug": slug,
                            "verdict": verdict})
            continue

        if not dry_run:
            new_body = insert_section(body, section)
            new_frontmatter = bump_generated_at(frontmatter, now)
            f.write_text(new_frontmatter + new_body, encoding="utf-8")

        applied_count += 1
        entries.append({
            "event": "applied", "week": week, "date": now.isoformat(),
            "query": c["query"], "slug": slug,
            "bing_before": {"impressions": c["page_impressions"],
                            "clicks": c["page_clicks"], "position": c["position"]},
            "humanize": {"flags_before": hres.flags_before, "flags_after": hres.flags_after,
                         "rewritten": hres.rewritten},
        })

    if not dry_run:
        _ledger_append(entries)
    return {"applied": applied_count, "entries": entries, "room": room}


# ---------------------------------------------------------------------------
# Verdict: +14 / +28 readback
# ---------------------------------------------------------------------------

def _already_judged(rows: list[dict], query: str, applied_date: str, window: int) -> bool:
    return any(r.get("event") == "verdict" and r.get("query") == query
              and r.get("applied_date") == applied_date and r.get("window") == window
              for r in rows)


def compute_verdicts(today: date | None = None, page_stats=None) -> list[dict]:
    """page_stats is injectable (defaults to bk.query_page_stats) so tests
    never hit the live Bing API."""
    today = today or date.today()
    page_stats = page_stats or bk.query_page_stats
    rows = _ledger_rows()
    out: list[dict] = []
    for row in rows:
        if row.get("event") != "applied":
            continue
        applied_date = row["date"][:10]
        age = (today - date.fromisoformat(applied_date)).days
        window = 28 if age >= 28 else (14 if age >= 14 else None)
        if window is None or _already_judged(rows, row["query"], applied_date, window):
            continue
        ranked = page_stats(row["query"])
        expected_path = f"/{row['slug']}/"
        after = next((p for p in ranked if p["path"].strip("/") == row["slug"]
                     or p["path"] == expected_path), None)
        out.append({
            "event": "verdict", "window": window, "query": row["query"], "slug": row["slug"],
            "applied_date": applied_date, "date": datetime.now(timezone.utc).isoformat(),
            "pos_before": row["bing_before"]["position"], "clicks_before": row["bing_before"]["clicks"],
            "pos_after": after["position"] if after else None,
            "clicks_after": after["clicks"] if after else None,
        })
    return out


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="show, write nothing")
    ap.add_argument("--verdict", action="store_true", help="+14/+28 readback instead of applying")
    a = ap.parse_args()

    # E-02: checked before EVERYTHING else, --dry-run included -- see the
    # same check in pseo-generate.py's _main() for why.
    if not pseo_enabled():
        print(json.dumps({"event": "skip", "reason": "pseo_disabled"}))
        print("[page-updates] skip: pseo_disabled (PSEO_ENABLED=false)")
        return 0

    if a.verdict:
        verdicts = compute_verdicts()
        if not verdicts:
            print("[page-updates] no update is due for a +14/+28 reading today")
            return 0
        for v in verdicts:
            print(f"  {v['window']:>2}d  {v['query']}  pos {v['pos_before']} -> {v['pos_after']}")
        if not a.dry_run:
            _ledger_append(verdicts)
        return 0

    result = apply_updates(dry_run=a.dry_run)
    print(f"[page-updates] applied {result['applied']} (room {result['room']}/wk)")
    for e in result["entries"]:
        if e["event"] == "applied":
            print(f"  applied  {e['slug']}  <- {e['query']}")
        else:
            print(f"  refused  {e.get('slug', '?')}  {e['reason']}  <- {e['query']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
