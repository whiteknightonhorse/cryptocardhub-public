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

_BODY_RE = re.compile(
    r'"type":"(?:sub)?heading","text":"([^"]*)"'
    r'|"type":"paragraph","text":"([^"]*)","class":"no-margin"')

# A paragraph cell whose ENTIRE text is one <b>...</b> span, and nothing else,
# reads as a label rather than body copy. Plasma's article 14703938 ships
# "Supported US states" this way — a plain paragraph, not a heading/subheading
# node (unlike every section label on ether.fi's 758780) — so without this a
# stream reader never sees a section boundary there and the cells after it
# get folded into the PRECEDING section (T-0142-08, first attempt: the "us
# states" section was unreachable, read as "countries that are blocked" with
# 47 cells in it instead). No etherfi fixture paragraph is wholly bold, so
# this adds a boundary Plasma needs without changing anything ether.fi reads.
_WHOLLY_BOLD_RE = re.compile(r"\\u003cb\\u003e[^\\]*\\u003c/b\\u003e$")


def parse_intercom_stream(
        html: str, bold_paragraph_headings: bool = False) -> list[tuple[str, list[str]]]:
    """Intercom help articles (help.ether.fi), as ORDERED (heading, cells).

    The article body ships as JSON inside the HTML: a stream of heading /
    subheading markers and one-column table cells. Every heading of either
    level opens a new section and the cells that follow belong to it — which
    means a table's own header row ("Country") is itself a section name.

    Occurrences are NOT merged into a dict here, because article 758780 uses
    the SAME column header ("Physical Card Available") for its country table
    and for its US-state table. Merging them produced one 242-cell blob in
    which a country and a state were indistinguishable — and "Georgia" is
    both. Order is the only thing that separates them, so order is kept.

    Heading text is run through `_clean_cell` before matching — Plasma's own
    heading nodes ship their text wrapped in `<b>...</b>` (`"type":"subheading",
    "text":"\\u003cb\\u003eUnsupported countries\\u003c/b\\u003e"`), which
    ether.fi's plain-text headings never do; without stripping it, every
    `_stream_section` lookup against a literal lowercase heading in the YAML
    misses and the section reads as unparseable.

    `bold_paragraph_headings`, OFF by default: when on, a paragraph cell
    whose entire text is one <b>...</b> span, and nothing else, is ALSO
    treated as a section boundary. Plasma's "Supported US states" ships this
    way — a plain paragraph, not a heading/subheading node — so
    `compare_sections` turns this on. It must stay off for
    `parse_intercom_tables` (the rates check): a rate table's own header row
    ships as three consecutive wholly-bold paragraph cells
    ("**Monthly Spend**", "**Standard Cashback Rate**", "**EUR Cashback
    Rate**") that are cells of the table, not new sections — turning this on
    unconditionally split every rate table into four empty fragments and lost
    every row.
    """
    out: list[tuple[str, list[str]]] = []
    for m in _BODY_RE.finditer(html):
        head, cell = m.group(1), m.group(2)
        if head is not None:
            out.append((_clean_cell(head).lower(), []))
        elif bold_paragraph_headings and cell is not None and _WHOLLY_BOLD_RE.fullmatch(cell):
            out.append((_clean_cell(cell).lower(), []))
        elif out and cell is not None:
            out[-1][1].append(cell.strip())
    return out


def parse_intercom_tables(html: str) -> dict[str, list[str]]:
    """Heading -> cells, repeats concatenated. The shape the rate tables use."""
    out: dict[str, list[str]] = {}
    for head, cells in parse_intercom_stream(html):
        out.setdefault(head, []).extend(cells)
    return out


PARSERS = {"intercom_tables": parse_intercom_tables}


def _stream_section(stream: list[tuple[str, list[str]]], want: str) -> list[str] | None:
    """Exact (case-folded) heading match, with one level of nesting.

        "physical card available"                    -> the FIRST such section
        "united states > physical card available"    -> the first one that
                                                        follows a "united
                                                        states" heading

    Not a substring match: "countries where card spending is unsupported" is a
    different list from the availability one, and a loose match that grabbed
    the wrong table would produce a confident, wrong comparison. A heading the
    issuer renames therefore reads as "could not check", never as an empty
    list that would look like every restriction had been lifted.
    """
    parts = [p.strip().lower() for p in want.split(">")]
    leaf, outer = parts[-1], parts[:-1]
    seen = not outer
    for head, cells in stream:
        if outer and head == outer[0]:
            seen = True
        elif seen and head == leaf:
            return cells
    return None


def _section(sections: dict[str, list[str]], want: str) -> list[str] | None:
    """Dict-shaped lookup, kept for the rate tables."""
    return sections.get(want.strip().lower())


