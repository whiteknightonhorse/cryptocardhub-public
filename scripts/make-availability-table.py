#!/usr/bin/env python3
"""make-availability-table.py — derive the E-08 literal table from the corpus.

`scripts/tables/availability-2026-09.tsv` is several hundred literals. They are
not hand-TYPED: every `old` side is lifted verbatim out of the corpus's raw
bytes by this script, because a literal typed from a decoded print matches
nothing (the 2026-08-13 lesson) and a literal that matches nothing is a refusal
inside `corpus_edit.apply_table`.

WHAT IT DOES
------------
For every `Hungary` / `Belgium` / `Ireland` occurrence sitting inside a
comma-separated list of jurisdictions, it lifts that list run verbatim and
emits `run -> run without the name`. Where a COUNT immediately precedes the run
AND equals the number of names in it, the count is pulled into the same literal
and decremented — otherwise this run would itself introduce a page that says
"20 countries" over a list of 19. A count that does not equal the run's length
counts something else and is left alone.

Occurrences that are not list members (a bullet, a heading, a sentence about
one country) cannot be repaired by deleting a name from a list. They are the
hand-written PROSE entries below, one per measured occurrence, emitted FIRST so
that a shorter list literal can never eat part of a sentence one of them was
written for — `corpus_edit.load_table` preserves file order.

Usage:  make-availability-table.py > scripts/tables/availability-2026-09.tsv
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "seo-site" / "src" / "content" / "pseo"

# The jurisdictions whose verdict flipped when the issuer replaced its
# block-list with the allow-list of article 758780 (config/availability.yaml,
# E-06). Measured against the saved fixture, 2026-09-14:
#   Hungary  ✅ in the issuer's table — every one of its 464 corpus mentions
#               says the opposite (all 464 read and classified, none claims
#               availability).
#   Belgium  ❌, Ireland ❌ — carry the issuer's own cross and appear inside
#               our "available in" lists.
TARGETS = ["Hungary", "Belgium", "Ireland"]

# "Northern Ireland" is a UK region, not the refused jurisdiction.
PATTERN = {"Ireland": r"(?<!Northern )\bIreland\b"}

# `Crypto.com` and `ether.fi` are capitalised words too: without the lookahead
# a run walks out of its own clause and swallows the next sentence's subject.
_W = r"[A-Z][A-Za-z'\-]*\b(?!\.[a-z])"
NAME = r"(?:the )?" + _W + r"(?: (?:of |and )?" + _W + r")*"
# `/` is deliberately NOT a separator. `EU excluding A/B/C, LATAM, APAC` would
# re-render as commas and turn two regions the card DOES serve into excluded
# ones; the two slash-joined runs in the corpus are PROSE entries instead.
SEP = r"(?:, ?(?:and |or )?|,? and |,? or )"
SPLIT = re.compile(r", ?(?:and |or )?|,? and |,? or ")

WORD_NUM = {"three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
            "eight": 8, "nine": 9, "ten": 10, "nineteen": 19, "twenty": 20}
NUM_WORD = {v: k for k, v in WORD_NUM.items()}


def run_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Grow [start, end) left and right across `, `-joined capitalised names."""
    while True:
        m = re.search(SEP + r"$", text[:start])
        if not m:
            break
        nm = re.search(NAME + r"$", text[:m.start()])
        if not nm or nm.start() == m.start():
            break
        start = nm.start()
    while True:
        m = re.match(SEP + r"(" + NAME + r")", text[end:])
        if not m:
            break
        end += m.end()
    return start, end


def split_names(run: str) -> list[str]:
    return [n.strip() for n in SPLIT.split(run) if n.strip()]


def final_separator(run: str) -> str:
    """The separator before the last name, verbatim (', and ', ' or ', ', ')."""
    m = re.search(r"(, ?(?:and|or) |,? (?:and|or) |, ?|/)" + NAME + r"$", run)
    return m.group(1) if m else ", "


def render(names: list[str], sep: str) -> str:
    if len(names) == 1:
        return names[0]
    conj = sep.strip(" ,")                      # "and" | "or" | ""
    if len(names) == 2:
        # "A, and B" is a comma splice; two names take the bare conjunction.
        return f"{names[0]} {conj} {names[1]}" if conj else f"{names[0]}, {names[1]}"
    return ", ".join(names[:-1]) + sep + names[-1]


def count_prefix(text: str, run_start: int, n_names: int) -> int | None:
    """Offset of a count that labels this run and equals its length."""
    window = text[max(0, run_start - 130):run_start]
    if "\n" in window:
        window = window[window.rindex("\n") + 1:]
    best = None
    for m in re.finditer(r"\b(\d{1,3}|[A-Za-z]+)\b", window):
        tok, tail = m.group(1), window[m.end():]
        val = int(tok) if tok.isdigit() else WORD_NUM.get(tok.lower())
        if val != n_names:
            continue
        # A cashback rate is a number too. `up to 5 %), ... For those in
        # Netherlands, Finland, Estonia, or Hungary` once matched on its "5":
        # a label for this list sits next to it and inside one sentence.
        if tail.startswith(("%", " %")) or ". " in tail or len(tail) > 45:
            continue
        best = run_start - len(window) + m.start()
    return best


