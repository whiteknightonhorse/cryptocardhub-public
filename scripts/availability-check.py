#!/usr/bin/env python3
"""availability-check.py — AEO-1 reconciliation. Finds disagreements. Fixes nothing.

THE RULE THIS FILE OBEYS
A statement that "ether.fi Cash is not available in India" is a factual claim
about a financial product. A machine may notice that the issuer's page no
longer agrees with ours. A machine may NOT decide what the new truth is and
write it into config/availability.yaml. So:

    lists match       -> bump `verified` to today. The date now means what it
                         says: a check happened and it passed.
    lists differ      -> write an alert, change NOTHING. The weekly report
                         carries the diff; a human edits the YAML.
    source unreachable
    or unparseable    -> write an alert, change NOTHING, do NOT bump.

The third case matters as much as the second. A checker that silently treats
"I could not read the page" as "nothing changed" manufactures freshness, which
is precisely the failure the visible `verified` date exists to prevent.

Usage:
    availability-check.py                 # check every card that has a source
    availability-check.py --card etherfi
    availability-check.py --dry-run       # report, never bump, never alert-file
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
YAML_PATH = ROOT / "config" / "availability.yaml"
ALERTS = ROOT / "logs" / "aeo" / "availability-alerts.json"
# Append-only, and deliberately NOT the alert file: an alert describes the
# present disagreement and vanishes when it is resolved, while the journal is
# what the monthly digest reads — the resolution is the story.
JOURNAL = ROOT / "logs" / "aeo" / "fact-journal.jsonl"


def journal(entry: dict) -> None:
    """Record one observed change about the issuer. Never rewrites a line."""
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    entry = {"observed": date.today().isoformat(), **entry}
    with JOURNAL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def journal_since(days: int) -> list[dict]:
    """Entries observed in the last `days` days, oldest first."""
    if not JOURNAL.is_file():
        return []
    cutoff = date.today() - timedelta(days=days)
    out = []
    for line in JOURNAL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            when = date.fromisoformat(row.get("observed", ""))
        except Exception:  # noqa: BLE001
            continue
        if when >= cutoff:
            out.append(row)
    return out
UA = "DefyCardBot/1.0 (+https://defycard.com; availability-check)"

# --- parsers ---------------------------------------------------------------
# One per issuer help-centre platform. A card whose `source.parser` is not
# listed here is reported as unparseable — never as "unchanged".

def parse_intercom_tables(html: str) -> dict[str, list[str]]:
    """Intercom help articles (help.ether.fi).

    The article body ships as JSON inside the HTML: a stream of heading /
    subheading markers and one-column table cells. Every heading of either
    level opens a new section and the cells that follow belong to it — which
    means a table's own header row ("Country") is itself a section name. That
    is fine: the YAML says which section holds which list, by exact name, and
    a renamed heading has to surface as "not found" rather than as an empty
    list that would read like the issuer had lifted every restriction.
    """
    out: dict[str, list[str]] = {}
    current: str | None = None
    for m in re.finditer(
            r'"type":"(?:sub)?heading","text":"([^"]*)"'
            r'|"type":"paragraph","text":"([^"]*)","class":"no-margin"', html):
        head, cell = m.group(1), m.group(2)
        if head is not None:
            current = head.strip().lower()
            out.setdefault(current, [])
        elif current is not None:
            out[current].append(cell.strip())
    return out


PARSERS = {"intercom_tables": parse_intercom_tables}


def _section(sections: dict[str, list[str]], want: str) -> list[str] | None:
    """Exact (case-folded) heading match.

    Not a substring match: "countries where card spending is unsupported" is a
    different list from the availability one, and a loose match that grabbed
    the wrong table would produce a confident, wrong comparison.
    """
    return sections.get(want.strip().lower())


# --- the check -------------------------------------------------------------

def fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def compare(card: dict[str, Any], html: str) -> dict[str, Any]:
    """-> {'status': 'match'|'differs'|'unparseable', ...}. Pure; no I/O."""
    parser = PARSERS.get((card.get("source") or {}).get("parser", ""))
    if parser is None:
        return {"status": "unparseable",
                "summary": f"no parser for source.parser="
                           f"{(card.get('source') or {}).get('parser')!r}"}
    sections = parser(html)
    heads = (card.get("source") or {}).get("sections") or {}
    diffs: list[str] = []
    for field, heading in heads.items():
        found = _section(sections, heading)
        if found is None:
            return {"status": "unparseable",
                    "summary": f"section {heading!r} not found on the page"}
        ours, theirs = set(card.get(field) or []), set(found)
        if ours != theirs:
            added = sorted(theirs - ours)
            removed = sorted(ours - theirs)
            diffs.append(
                f"{field}: issuer adds {added or '-'}, issuer no longer lists {removed or '-'}")
    if diffs:
        return {"status": "differs", "summary": "; ".join(diffs)}
    return {"status": "match", "summary": "issuer list identical to ours"}


# ---------------------------------------------------------------------------
# Cashback rates (Y73)
# ---------------------------------------------------------------------------

RATES_YAML = ROOT / "config" / "rates.yaml"


def _clean_cell(cell: str) -> str:
    """Intercom ships the body as escaped JSON; unwrap the bits that carry
    meaning and drop the markup that does not."""
    s = cell.replace("\\u003cbr\\u003e", " / ")
    s = re.sub(r"\\u003c/?b\\u003e", "", s)
    s = re.sub(r"\\u003c[^\\]*?\\u003e", "", s)
    s = s.replace("\\u0026amp;", "&").replace("\\u2013", "\u2013")
    return re.sub(r"\s+", " ", s).strip()


def parse_rate_tables(html: str) -> dict[str, list[dict[str, str]]]:
    """-> {level: [{band, usd, eur}, ...]}.

    Each level's section is a flat list of cells in row-major order: three
    header cells, then (band, usd_rate, eur_rate) triples. Reading it as
    triples is the whole parse — and a table that gains a column will produce
    garbage triples rather than silently dropping data, which is why the header
    row is checked first.
    """
    sections = parse_intercom_tables(html)
    out: dict[str, list[dict[str, str]]] = {}
    for level in ("core", "luxe", "pinnacle", "business"):
        cells = [c for c in (_clean_cell(x) for x in sections.get(level, [])) if c]
        if len(cells) < 6:
            continue
        header, body = cells[:3], cells[3:]
        if "monthly spend" not in header[0].lower():
            continue          # the table changed shape; caller reports it
        rows = []
        for i in range(0, len(body) - 2, 3):
            rows.append({"band": body[i], "usd": body[i + 1], "eur": body[i + 2]})
        out[level] = rows
    return out


def compare_rates(expected: dict, html: str) -> dict[str, Any]:
    """-> {'status': 'match'|'differs'|'unparseable', 'summary': str}."""
    found = parse_rate_tables(html)
    if not found:
        return {"status": "unparseable",
                "summary": "no rate tables found — the article changed shape"}
    diffs: list[str] = []
    levels = expected.get("levels") or {}
    for level, rows in levels.items():
        theirs = found.get(level)
        if theirs is None:
            diffs.append(f"{level}: table missing from the article")
            continue
        ours = [{"band": r["band"], "usd": r["usd"], "eur": r["eur"]} for r in rows]
        if ours != theirs:
            for i, (a, b) in enumerate(zip(ours, theirs)):
                if a != b:
                    diffs.append(f"{level} row {i + 1}: ours {a} vs issuer {b}")
            if len(ours) != len(theirs):
                diffs.append(f"{level}: we list {len(ours)} bands, "
                             f"the issuer lists {len(theirs)}")
    for level in found:
        if level not in levels:
            diffs.append(f"{level}: NEW level on the issuer's page, absent from ours")
    if diffs:
        return {"status": "differs", "summary": "; ".join(diffs[:8])}
    return {"status": "match",
            "summary": f"all {sum(len(v) for v in levels.values())} rate bands "
                       f"identical across {len(levels)} levels"}


def check_rates(dry_run: bool, alerts: list) -> int:
    """The same three outcomes as the availability check, on the rate tables."""
    import yaml
    if not RATES_YAML.is_file():
        print("[rates] config/rates.yaml missing — skipped", file=sys.stderr)
        return 0
    cfg = yaml.safe_load(RATES_YAML.read_text(encoding="utf-8"))
    src = cfg.get("source") or {}
    url = src.get("url")
    if not url:
        print("[rates] no source url — skipped (stays unverified)", file=sys.stderr)
        return 0
    try:
        html = fetch(url)
    except Exception as e:  # noqa: BLE001
        # UNREACHABLE is not "unchanged". No bump, and it is reported.
        alerts.append({"card": "rates", "status": "unreachable",
                       "summary": f"{type(e).__name__}: {str(e)[:120]}"})
        print(f"[rates] unreachable: {type(e).__name__} — NOT bumping", file=sys.stderr)
        return 1

    # A promo that has just crossed its own `expires:` date is a market change
    # in its own right, and the only one the issuer's page will never announce.
    for promo in (cfg.get("promos") or []):
        exp = promo.get("expires")
        if not exp:
            continue
        try:
            when = date.fromisoformat(str(exp))
        except ValueError:
            continue
        if when == date.today() - timedelta(days=1) and not dry_run:
            journal({"kind": "promo_expired", "card": "etherfi",
                     "summary": f"{promo.get('name')} ({promo.get('rate')}) "
                                f"ended {exp}",
                     "source": src.get("url", "")})

    verdict = compare_rates(cfg, html)
    print(f"[rates] {verdict['status']}: {verdict['summary']}")
    if verdict["status"] != "match":
        alerts.append({"card": "rates", **verdict})
        if verdict["status"] == "differs" and not dry_run:
            journal({"kind": "rates", "card": "etherfi",
                     "summary": verdict["summary"], "source": url})
        return 1
    if dry_run:
        print(f"[rates] verified -> {date.today().isoformat()} (dry-run)")
        return 0
    text = RATES_YAML.read_text(encoding="utf-8")
    new = re.sub(r'^(  verified:\s*)"[^"]*"',
                 lambda m: f'{m.group(1)}"{date.today().isoformat()}"',
                 text, count=1, flags=re.M)
    if new != text:
        RATES_YAML.write_text(new, encoding="utf-8")
        print(f"[rates] verified -> {date.today().isoformat()}")
    return 0


def bump_verified(text: str, card_key: str, day: str) -> str:
    """Rewrite exactly one `verified:` line — the one inside `cards: <key>:`.

    Line-level surgery rather than a YAML round-trip: the file is hand-edited
    and hand-commented, and a dump would reflow every comment that explains
    why the numbers in it are what they are.
    """
    lines = text.splitlines(keepends=True)
    in_cards = False
    in_card = False
    out = []
    done = False
    for line in lines:
        if re.match(r"^cards:\s*$", line):
            in_cards, in_card = True, False
        elif in_cards and re.match(r"^\S", line):
            in_cards, in_card = False, False
        elif in_cards and re.match(rf"^  {re.escape(card_key)}:\s*$", line):
            in_card = True
        elif in_cards and re.match(r"^  \S", line):
            in_card = False
        if in_card and not done and re.match(r"^    verified:\s*", line):
            line = f'    verified: "{day}"\n'
            done = True
        out.append(line)
    if not done:
        raise KeyError(f"no `verified:` line under cards.{card_key}")
    return "".join(out)


def main() -> int:
    import yaml
    ap = argparse.ArgumentParser()
    ap.add_argument("--card")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-rates", action="store_true",
                    help="country lists only (the rates check is on by default)")
    a = ap.parse_args()

    text = YAML_PATH.read_text(encoding="utf-8")
    cfg = yaml.safe_load(text) or {}
    cards = cfg.get("cards") or {}
    today = str(date.today())

    alerts: list[dict[str, str]] = []
    for key, card in cards.items():
        if a.card and key != a.card:
            continue
        url = (card.get("source") or {}).get("url")
        if not url:
            print(f"[avail] {key}: no source URL — skipped (stays unpublished)")
            continue
        try:
            html = fetch(url)
        except Exception as e:  # noqa: BLE001
            res = {"status": "unreachable", "summary": f"{type(e).__name__}: {e}"}
        else:
            res = compare(card, html)

        print(f"[avail] {key}: {res['status']} — {res['summary']}")
        if res["status"] == "match":
            if not a.dry_run:
                text = bump_verified(text, key, today)
                YAML_PATH.write_text(text, encoding="utf-8")
            print(f"[avail] {key}: verified -> {today}")
        else:
            # NOT a bump, NOT an edit. A human decides what the truth is.
            alerts.append({"card": key, "status": res["status"],
                           "summary": res["summary"], "checked": today,
                           "source": url})
            if res["status"] == "differs" and not a.dry_run:
                journal({"kind": "availability", "card": key,
                         "summary": res["summary"], "source": url})

    # The rates half. Same three outcomes, same alert file — one instrument
    # reporting on one issuer, because a second script would drift from this
    # one the first time either law changed.
    if not a.card and not a.skip_rates:
        check_rates(a.dry_run, alerts)

    if not a.dry_run:
        ALERTS.parent.mkdir(parents=True, exist_ok=True)
        if alerts:
            ALERTS.write_text(json.dumps(alerts, indent=2) + "\n", encoding="utf-8")
        elif ALERTS.exists():
            ALERTS.unlink()
    if alerts:
        print(f"\n{len(alerts)} item(s) need a HUMAN edit to config/availability.yaml "
              f"or config/rates.yaml", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