# --- the check -------------------------------------------------------------

def fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_flat_list(cells: list[str]) -> list[str]:
    """A section whose cells ARE the list, one name per cell — no verdict
    signs to key off, because the issuer stopped shipping the ✅/❌ table
    (T-0134, 2026-09-17) in favour of a plain enumeration.

    Leading cells that read as an intro SENTENCE (end in "." or ":" once
    cleaned) are dropped, not counted as list members. ether.fi's list
    sections open directly with a name and are unaffected; Plasma's three
    list sections (article 14703938) each open with one explanatory
    paragraph ("Plasma One is not currently available in the following
    countries...", "...verification at this time:") before the names start —
    without this, that sentence itself counted as a 'country' and the real
    list of 7/12/31 always came back exactly one instance shy of matching.
    """
    cleaned = [c for c in (_clean_cell(x) for x in cells) if c]
    while cleaned and cleaned[0].endswith((".", ":")):
        cleaned.pop(0)
    return cleaned


def parse_prose_list(cells: list[str], anchor: str) -> list[str] | None:
    """One cell holds "ANCHOR name, name, ..., name." — the anchor is a
    literal prefix stated in the YAML, never guessed from the cell shape
    (T-0134 law #2). None means no cell started with it: the caller must
    treat that as unparseable, never as an empty list.
    """
    for raw in cells:
        cell = _clean_cell(raw)
        if cell.startswith(anchor):
            tail = cell[len(anchor):].strip().rstrip(".")
            return [n.strip() for n in tail.split(",") if n.strip()]
    return None


_PROSE_NO_RE = re.compile(
    r"^(.*?)\s+(?:is|are) eligible regions? for ether\.fi Cash\. "
    r"However, physical Cash card delivery is not currently available "
    r"in (?:this|these) regions?\.", re.I)


def parse_prose_no(cells: list[str]) -> list[str] | None:
    """The issuer's "X and Y are eligible regions ... not currently available"
    sentence, in the shape of article 758780 as restructured 2026-09-17. None
    means the sentence's own wording is gone — unparseable, not "nobody is
    refused" (T-0134 law #4: this list is money, see Y50)."""
    for raw in cells:
        cell = _clean_cell(raw)
        m = _PROSE_NO_RE.match(cell)
        if m:
            return [n.strip() for n in re.split(r",|\band\b", m.group(1)) if n.strip()]
    return None


_SECTION_FORMS = {
    "list": lambda cells, spec: parse_flat_list(cells) or None,
    "prose_no": lambda cells, spec: parse_prose_no(cells),
    "prose_list": lambda cells, spec: parse_prose_list(cells, spec["anchor"]),
}


def compare_sections(card: dict[str, Any], html: str) -> dict[str, Any]:
    """Allow-list card against sections whose FORM is declared in the YAML,
    not inferred from the page (T-0134 law #2 — autodetection is the same
    weakening in different clothes). Pure; no I/O.

    `apply` is the machine half of the verdict: the exact lists the issuer's
    page states right now. Nothing writes it here — `--accept` does, and only
    the operator runs that.
    """
    stream = parse_intercom_stream(html, bold_paragraph_headings=True)
    heads = (card.get("source") or {}).get("sections") or {}
    diffs: list[str] = []
    apply: dict[str, list[str]] = {}
    for field, spec in heads.items():
        heading = spec["heading"]
        cells = _stream_section(stream, heading)
        if cells is None:
            return {"status": "unparseable",
                    "summary": f"section {heading!r} not found on the page"}
        form_fn = _SECTION_FORMS.get(spec["form"])
        found = form_fn(cells, spec) if form_fn else None
        if found is None:
            return {"status": "unparseable",
                    "summary": f"section {heading!r} ({spec['form']}) no "
                               f"longer matches the expected shape/wording"}
        ours = set(card.get(field) or [])
        theirs = set(found)
        if ours != theirs:
            added = sorted(theirs - ours)
            removed = sorted(ours - theirs)
            diffs.append(f"{field}: issuer adds {added or '-'}, "
                         f"issuer no longer lists {removed or '-'}")
        apply[field] = found
    if diffs:
        return {"status": "differs", "summary": "; ".join(diffs), "apply": apply}
    return {"status": "match", "summary": "issuer list identical to ours"}


def compare(card: dict[str, Any], html: str) -> dict[str, Any]:
    """-> {'status': 'match'|'differs'|'unparseable', ...}. Pure; no I/O."""
    if (card.get("source") or {}).get("parser") == "intercom_sections":
        return compare_sections(card, html)
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


_LOCK_RE = re.compile(r"(\d+)-day lock period")
_MIN_CLAIM_RE = re.compile(r"minimum of \$(\d+(?:\.\d+)?) USD in cashback")