def decrement(tok: str, by: int = 1) -> str:
    if tok.isdigit():
        return str(int(tok) - by)
    word = NUM_WORD[WORD_NUM[tok.lower()] - by]
    return word.capitalize() if tok[0].isupper() else word


# ---------------------------------------------------------------------------
# Occurrences that are not list members. Every one was read in full context.
PROSE: list[tuple[str, str]] = [
    # does-ether-fi-cash-work-in-europe — a heading plus a five-bullet list.
    ("Five EU Countries Where Ether.fi Cash Is NOT Available\n\nDespite being in the EU, these five countries have account-opening and spending restrictions:\n\n- **Netherlands**\n- **Finland**\n- **Estonia**\n- **Hungary**\n- **Turkey** (EEA-adjacent)",
     "Four EU Countries Where Ether.fi Cash Is NOT Available\n\nDespite being in the EU, these four countries have account-opening and spending restrictions:\n\n- **Netherlands**\n- **Finland**\n- **Estonia**\n- **Turkey** (EEA-adjacent)"),
    # best-crypto-card-for-europe — "Excluded (4 major EU countries)" + bullets.
    ("**Excluded (4 major EU countries):**\n- **Netherlands** \u2192 Use Crypto.com or alternative non-custodial cards\n- **Finland** \u2192 Use Crypto.com\n- **Estonia** \u2192 Use Crypto.com\n- **Hungary** \u2192 Use Crypto.com",
     "**Excluded (3 major EU countries):**\n- **Netherlands** \u2192 Use Crypto.com or alternative non-custodial cards\n- **Finland** \u2192 Use Crypto.com\n- **Estonia** \u2192 Use Crypto.com"),
    # crypto-card-mica-eu — one bullet per country; Hungary's is its own line.
    ("\n- **Hungary** \u2014 Crypto-payment restrictions on consumer cards", ""),
    # metamask-card-vs-ether-fi — the same clause twice, two spellings.
    ("Turkey banned crypto cards in 2023; Hungary followed.",
     "Turkey banned crypto cards in 2023."),
    ("Turkey banned crypto card issuance in 2023; Hungary followed.",
     "Turkey banned crypto card issuance in 2023."),
    # best-crypto-card-2026 — a generic legality example that happens to name
    # the one EU country the issuer does serve. Turkey is absent from the
    # allow-list, and this corpus already states its 2023 ban.
    ("a card legal in Germany may be illegal in Hungary",
     "a card legal in Germany may be illegal in Turkey"),
    # mica-compliant-vs-non-compliant-crypto-card — the run here is an
    # UNAVAILABLE list whose majority is available: France, Germany, Spain,
    # Italy and the UK all carry the issuer's tick. Deleting Ireland (the one
    # name that belongs) would leave the false half standing, so the sentence
    # is restated from config/availability.yaml instead.
    ("Most of the core EU (France, Germany, Spain, Italy, Ireland, UK, etc.) is outside ether.fi's service zone due to MiCA complexity.",
     "Most of the core EU (France, Germany, Spain, Italy, UK, etc.) is inside ether.fi's service zone; Belgium and Ireland are the two European countries the issuer refuses outright."),
    # how-to-use-crypto-card-on-apple-pay — slash-joined, inside a wider
    # comma list of regions that are NOT excluded.
    ("EU excluding Netherlands/Estonia/Finland/Hungary",
     "EU excluding Netherlands/Estonia/Finland"),
    # does-crypto-com-card-work-in-europe — Belgium was the Benelux country
    # the card supposedly served; the issuer refuses it, so the exception
    # named in the same breath has to move with it.
    ("**Benelux** (Netherlands exception): ether.fi works in Belgium/Luxembourg. Netherlands users must choose Crypto.com or seek alternatives.",
     "**Benelux** (Netherlands and Belgium exceptions): ether.fi works in Luxembourg. Netherlands and Belgium users must choose Crypto.com or seek alternatives."),

    # --- the same claim in ISO-2, on 12 pages -----------------------------
    # Y56: one page, one law, whatever the serialization. `HU` was found only
    # because a diff of an already-"finished" page still read `exc. NL, FI,
    # EE, HU`; a name-shaped grammar cannot see a two-letter code.
    ("four EU countries (NL, FI, EE, HU) are currently blocked",
     "three EU countries (NL, FI, EE) are currently blocked"),
    # Crypto.com's own coverage, in the sentence that follows the block-list.
    ("use Crypto.com Card (available in NL, FI, HU but not always EE)",
     "use Crypto.com Card (available in NL, FI but not always EE)"),
    ("NL, FI, EE, HU", "NL, FI, EE"),
    ("NL, FI, HU, EE", "NL, FI, EE"),
    ("NL/FI/EE/HU", "NL/FI/EE"),
    ("NL/FI/HU/EE", "NL/FI/EE"),
    # --- and at city level -------------------------------------------------
    # best-crypto-card-europe: Amsterdam was in BOTH halves of this sentence
    # before E-08 — a pre-existing contradiction on the same law (the issuer
    # does not serve the Netherlands), corrected in the same literal.
    ("If you're in Berlin, Paris, Madrid, Amsterdam, or London, ether.fi works. If you're in Helsinki, Budapest, or Amsterdam, it doesn't.",
     "If you're in Berlin, Paris, Madrid, Budapest, or London, ether.fi works. If you're in Helsinki, Amsterdam, or Tallinn, it doesn't."),
    # best-crypto-card-france: Brussels is in Belgium, which the issuer refuses.
    ("whether you're in Paris, Brussels, or online",
     "whether you're in Paris, Lyon, or online"),
]

