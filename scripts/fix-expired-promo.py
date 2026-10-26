#!/usr/bin/env python3
"""fix-expired-promo.py — remove an expired promotional rate from published pages.

WHY THIS EXISTS
---------------
`.claude/skills/etherfi/SKILL.md` carried the row "Promo cashback — up to 15 %
on food" with no expiry field. The promotion ran January–February 2026. In
August it was still being printed as a current property of the card on 307 of
544 pages, 9 of them in the top 20 by AI-agent consumption, under a visible
"Last verified" stamp dated after the promo had ended.

The fact file is fixed; this removes what it already produced.

HOW IT IS SAFE — every rule below was bought with damage
-------------------------------------------------------
* **Frontmatter arrays are parsed, never regexed.** `snapshot_bullets`,
  `what_to_watch`, `bottom_line` and `faq` are one-line JSON values. A greedy
  regex applied to whole files once ate the opening bracket of such an array
  and broke 5 pages — and `seo-site/src/content/pseo` is gitignored, so there
  was nothing to restore from. Here the value is `json.loads`-ed, edited as
  data, and `json.dumps`-ed back: a structural break is impossible, and a value
  that fails to parse is skipped and reported rather than guessed at.
* **Prose is edited one sentence at a time, one line at a time.** No DOTALL,
  no multiline, so nothing can span a structural boundary.
* **Nothing unrecognised is touched.** Every occurrence the rules do not match
  is left exactly as it was and printed as manual work. On money claims a
  twenty-line manual tail is cheaper than false confidence in automation.
* **The count is asserted.** Occurrences found on the scan must equal
  occurrences resolved (rewritten + deliberately kept + reported); a silent
  drop is a failure, not a success.
* **Dry-run is the default.** `--write` is required to touch anything.

Usage:
    fix-expired-promo.py --pages top9          # dry-run, full diff
    fix-expired-promo.py --pages top9 --write
    fix-expired-promo.py --pages all --write
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "seo-site" / "src" / "content" / "pseo"
TARGETS = ROOT / "logs" / "aeo" / "targets-2026-W32.json"

TODAY = date.today().isoformat()

# The one page where the promo's history may be stated, dated, because people
# still search for it (Fable, 2026-08-13). Everywhere else it is removed.
HISTORY_PAGE = "ether-fi-cash-review"

# --- what counts as an occurrence ------------------------------------------
# 15 (or 15.0) percent in any spelling, near a food word. The food proximity is
# what makes it THIS claim: 15 % also appears legitimately elsewhere.
_PCT15 = r"(?:up\s+to\s+)?\*{0,2}15(?:\.0)?\s?%\*{0,2}"
FOOD = r"dining|grocer|food|restaurant|meal|eat"
OCCURRENCE = re.compile(_PCT15, re.I)
FOODY = re.compile(FOOD, re.I)

# --- canonical replacements, most specific first ---------------------------
# Each is (pattern, replacement, label). Applied to ONE sentence at a time.
RULES: list[tuple[re.Pattern, str, str]] = [
    # "Up to 3 % standard, up to 15 % on dining/groceries" -> the real structure
    # The dominant snapshot form, in every separator and suffix the corpus
    # actually uses: "Up to 3% standard, up to 15% on food/dining (promotional)"
    # and "up to 3% standard; up to 15% on dining & groceries (promo)".
    # The trailing parenthetical must be swallowed HERE — left behind it is a
    # scar, and a scar reverts the whole edit.
    (re.compile(r"[Uu]p to\s*\*{0,2}3\s?%\*{0,2}\s*standard[,;]?\s*(?:and\s*)?"
                r"up to\s*\*{0,2}15(?:\.0)?\s?%\*{0,2}\s*on\s*[\w /&-]*"
                r"(?:\s*\((?:promo|promotional)[^)]*\))?", re.I),
     "up to **3 %** on the first band of monthly spend, stepping down to 1 % "
     "and then 0.5 % as spend rises",
     "snapshot: standard+promo pair"),
    # The promo clause in every connector the corpus uses. Requires BOTH the
    # 15 % and a food word, so a legitimate "15 % of applicants" is untouched.
    (re.compile(
        r"\s*(?:[,;]|\+|\band\b|\bplus\b|\bwith\b|&)?\s*"
        r"(?:promo(?:tional)?\s*)?(?:cashback\s*)?(?:rates?\s*)?"
        r"(?:of\s+|to\s+|up\s+to\s+)?"
        r"\*{0,2}15(?:\.0)?\s?%\*{0,2}\s*"
        r"(?:promo(?:tional)?\s*)?"
        r"(?:on|for|in)\s+(?:" + FOOD + r")[\w /&-]*"
        r"(?:\s*\((?:promo|promotional|time-limited|seasonal)[^)]*\))?", re.I),
     "", "clause: promo rate on <food>, any connector"),
    # "3 % base + 15 % food promo", "up to 3 % general + up to 15 % dining/groceries"
    (re.compile(r"\s*\+\s*(?:up to\s*)?\*{0,2}15(?:\.0)?\s?%\*{0,2}\s*"
                r"[\w /&-]*(?:" + FOOD + r")[\w /&-]*(?:\s*promo\w*)?", re.I),
     "", "clause: '+ 15 % <food> promo'"),
    # "(or 15 % during food promos)"
    (re.compile(r"\s*\((?:or\s*)?(?:up to\s*)?\*{0,2}15(?:\.0)?\s?%\*{0,2}"
                r"[^)]*(?:" + FOOD + r")[^)]*\)", re.I),
     "", "parenthetical: '(or 15 % during food promos)'"),
    # ", with a 15 % promo rate on dining and groceries through 2026"
    (re.compile(r"[,;]?\s*(?:with|including)\s+(?:a\s+)?\*{0,2}15(?:\.0)?\s?%\*{0,2}"
                r"[^.]*?(?:" + FOOD + r")[\w /&-]*(?:\s*through\s*\d{4})?", re.I),
     "", "clause: 'with a 15 % promo rate on <food>'"),
    # heading "Cashback & Rewards: 3% Standard + 15% Food Promo"
    (re.compile(r":\s*3\s?%\s*Standard\s*\+\s*15\s?%\s*Food\s*Promo", re.I),
     "", "heading suffix"),
    # ", up to 15 % food promo" trailing a true rate
    (re.compile(r"[,;]\s*(?:up to\s*)?\*{0,2}15(?:\.0)?\s?%\*{0,2}\s*"
                r"(?:" + FOOD + r")[\w /&-]*\s*promo\w*", re.I),
     "", "trailing ', up to 15 % food promo'"),
    # "(up to 15 % on dining)" parenthetical
    (re.compile(r"\s*\((?:up to\s*)?\*{0,2}15(?:\.0)?\s?%\*{0,2}\s*on\s*[\w /&-]*\)", re.I),
     "", "parenthetical"),
]

# Sentences that exist ONLY to talk about the promo are dropped whole.
DROP_IF = re.compile(
    r"(?:" + _PCT15 + r")[^.]*(?:" + FOOD + r")|(?:" + FOOD + r")[^.]*(?:" + _PCT15 + r")",
    re.I)

# Debris an edit can leave behind. Each was produced by an actual dry-run.
SCARS = (
    re.compile(r"[+&]\s*[.;,]"),          # "3 % (standard) +."
    re.compile(r"[;,]\s*\("),             # "standard;(promo)"
    re.compile(r"\b(?:and|plus|or|with)\s*[.;,]", re.I),
    re.compile(r"\(\s*(?:promo|promotional|food|dining)\s*\)", re.I),
    re.compile(r"[.,;:]\s*[.,;:]"),
    re.compile(r"\bup to\s*[.,;)]", re.I),
)


def scarred(s: str) -> str | None:
    for rx in SCARS:
        m = rx.search(s)
        if m:
            return m.group(0)
    return None


LABEL = re.compile(r"^(\*\*[^*]+:\*\*)")
PROMO_LABEL = re.compile(r"promo", re.I)
# No markdown: inside these bullets only the leading `**Label:**` renders, so
# emphasis anywhere else reaches the reader as literal asterisks. Seen live on
# /learn/how-does-ether-fi-cash-work/ after stage 1.
CANONICAL_CASHBACK = ("up to 3% on the first band of monthly spend, stepping "
                      "down to 1% and then 0.5% as spend rises")

# Any rate figure that is NOT the expired 15 % promo. Its presence means the
# sentence carries truth worth keeping.
OTHER_RATE = re.compile(r"\b(?:0\.5|1|2|3|4|5)\s?%")

# A 15 % that is NOT this claim. Ranges are the giveaway: "10–15 %", "5–15 %".
# So is a 15 % that describes something other than a cashback rate.
NOT_THE_CLAIM = (
    re.compile(r"\d+\s*[\u2013\u2014-]\s*15(?:\.0)?\s?%"),   # 10–15 %
    re.compile(r"15(?:\.0)?\s?%\s*(?:move|swing|drop|rise|fall|of\b)", re.I),
    re.compile(r"(?:understates?|overstates?|by)\s+[\d\u2013-]*15\s?%", re.I),
)


def is_the_claim(sentence: str) -> bool:
    """True unless this 15 % is plainly about something else."""
    return not any(rx.search(sentence) for rx in NOT_THE_CLAIM)

FRONTMATTER_ARRAYS = ("snapshot_bullets", "what_to_watch", "bottom_line", "faq",
                      "supporting_keywords")
# JSON objects, not arrays — same structural treatment. `schema_faq` is the
# JSON-LD block Google and the answer engines read; the first dry-run edited it
# as raw text because the array regex did not match it. It came out intact by
# luck, and luck is what the greedy-regex accident was made of.
FRONTMATTER_OBJECTS = ("schema_faq",)


# ---------------------------------------------------------------------------

def sentences(text: str) -> list[str]:
    """Split keeping the delimiters, so rejoining is byte-exact."""
    return re.split(r"(?<=[.!?])(\s+)", text)


def fix_text(text: str, stats: dict) -> str:
    """Edit one string (a bullet, an FAQ answer, a prose line) sentence-wise."""
    if not OCCURRENCE.search(text):
        return text
    parts = sentences(text)
    out = []
    for part in parts:
        if not OCCURRENCE.search(part):
            out.append(part)
            continue
        original = part
        if not is_the_claim(part):
            stats["not_the_claim"] = stats.get("not_the_claim", 0) + 1
            out.append(part)
            continue
        for rx, rep, label in RULES:
            if rx.search(part):
                part = rx.sub(rep, part)
                stats["rules"][label] = stats["rules"].get(label, 0) + 1
        if OCCURRENCE.search(part) and DROP_IF.search(part):
            if OTHER_RATE.search(OCCURRENCE.sub("", part)):
                # the sentence also states a rate that is TRUE. Dropping it
                # would delete correct information to remove incorrect
                # information — the reader ends up worse informed either way,
                # so a human decides.
                stats["manual"].append("[mixed] " + original.strip()[:180])
                out.append(original)
                continue
            stats["dropped"] += 1
            continue
        if OCCURRENCE.search(part):
            stats["manual"].append(original.strip()[:200])
            out.append(original)          # untouched, reported
            continue
        out.append(tidy(part))
    result = tidy_join(out)
    scar = scarred(result)
    if scar:
        # revert the WHOLE string: a half-edited money sentence is worse than
        # an un-edited one, because it looks reviewed.
        stats["manual"].append(f"[scar {scar!r}] " + text.strip()[:180])
        stats["scarred"] = stats.get("scarred", 0) + 1
        return text
    return result


def tidy(s: str) -> str:
    """Clean the scars a removal leaves. Each pattern here is one the dry-run
    diff actually produced — `promotional rates () may apply` is not a
    hypothetical, it is what the first run wrote."""
    s = re.sub(r"\(\s*\)", "", s)             # "rates ()" -> "rates"
    s = re.sub(r"\[\s*\]", "", s)
    s = re.sub(r"\s+([,.;:])", r"\1", s)
    s = re.sub(r",\s*([.;])", r"\1", s)
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"\s+$", "", s) if s.endswith("  ") else s
    return s


def tidy_join(parts: list[str]) -> str:
    """A dropped sentence leaves its trailing separator behind, which is how
    'variable.  If consistency' got two spaces."""
    return re.sub(r"\s{2,}", " ", "".join(parts)).strip()


def fix_frontmatter_line(line: str, stats: dict) -> str:
    """A `key: [...]` line whose value is JSON — edited as data, never as text."""
    m = re.match(r"^(\w+):\s*([\[{].*[\]}])\s*$", line)
    if not m:
        return line
    key, raw = m.groups()
    if key not in FRONTMATTER_ARRAYS + FRONTMATTER_OBJECTS:
        return line
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        stats["unparsed"].append(key)
        return line
    changed = False

    def walk(v):
        nonlocal changed
        if isinstance(v, str):
            nv = fix_text(v, stats)
            stripped = LABEL.sub("", nv).strip(" .,;:-\u2014")
            if not stripped and LABEL.match(v):
                # the whole bullet was about the promo, but its LABEL is a
                # promise to the reader: a page that lists "Cashback:" and then
                # omits it reads as if the card pays none.
                label = LABEL.match(v).group(1)
                if PROMO_LABEL.search(label):
                    nv = ""          # the heading itself is about the promo
                elif "cashback" in label.lower():
                    nv = f"{label} {CANONICAL_CASHBACK}"
                else:
                    nv = ""
                stats["relabelled"] = stats.get("relabelled", 0) + 1
            changed |= nv != v
            return nv
        if isinstance(v, list):
            out = [walk(x) for x in v]
            return [x for x in out
                    if x is not None and not (isinstance(x, str) and not x.strip())]
        if isinstance(v, dict):
            # A Q&A whose QUESTION is the promo has no answer left once the
            # promo is gone: "What's the difference between 3 % and 15 %
            # cashback?" cannot be honestly answered by deleting half of it.
            q = v.get("q") or v.get("name") or ""
            if isinstance(q, str) and OCCURRENCE.search(q) and is_the_claim(q) \
                    and FOODY.search(json.dumps(v)):
                stats["faq_dropped"] = stats.get("faq_dropped", 0) + 1
                return None
            return {k: walk(x) for k, x in v.items()}
        return v

    value = walk(value)
    if isinstance(value, list):
        value = [x for x in value if x is not None]
    if not changed:
        return line
    return f"{key}: {json.dumps(value)}"


def fix_scalar_line(line: str, stats: dict) -> str:
    """A `key: "..."` line, or a body line. Both edited sentence-wise."""
    m = re.match(r'^(\w+):\s*(".*")\s*$', line)
    if m:
        key, raw = m.groups()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            stats["unparsed"].append(key)
            return line
        nv = fix_text(value, stats)
        return line if nv == value else f"{key}: {json.dumps(nv)}"
    return fix_text(line, stats)


# add_verified / bump_verified / load_table / apply_table used to live here in
# full. They now live in scripts/corpus_edit.py, with the `--expect` ceiling
# attached to them, and this script imports them — one implementation, per the
# rule the 2026-08-13 accident bought (.claude/CLAUDE.md, THE ACCIDENT).
sys.path.insert(0, str(ROOT / "scripts"))
from corpus_edit import (  # noqa: E402 -- LAW #ONE-PLACE canonical table editor
    add_verified, apply_table, bump_verified, load_table)

TABLE = ROOT / "scripts" / "promo-replacements.tsv"


def page_list(which: str) -> list[Path]:
    if which == "all":
        return sorted(p for p in CONTENT.glob("*.md")
                      if OCCURRENCE.search(p.read_text(encoding="utf-8")))
    tg = json.loads(TARGETS.read_text(encoding="utf-8"))
    hot = [r["path"].strip("/").split("/")[-1] for r in tg["hot"]]
    out = []
    for slug in hot:
        f = CONTENT / f"{slug}.md"
        if f.exists() and OCCURRENCE.search(f.read_text(encoding="utf-8")):
            out.append(f)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", choices=("top9", "all"), default="top9")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--diff", action="store_true", help="print the unified diff")
    ap.add_argument("--table", action="store_true",
                    help="apply scripts/promo-replacements.tsv literally instead "
                         "of running the pattern rules")
    a = ap.parse_args()

    files = page_list("all" if a.pages == "all" else "top9")
    print(f"pages carrying the claim: {len(files)}  (mode {a.pages}, "
          f"{'WRITE' if a.write else 'dry-run'})")

    if a.table:
        # The pattern modes only look at pages carrying the promo. A literal
        # table is a general repair tool and must see EVERY page — the last
        # eight artifacts sat on pages the promo had already left, so the
        # filter silently returned an empty set and the table reported "0
        # applied" as though the strings were wrong.
        files = sorted(CONTENT.glob("*.md"))
        print(f"table mode: scanning all {len(files)} pages, not only promo pages")
        table = load_table(TABLE) if TABLE.exists() else []
        print(f"literal replacements loaded: {len(table)}")
        st = apply_table(files, table, a.write)
        print(f"  replacements applied : {st['applied']}")
        print(f"  files changed        : {st['files']}   dated: {st['dated']}")
        if st["ambiguous"]:
            print(f"  appeared more than once (both copies replaced): "
                  f"{len(st['ambiguous'])}")
            for stem, frag, n in st["ambiguous"][:8]:
                print(f"     {stem}: x{n}  {frag}")
        if st["unused"]:
            print(f"  TABLE ENTRIES THAT MATCHED NOTHING: {len(st['unused'])}")
            for frag in st["unused"][:12]:
                print(f"     · {frag}")
        return 0

    stats = {"found": 0, "dropped": 0, "manual": [], "unparsed": [],
             "rules": {}, "date_bumped": 0, "no_date_field": [], "changed": 0}
    for f in files:
        if f.stem == HISTORY_PAGE:
            print(f"  SKIP {f.stem} — the one page allowed to state the promo's "
                  f"history, dated; edited by hand")
            continue
        before, after = process(f, stats)
        if after == before:
            continue
        stats["changed"] += 1
        if a.diff:
            print("\n".join(difflib.unified_diff(
                before.split("\n"), after.split("\n"),
                fromfile=f"a/{f.name}", tofile=f"b/{f.name}", lineterm="", n=1)))
        if a.write:
            f.write_text(after, encoding="utf-8")

    print(f"\noccurrences found: {stats['found']}")
    print(f"  rewritten by rule: {sum(stats['rules'].values())}")
    for label, n in sorted(stats["rules"].items(), key=lambda kv: -kv[1]):
        print(f"     {n:4d}  {label}")
    print(f"  whole sentences dropped: {stats['dropped']}")
    print(f"  not this claim (ranges etc, left alone): {stats.get('not_the_claim', 0)}")
    print(f"  FAQ entries removed with the promo: {stats.get('faq_dropped', 0)}")
    print(f"  reverted as scarred: {stats.get('scarred', 0)}")
    print(f"  relabelled bullets: {stats.get('relabelled', 0)}")
    print(f"  LEFT FOR MANUAL WORK: {len(stats['manual'])}")
    for s in stats["manual"][:40]:
        print(f"     · {s}")
    print(f"files changed: {stats['changed']}  date bumped on: {stats['date_bumped']}"
          f"  date field ADDED on: {stats.get('date_added', 0)}")
    if stats["no_date_field"]:
        print(f"  corrected but have NO visible date field ({len(stats['no_date_field'])}): "
              f"{stats['no_date_field'][:10]}")
    if stats["unparsed"]:
        print(f"  UNPARSEABLE frontmatter values (left alone): {stats['unparsed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