def parse_lock_and_minimum(html: str) -> dict[str, float]:
    """The two facts the article states in PROSE, not in a table.

    Read straight off the raw fetch: the surrounding sentence carries no HTML
    entities that would need `_clean_cell`'s unwrapping, and going through the
    table parser would tie a paragraph fact to a table-shaped one for no
    reason. Missing keys mean "the article no longer says this in this
    wording" — the caller must not treat that as zero.
    """
    out: dict[str, float] = {}
    m = _LOCK_RE.search(html)
    if m:
        out["lock_days"] = int(m.group(1))
    m = _MIN_CLAIM_RE.search(html)
    if m:
        v = float(m.group(1))
        out["min_claim_usd"] = int(v) if v.is_integer() else v
    return out


def compare_rates(expected: dict, html: str) -> dict[str, Any]:
    """-> {'status': 'match'|'differs'|'unparseable', 'summary': str[, 'apply': dict]}."""
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

    # The two prose facts, not tied to any tier — grouped under one "business:"
    # line (account-wide rules of the cashback business, not the Business
    # tier) so `--accept` writes both root-level fields as one operator call.
    apply: dict[str, Any] = {}
    facts = parse_lock_and_minimum(html)
    biz: list[str] = []
    for field, label in (("lock_days", "lock_days"), ("min_claim_usd", "min_claim_usd")):
        issuer_value = facts.get(field)
        if issuer_value is None or expected.get(field) == issuer_value:
            continue
        biz.append(f"{label} {expected.get(field)!r}->{issuer_value}")
        apply[field] = issuer_value
    if biz:
        diffs.append("business: " + ", ".join(biz))

    if diffs:
        result: dict[str, Any] = {"status": "differs", "summary": "; ".join(diffs)}
        if apply:
            result["apply"] = apply
        return result
    return {"status": "match",
            "summary": f"all {sum(len(v) for v in levels.values())} rate bands "
                       f"identical across {len(levels)} levels, lock/minimum unchanged"}


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
        row = {"id": alert_id("rates", verdict["summary"]), "card": "rates",
               "checked": date.today().isoformat(), "source": url, **verdict}
        alerts.append(row)
        if row.get("apply"):
            print(f"[rates] accept with scripts/availability-check.py "
                  f"--accept {row['id']}")
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


def replace_list(text: str, card_key: str, field: str, values: list[str]) -> str:
    """Rewrite one block sequence under `cards: <key>:`, comments intact.

    Same line surgery as bump_verified and for the same reason: this file is
    hand-commented and a YAML round-trip would reflow every comment that
    explains why the numbers in it are what they are.

    Only the block form (`field:` then `      - item` lines) is understood. An
    inline `field: [a, b]` raises instead of being silently truncated — losing
    the tail of an availability list is a money bug, so the unknown shape has
    to stop the write, not be guessed at.
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_cards = in_card = False
    done = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"^cards:\s*$", line):
            in_cards, in_card = True, False
        elif in_cards and re.match(r"^\S", line):
            in_cards, in_card = False, False
        elif in_cards and re.match(rf"^  {re.escape(card_key)}:\s*$", line):
            in_card = True
        elif in_cards and re.match(r"^  \S", line):
            in_card = False
        m = re.match(rf"^    {re.escape(field)}:(.*)$", line) if in_card else None
        if m and not done:
            if m.group(1).strip() and not m.group(1).strip().startswith("#"):
                raise ValueError(
                    f"cards.{card_key}.{field} is not a block sequence "
                    f"({m.group(1).strip()[:40]!r}) — refusing to rewrite it")
            out.append(f"    {field}:{m.group(1).rstrip()}\n" if m.group(1).strip()
                       else f"    {field}:\n")
            out.extend(f"      - {v}\n" for v in values)
            i += 1
            while i < len(lines) and re.match(r"^      - ", lines[i]):
                i += 1
            done = True
            continue
        out.append(line)
        i += 1
    if not done:
        raise KeyError(f"no `{field}:` block under cards.{card_key}")
    return "".join(out)


def replace_scalar(text: str, field: str, value: Any) -> str:
    """Rewrite one top-level `field: value` line in rates.yaml, comments intact.

    Same line-surgery reasoning as replace_list/bump_verified: the file is
    hand-commented and a YAML round-trip would reflow the comments that
    explain the numbers. Only a bare top-level scalar (`lock_days:`,
    `min_claim_usd:`) is understood; a missing line raises rather than
    appending one in a guessed spot.
    """
    pattern = re.compile(rf"^{re.escape(field)}:.*$", re.M)
    if not pattern.search(text):
        raise KeyError(f"no top-level `{field}:` line in the file")
    return pattern.sub(f"{field}: {json.dumps(value)}", text, count=1)


def alert_id(card_key: str, summary: str) -> str:
    """Stable handle for one disagreement. Derived from the diff itself, so
    the id in the Telegram message names exactly the diff it was computed
    from — a re-run that finds a different diff gets a different id and the
    operator's queued `--accept` no longer matches."""
    import hashlib
    return hashlib.sha256(f"{card_key}|{summary}".encode()).hexdigest()[:12]


