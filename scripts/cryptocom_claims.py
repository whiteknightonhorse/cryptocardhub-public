#!/usr/bin/env python3
"""T-0142-14 — Crypto.com on the 7 fallback pages: measured, then judged.

Two things this module does, and does NOT do more than:

1. Reads config/facts/cryptocom.yaml (the measurement — availability per
   jurisdiction, current tiers/cashback/stake per product) and computes, per
   page, whether the fallback CTA (config/affiliates.yaml `cryptocom`, routed
   to by config/availability.yaml `etherfi.fallback_partner`) should stay or
   be rerouted, per ruling-3 Q9 point 2: "available" keeps the CTA; "not
   available" or "not stated" reroutes it.

2. Scans the 7 pages (unedited — this task's own acceptance line: "страницы в
   этой задаче не правятся") for the specific stale-claim keyword patterns
   named in the task (Ruby, Jade, "% cashback", "CRO stake", "$4k") and flags
   any such claim whose sentence does not also contain a numeric token backed
   by a `current`-status fact for that page's jurisdiction. This is a literal
   substring check, not NLP — see cryptocom.yaml's own header for why nearly
   every claim on these pages is stale (two different card products, one
   describes a discontinued USD pricing scheme neither product uses today).

Neither step edits a page. The claim list is handed to a future task
(corpus_edit.py --table ... --expect N), named in this task's own acceptance
line — building that table is out of scope here.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import facts_lib as fl  # noqa: E402

FACTS_PATH = ROOT / "config" / "facts" / "cryptocom.yaml"
CONTENT = ROOT / "seo-site" / "src" / "content" / "pseo"
OUT_DIR = ROOT / "logs" / "partners"

# slug -> (jurisdiction name as it appears in cryptocom.yaml's applies_to)
PAGE_JURISDICTIONS: dict[str, str] = {
    "best-crypto-card-india": "India",
    "best-crypto-card-turkey": "Turkey",
    "best-crypto-card-netherlands": "Netherlands",
    "best-crypto-card-georgia": "Georgia",
    "best-crypto-card-arizona": "Arizona",
    "best-crypto-card-nevada": "Nevada",
    "best-crypto-card-washington": "Washington",
}
VALID_AVAILABILITY = {"available", "not available", "not stated"}

# The 5 stale-claim keyword classes named in the task, as regexes. Each
# capture group (if any) is unused — presence in a sentence is what matters,
# the CURRENT-token check below decides whether the sentence is backed.
CLAIM_PATTERNS = {
    "ruby_tier": re.compile(r"\bRuby\b", re.IGNORECASE),
    "jade_tier": re.compile(r"\bJade\b", re.IGNORECASE),
    "pct_cashback": re.compile(r"\d+(?:\.\d+)?\s?%\s*(?:cash ?back|back)", re.IGNORECASE),
    "cro_stake": re.compile(r"CRO\s+stak(?:e|ing)", re.IGNORECASE),
    "four_k_stake": re.compile(r"\$\s?4[,.]?0{0,3}k|\$\s?4,000", re.IGNORECASE),
}


def _split_sentences(text: str) -> list[str]:
    # Frontmatter/prose split good enough for this task: sentences never
    # need to survive round-trip, only to bound a claim's context window.
    # NOTE (ruling-2): must be `\n` (an actual newline char, which the regex
    # engine matches), not `\\n` (two literal characters, backslash+n) — the
    # latter never matches a real line break and merges whole bullet lists
    # (which don't end in .!?) into one oversized, truncated "sentence".
    return re.split(r"(?<=[.!?])\s+|\n", text)


def load_facts() -> list[dict[str, Any]]:
    return fl.load(FACTS_PATH)


def availability_by_jurisdiction(facts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """jurisdiction name -> its availability fact (value/url/quote)."""
    out: dict[str, dict[str, Any]] = {}
    for f in facts:
        if f["class"] != "availability":
            continue
        at = f.get("applies_to") or {}
        names = at.get("jurisdictions") or ([at["jurisdiction"]] if "jurisdiction" in at else [])
        for name in names:
            out[name] = f
    return out


def current_tokens_by_jurisdiction(facts: list[dict[str, Any]]) -> dict[str, set[str]]:
    """jurisdiction name -> set of literal numeric tokens ("2%", "EUR450", ...)
    backed by a `current`-status rate/fee fact scoped to that jurisdiction.
    Empty set means "nothing to check a claim against" — every claim on that
    page's sentences is unbacked by construction, which is correct for a
    jurisdiction whose own availability is not "available"."""
    out: dict[str, set[str]] = {}
    for f in facts:
        if f["class"] not in ("rate", "fee") or f["status"] != "current":
            continue
        at = f.get("applies_to") or {}
        names = at.get("jurisdictions") or ([at["jurisdiction"]] if "jurisdiction" in at else [])
        tokens = _tokens_for_fact(f)
        for name in names:
            out.setdefault(name, set()).update(tokens)
    return out


def _tokens_for_fact(fact: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    value, unit = fact.get("value"), fact.get("unit")
    if isinstance(value, (int, float)) and unit == "%":
        v = int(value) if float(value).is_integer() else value
        tokens.add(f"{v}%")
    fee = (fact.get("applies_to") or {}).get("fee") or ""
    for m in re.finditer(r"(?:EUR|\$)\d[\d,]*", fee):
        tokens.add(m.group(0))
    return tokens


def _token_in_sentence(tok: str, sentence: str) -> bool:
    """Boundary-aware: "$4" must not match inside "$400", "$4,000", or "$4k"
    (a shorter fee token is a real substring of a longer stale-claim number),
    and "5%" must not match inside "15%"/"50%". A plain `tok in sentence`
    check let the "$4" token from a "$4.99/month" fee silently back every
    "$400"/"$4,000"/"$4k" stale claim on 4 US pages (T-0142-14 ruling-1).

    T-0142-16 REJECT: that same trailing check also rejected a COMPLETE,
    correct token ("4.5%") when ordinary sentence punctuation follows it
    ("...confirmed rate at 4.5%, but Crypto.com...") -- a bare comma is not
    a continuation of the number, only "<comma><digit>" is (the thousands-
    separator case "$4,000" the original rule exists for). Splitting the
    trailing check into "no digit" + "no comma-then-digit" + "no k" keeps
    both: "$4" still fails to back "$4,000"/"$4,99" (comma-then-digit), but
    "4.5%" now backs "...at 4.5%, but..." (comma-then-space)."""
    return re.search(
        rf"(?<![\d.,]){re.escape(tok)}(?!\d)(?!,\d)(?!k)", sentence
    ) is not None


def reroute_slugs(facts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """slug -> {jurisdiction, value, reason} for every page whose jurisdiction
    is NOT "available" (ruling-3 Q9.2: "not available" or "not stated" both
    reroute)."""
    avail = availability_by_jurisdiction(facts)
    out: dict[str, dict[str, Any]] = {}
    for slug, jurisdiction in PAGE_JURISDICTIONS.items():
        fact = avail.get(jurisdiction)
        value = fact["value"] if fact else "not stated"
        if value != "available":
            out[slug] = {"jurisdiction": jurisdiction, "value": value,
                        "source_url": fact["source_url"] if fact else ""}
    return out


_CRYPTOCOM_MENTION = re.compile(r"crypto\.?com|\bCRO\b", re.IGNORECASE)
# ruby/jade/CRO-stake/$4k are Crypto.com's own vocabulary (no other card in
# this corpus uses CRO or those tier names); "% cashback" alone is generic
# to every card on these pages (ether.fi, RedotPay, Bybit all use it), so
# that one pattern additionally requires a same-sentence Crypto.com mention
# or it floods the report with claims about OTHER cards. "CRO" itself counts
# as a mention (ruling-2): a sentence can say "$400 CRO investment unlocks
# 5% cashback" without the literal word "Crypto.com" and still be a
# Crypto.com claim — CRO is that brand's own token, named nowhere else here.
_REQUIRES_BRAND_MENTION = {"pct_cashback"}


def scan_page_claims(slug: str, text: str, current_tokens: set[str]) -> list[dict[str, str]]:
    """Every sentence in `text` matching one of CLAIM_PATTERNS that does NOT
    also contain one of `current_tokens` -> an unbacked-claim row."""
    findings: list[dict[str, str]] = []
    for sentence in _scan_units(text):
        matched = [name for name, rx in CLAIM_PATTERNS.items() if rx.search(sentence)]
        matched = [name for name in matched
                   if name not in _REQUIRES_BRAND_MENTION or _CRYPTOCOM_MENTION.search(sentence)]
        if not matched:
            continue
        backed = any(_token_in_sentence(tok, sentence) for tok in current_tokens)
        if backed:
            continue
        snippet = sentence.strip().replace("\n", " ")[:180]
        findings.append({"slug": slug, "keywords": ",".join(matched), "snippet": snippet})
    return findings


# ---------------------------------------------------------------------------
# G46 (T-0142-16, ruling-6): the same "unbacked money claim" law, generalized
# from Crypto.com-only to every brand DefyCard tracks on these 7 pages. A
# brand with its own facts file (cryptocom/etherfi/plasma) can be backed by a
# `current` rate/fee fact scoped to the page's own jurisdiction, same as
# above. RedotPay/Bybit/Wirex have no facts file at all -- ANY money figure
# next to one of those three names is unbacked by construction, because
# there is no fact that could ever back it.
# ---------------------------------------------------------------------------
ETHERFI_FACTS_PATH = ROOT / "config" / "facts" / "etherfi.yaml"
PLASMA_FACTS_PATH = ROOT / "config" / "facts" / "plasma.yaml"

BRAND_FACTS_PATHS: dict[str, Path] = {
    "cryptocom": FACTS_PATH,
    "etherfi": ETHERFI_FACTS_PATH,
    "plasma": PLASMA_FACTS_PATH,
}

_FRONTMATTER_ARRAY_RE = re.compile(
    r'^(?:snapshot_bullets|what_to_watch|bottom_line):\s*(\[.*\])\s*$', re.M)
_FRONTMATTER_FAQ_RE = re.compile(r'^faq:\s*(\[.*\])\s*$', re.M)
_FRONTMATTER_SCHEMA_RE = re.compile(r'^(?:schema_article|schema_faq):.*$', re.M)


def _scan_units(text: str) -> list[str]:
    """G46's sentence-like units. `_split_sentences` alone merges an entire
    frontmatter JSON array (snapshot_bullets/what_to_watch/bottom_line/faq)
    into ONE giant "sentence", because those bullets sit on a single physical
    line and most don't end in `.!?` -- the exact bug class Y49 named for
    un-split bullet LISTS in the page body, one layer up in the frontmatter.
    Parsed as data instead: one bullet, one FAQ question, one FAQ answer is
    one unit each. `schema_article`/`schema_faq` are dropped rather than
    scanned raw -- they are a JSON-LD mirror of `faq`/`description`, already
    scanned there; scanning the raw blob would just re-flag the same claim
    under a worse, truncated snippet. Everything else (the markdown body,
    any other frontmatter line) falls back to `_split_sentences`."""
    units: list[str] = []
    rest = text
    for rx in (_FRONTMATTER_ARRAY_RE,):
        for m in rx.finditer(text):
            rest = rest.replace(m.group(0), "")
            try:
                items = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
            units.extend(i for i in items if isinstance(i, str))
    for m in _FRONTMATTER_FAQ_RE.finditer(text):
        rest = rest.replace(m.group(0), "")
        try:
            items = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in ("q", "a"):
                if isinstance(item.get(key), str):
                    units.append(item[key])
    rest = _FRONTMATTER_SCHEMA_RE.sub("", rest)
    units.extend(_split_sentences(rest))
    return units


# Brand -> facts-file key, or None for "no facts file exists, ever". Order
# matters only in that every entry is checked per sentence (a sentence can
# name more than one brand and produce more than one finding).
BRAND_PATTERNS: list[tuple[str | None, re.Pattern[str]]] = [
    ("cryptocom", _CRYPTOCOM_MENTION),
    ("etherfi", re.compile(r"ether\.?fi", re.IGNORECASE)),
    ("plasma", re.compile(r"\bplasma\b", re.IGNORECASE)),
    (None, re.compile(r"redotpay|bybit|wirex", re.IGNORECASE)),
]

# A money figure: percent, dollar, or euro amount -- the three forms named
# in ruling-6's G46 acceptance row, literally ("%", "$", "EUR|€").
MONEY_RE = re.compile(r"\d+(?:\.\d+)?\s?%|\$\s?\d|(?:EUR|€)\s?\d", re.IGNORECASE)


def load_brand_facts() -> dict[str, list[dict[str, Any]]]:
    return {brand: fl.load(path) for brand, path in BRAND_FACTS_PATHS.items()}


def scan_all_brand_claims(slug: str, text: str, jurisdiction: str,
                           brand_facts: dict[str, list[dict[str, Any]]]) -> list[dict[str, str]]:
    """Every sentence naming a tracked brand next to a money figure that is
    not backed by a `current` rate/fee fact scoped to `jurisdiction`. A brand
    absent from `brand_facts` (or mapped to `None` in BRAND_PATTERNS) can
    never be backed -- reads `text` fresh each call, never the built dist
    (numbers live in the source .md; links are G12/G47/G48's job, on dist)."""
    tokens_by_brand = {
        brand: current_tokens_by_jurisdiction(facts).get(jurisdiction, set())
        for brand, facts in brand_facts.items()
    }
    findings: list[dict[str, str]] = []
    for sentence in _scan_units(text):
        if not MONEY_RE.search(sentence):
            continue
        snippet = sentence.strip().replace("\n", " ")[:180]
        for brand, rx in BRAND_PATTERNS:
            if not rx.search(sentence):
                continue
            if brand is None:
                findings.append({"slug": slug, "brand": "redotpay/bybit/wirex", "snippet": snippet})
                continue
            tokens = tokens_by_brand.get(brand, set())
            if not any(_token_in_sentence(tok, sentence) for tok in tokens):
                findings.append({"slug": slug, "brand": brand, "snippet": snippet})
    return findings


def scan_g46_all_pages(brand_facts: dict[str, list[dict[str, Any]]] | None = None) -> list[dict[str, str]]:
    """G46 over the 7 literal fallback pages, reading each page's own source
    .md fresh (never cached, never the built dist)."""
    brand_facts = brand_facts if brand_facts is not None else load_brand_facts()
    out: list[dict[str, str]] = []
    for slug, jurisdiction in PAGE_JURISDICTIONS.items():
        path = CONTENT / f"{slug}.md"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        out.extend(scan_all_brand_claims(slug, text, jurisdiction, brand_facts))
    return out


def validate(facts: list[dict[str, Any]]) -> None:
    """The law this task's own mutations must prove red:
    - every fact carries `expires` (facts_lib only requires it for
      promo/beta; this task requires it universally, like plasma.yaml).
    - every availability fact whose value is "available"/"not available"
      (a definitive yes/no, T-0142-13's own rule) carries a non-empty
      source_url and source_quote; "not stated" needs neither.
    """
    for f in facts:
        if not f.get("expires"):
            raise fl.FactError(f"{f['id']}: missing expires")
        if f["class"] != "availability":
            continue
        if f["value"] not in VALID_AVAILABILITY:
            raise fl.FactError(f"{f['id']}: value {f['value']!r} not in {VALID_AVAILABILITY}")
        if f["value"] in ("available", "not available"):
            if not f.get("source_url"):
                raise fl.FactError(f"{f['id']}: {f['value']} fact has no source_url")
            if not f.get("source_quote"):
                raise fl.FactError(f"{f['id']}: {f['value']} fact has no source_quote")


def render_markdown(report_date: str, facts: list[dict[str, Any]]) -> str:
    avail = availability_by_jurisdiction(facts)
    tokens = current_tokens_by_jurisdiction(facts)
    reroutes = reroute_slugs(facts)

    lines = [f"# Crypto.com on the 7 fallback pages — measured {report_date}", ""]
    lines.append("## Availability")
    lines.append("")
    lines.append("| Jurisdiction | Value | Source |")
    lines.append("|---|---|---|")
    for slug, jurisdiction in PAGE_JURISDICTIONS.items():
        fact = avail.get(jurisdiction)
        value = fact["value"] if fact else "not stated"
        url = fact["source_url"] if fact else ""
        lines.append(f"| {jurisdiction} ({slug}) | {value} | {url} |")
    lines.append("")

    lines.append("## Unbacked money claims (pages NOT edited — see corpus_edit next task)")
    lines.append("")
    total_claims = 0
    for slug, jurisdiction in PAGE_JURISDICTIONS.items():
        path = CONTENT / f"{slug}.md"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        current = tokens.get(jurisdiction, set())
        findings = scan_page_claims(slug, text, current)
        total_claims += len(findings)
        if not findings:
            continue
        lines.append(f"### {slug}")
        lines.append("")
        for f in findings:
            lines.append(f"- `{f['keywords']}`: {f['snippet']!r}")
        lines.append("")
    lines.append(f"**{total_claims} unbacked claim(s) found across {len(PAGE_JURISDICTIONS)} pages.**")
    lines.append("")

    lines.append("## REROUTE")
    lines.append("")
    if not reroutes:
        lines.append("None — every page's jurisdiction is \"available\".")
    for slug, info in sorted(reroutes.items()):
        lines.append(
            f"REROUTE {slug}: crypto.com CTA must go, LAW 1 needs a card "
            f"from T-0142-13 ({info['jurisdiction']}: {info['value']}) — "
            f"see logs/partners/r6-candidates-*.md for candidates")
    lines.append("")

    lines.append("## G46 — every tracked brand, all 7 pages (money figure without a current fact)")
    lines.append("")
    g46 = scan_g46_all_pages()
    if not g46:
        lines.append("None — every money figure on all 7 pages is backed by a current fact.")
    else:
        for f in g46:
            lines.append(f"- {f['slug']} [{f['brand']}]: {f['snippet']!r}")
    lines.append("")
    lines.append(f"**{len(g46)} G46 unbacked claim(s) found across {len(PAGE_JURISDICTIONS)} pages.**")
    lines.append("")
    return "\n".join(lines) + "\n"


def write_outputs(report_date: str | None = None) -> tuple[Path, Path]:
    facts = load_facts()
    validate(facts)
    report_date = report_date or date.today().isoformat()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md_path = OUT_DIR / f"cryptocom-claims-{report_date}.md"
    json_path = OUT_DIR / f"cryptocom-claims-{report_date}.json"
    md_path.write_text(render_markdown(report_date, facts), encoding="utf-8")
    json_path.write_text(json.dumps({
        "date": report_date,
        "availability": {j: f["value"] for j, f in availability_by_jurisdiction(facts).items()},
        "reroute": reroute_slugs(facts),
        "g46_unbacked_claims": len(scan_g46_all_pages()),
    }, indent=2), encoding="utf-8")
    return md_path, json_path


if __name__ == "__main__":
    md_path, json_path = write_outputs()
    print(f"wrote {md_path}")
    print(f"wrote {json_path}")