# (file stem, verbatim run) pairs the automatic pass must not touch because a
# PROSE entry above already covers them.
SKIP_RUNS = {("mica-compliant-vs-non-compliant-crypto-card",
              "France, Germany, Spain, Italy, Ireland, UK")}


def encode(s: str) -> str:
    return s.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", " ")


def main() -> int:
    seen: dict[str, str] = {}
    order: list[tuple[str, str]] = []
    conflicts = 0

    def add(old: str, new: str) -> bool:
        nonlocal conflicts
        if old == new or not old:
            return False
        if old in seen:
            if seen[old] != new:
                conflicts += 1
                print(f"CONFLICT {old[:70]!r}\n   A={seen[old][:70]!r}\n"
                      f"   B={new[:70]!r}", file=sys.stderr)
            return False
        seen[old] = new
        order.append((old, new))
        return True

    for old, new in PROSE:
        add(old, new)

    rows: list[tuple[str, str]] = []
    skipped_solo = 0
    for f in sorted(CONTENT.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        spans = set()
        for target in TARGETS:
            for m in re.finditer(PATTERN.get(target, r"\b" + target + r"\b"),
                                 text):
                spans.add(run_bounds(text, m.start(), m.end()))
        for s, e in sorted(spans):
                run = text[s:e]
                if (f.stem, run) in SKIP_RUNS:
                    continue
                # A run touching a `/` is half of a slash-joined list the
                # comma grammar cannot see; those are PROSE entries.
                if text[s - 1:s] == "/" or text[e:e + 1] == "/":
                    skipped_solo += 1
                    continue
                names = split_names(run)
                # Every target inside one run goes in one replacement: a run
                # holding both Belgium and Ireland must not produce two rival
                # `new` sides for the same `old`.
                dropped = [n for n in names if n.replace("the ", "") in TARGETS]
                keep = [n for n in names if n.replace("the ", "") not in TARGETS]
                if len(names) < 2 or not dropped:
                    skipped_solo += 1
                    continue
                if "Hungary" in dropped and len(dropped) > 1:
                    # One list cannot be both the available and the unavailable
                    # one. If this ever fires the run needs a PROSE entry.
                    print(f"MIXED POLARITY {f.stem}: {run[:80]!r}",
                          file=sys.stderr)
                    conflicts += 1
                    continue
                new_run = render(keep, final_separator(run))
                cs = count_prefix(text, s, len(names))
                if cs is None:
                    rows.append((run, new_run))
                else:
                    head = text[cs:s]
                    tok = re.match(r"(\d{1,3}|[A-Za-z]+)", head).group(1)
                    rows.append((head + run,
                                 decrement(tok, len(dropped))
                                 + head[len(tok):] + new_run))

    # Longest first: a short list literal must never consume part of a longer
    # one that carries a count.
    for old, new in sorted(rows, key=lambda r: -len(r[0])):
        add(old, new)

    bad = [o for o, _ in order if "\t" in o]
    if bad or conflicts:
        print(f"refusing: {len(bad)} tabbed, {conflicts} conflicting",
              file=sys.stderr)
        return 1

    print("# E-08 — bring the corpus's jurisdiction verdicts to the issuer's")
    print("# allow-list (config/availability.yaml, article 758780, E-06).")
    print("# Lifted from the corpus's raw bytes by scripts/make-availability-table.py.")
    print("# Order matters: corpus_edit.load_table preserves it, longest first.")
    print(f"# {len(order)} literals; `\\n` is a newline, `\\\\` a backslash.")
    for old, new in order:
        print(encode(old) + "\t" + encode(new))
    print(f"[table] {len(order)} entries, {skipped_solo} non-list occurrences "
          f"left to PROSE", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