def accept(alert_ref: str) -> int:
    """Apply EXACTLY the diff carried by one alert, then bump `verified`.

    The machine still does not decide what the truth is: it replays a diff a
    human read and approved by id. An id that is not in the alert file, or an
    alert that carries no `apply`, writes nothing.
    """
    if not ALERTS.is_file():
        print(f"no alert file at {ALERTS} — nothing to accept", file=sys.stderr)
        return 1
    rows = json.loads(ALERTS.read_text(encoding="utf-8"))
    match = [r for r in rows if r.get("id") == alert_ref]
    if not match:
        print(f"no alert with id {alert_ref!r} (have: "
              f"{[r.get('id') for r in rows]})", file=sys.stderr)
        return 1
    row = match[0]
    changes = row.get("apply") or {}
    if not changes:
        print(f"alert {alert_ref} carries no list to apply "
              f"(status={row.get('status')!r}) — nothing written", file=sys.stderr)
        return 1
    if row["card"] == "rates":
        # Scalar root-level fields, not a list under `cards:` — a different
        # file, a different write, the same one-command law.
        text = RATES_YAML.read_text(encoding="utf-8")
        for field, value in changes.items():
            text = replace_scalar(text, field, value)
        text = re.sub(r'^(  verified:\s*)"[^"]*"',
                      lambda m: f'{m.group(1)}"{date.today().isoformat()}"',
                      text, count=1, flags=re.M)
        RATES_YAML.write_text(text, encoding="utf-8")
        journal({"kind": "rates_accepted", "card": "rates",
                 "summary": row.get("summary", ""), "source": row.get("source", ""),
                 "alert": alert_ref})
        print(f"accepted {alert_ref}: {', '.join(f'{k}={v}' for k, v in changes.items())}"
              f" — rates.yaml verified -> {date.today().isoformat()}")
    else:
        text = YAML_PATH.read_text(encoding="utf-8")
        for field, values in changes.items():
            text = replace_list(text, row["card"], field, values)
        text = bump_verified(text, row["card"], str(date.today()))
        YAML_PATH.write_text(text, encoding="utf-8")
        journal({"kind": "availability_accepted", "card": row["card"],
                 "summary": row.get("summary", ""), "source": row.get("source", ""),
                 "alert": alert_ref})
        print(f"accepted {alert_ref}: {', '.join(f'{k}={len(v)}' for k, v in changes.items())}"
              f" — cards.{row['card']}.verified -> {date.today().isoformat()}")
    rest = [r for r in rows if r.get("id") != alert_ref]
    if rest:
        ALERTS.write_text(json.dumps(rest, indent=2) + "\n", encoding="utf-8")
    else:
        ALERTS.unlink()
    return 0


def main() -> int:
    import yaml
    ap = argparse.ArgumentParser()
    ap.add_argument("--card")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--accept", metavar="ALERT_ID",
                    help="apply exactly the diff of that alert (operator only)")
    ap.add_argument("--skip-rates", action="store_true",
                    help="country lists only (the rates check is on by default)")
    a = ap.parse_args()

    if a.accept:
        return accept(a.accept)

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
            row = {"id": alert_id(key, res["summary"]), "card": key,
                   "status": res["status"], "summary": res["summary"],
                   "checked": today, "source": url}
            if res.get("apply"):
                row["apply"] = res["apply"]
                print(f"[avail] {key}: accept with "
                      f"scripts/availability-check.py --accept {row['id']}")
            alerts.append(row)
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
        if not a.dry_run:
            notify(alerts)
    return 0


def notify(alerts: list[dict]) -> None:
    """The diff AND the command that applies it, on the surface a human reads.

    Y57: a finding that only reaches a cron log is a finding nobody acts on.
    The accept command travels with the diff it was computed from, so the
    operator never has to reconstruct which change they are approving.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import telegram_lib
    except Exception as e:  # noqa: BLE001
        print(f"[avail] telegram unavailable: {type(e).__name__}", file=sys.stderr)
        return
    lines = []
    for row in alerts:
        lines.append(f"{row['card']} [{row['status']}]: {row['summary'][:400]}")
        if row.get("apply"):
            lines.append(f"  accept: scripts/availability-check.py "
                         f"--accept {row['id']}")
        else:
            lines.append("  no list to apply — edit config by hand")
    telegram_lib.alert("[availability] " + "\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
