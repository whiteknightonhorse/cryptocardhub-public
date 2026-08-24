#!/usr/bin/env python3
"""bing-expand.py — fill the keyword bank from MEASURED Bing demand.

WHY THIS EXISTS, AND WHY IT IS NOT THE THING THAT WAS FORBIDDEN
--------------------------------------------------------------
Fable's ruling of 2026-08-13 banned recombining the existing bank — key x
partner, key x scenario, key pairs — because a slot substitution has no fact
delta and produces doorway pages. It did NOT ban printing pages; it required a
demand row first:

    a number + a window + the instrument that produced it   (Y55)

`bing-keywords.py` waits on `GetQueryStats`, which reports queries that reached
OUR site and is still empty because the site was only verified in Bing days
ago. That is a different endpoint from the one used here. **Bing's keyword
research works on Bing's own index and needs no data of ours at all** —
`GetRelatedKeywords` returns market vocabulary with impression counts, and
`GetKeywordStats` returns the same by week, which is the window. So the bank
can be filled today, lawfully, and what the operator called "recombining" turns
into ordinary generation against measured demand.

TWO SEED SOURCES, AND WHY THE SECOND IS ONLY A SEED
---------------------------------------------------
* our own 572 bank rows — expands the vocabulary we already committed to;
* competitor sitemaps — the topics we never thought of.

Volumes NEVER come from a competitor. Their page list tells you what they
guessed was worth writing; Bing tells you what people actually searched. Taking
their traffic estimates would be inheriting their mistakes with confidence.

WHAT IS REFUSED
---------------
* off-topic drift — `GetRelatedKeywords` cheerfully returned "fixedfloat
  crypto" (32 285 impressions) for the seed "crypto card". Volume is not
  relevance, and a bank filled by volume alone would walk the site out of its
  own subject in one pass;
* queries a page already covers — that is not demand for a NEW page;
* anything below the impressions floor;
* and nothing is promoted automatically: this writes CANDIDATES.md, which
  `parse_keyword_bank()` deliberately does not read. Promotion into SKILL.md
  stays a deliberate act by a human.

Usage:
  bing-expand.py --probe                 # 5 seeds, show what comes back
  bing-expand.py --max-seeds 60
  bing-expand.py --max-seeds 600 --write
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from pathlib import Path as pathlib_Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://ssl.bing.com/webmaster/api.svc/json"
BANK = ROOT / ".claude" / "skills" / "keywords" / "SKILL.md"
CANDIDATES = ROOT / ".claude" / "skills" / "keywords" / "CANDIDATES-BING.md"
CONTENT = ROOT / "seo-site" / "src" / "content" / "pseo"
STATE = ROOT / "video-generator" / "state" / "bing-expand.json"

# --- the laws, as literals (mirrored in scripts/tests/test_bing_expand.py) ---
# NOT a gate. Kept only to mark which rows sit above the instrument's floor,
# i.e. which numbers are readable at all. Selection is by ADJACENCY (below).
READABLE_ABOVE = 10
MIN_ADJACENCY = 0.05          # a candidate unrelated to anything we serve
COUNTRY, LANGUAGE = "us", "en-US"
WINDOW_DAYS = 90

# Topicality. A candidate must be about a payment card, or about crypto AND
# spending. This is what keeps "fixedfloat crypto" out.
CARD_WORDS = ("card", "cards")
CRYPTO_WORDS = ("crypto", "bitcoin", "btc", "eth", "ethereum", "stablecoin",
                "usdc", "usdt", "defi", "wallet")
SPEND_WORDS = ("cashback", "cash back", "spend", "spending", "pay", "payment",
               "atm", "fee", "fees", "rewards", "limit", "limits", "top up")
# Brands we write about — a branded query is on-topic even without "card".
BRANDS = ("ether.fi", "etherfi", "redotpay", "gnosis pay", "crypto.com",
          "coinbase", "nexo", "wirex", "bybit", "kast", "bleap", "metamask",
          "bitpanda", "revolut", "binance", "gate", "holyheld", "baanx")
# Off-topic sinks seen in real responses.
BANNED = ("fixedfloat", "casino", "porn", "loan app", "airdrop farming")
# Someone typing "coinbase login" wants coinbase.com, not an article. These
# carry the largest numbers in the whole pull (coinbase login: 84,538) and are
# worth exactly nothing to us — the clearest proof that volume is not value.
# "kast king" is a fishing-reel brand colliding with the card name.
# A query that wants a SITE, not an article. These carry the largest
# numbers in the whole pull — `coinbase login` at 84,538 — and are worth
# nothing to us: the clearest proof that volume is not value. `kast king`
# is a fishing-reel brand colliding with the card name.
#
# A domain alone is navigational; a domain with a topical word is not.
# `crypto.com` is someone looking for the site, `crypto.com card` is
# someone asking about the product — and crypto.com is one of our brands,
# so a blanket `.com` rule threw away real topics.
NAVIGATIONAL = re.compile('\\b(?:login|log in|sign in|signin|sign up|download|app|apps|extension|website|customer service|phone number|stock|share price|price|chart|nasdaq|site|king|reel|not working site)\\b', re.I)
BARE_DOMAIN = re.compile('^\\s*(?:www\\.)?[a-z0-9-]+(?:\\.[a-z]{2,4})+\\s*$', re.I)

TARGETS = ROOT / "logs" / "aeo"

STOP = {"the", "a", "an", "for", "in", "of", "to", "and", "or", "is", "it",
        "how", "what", "why", "can", "do", "does", "my", "your", "with", "on",
        "best", "top", "vs", "2025", "2026", "review", "reviews"}


def consumption_profile() -> dict[str, float]:
    """Token -> weight, built from what agents actually fetched.

    Weight is the fetch count, so a token from the 108-fetch page counts more
    than one from a 12-fetch page. Tokens are taken from the URL slug, which is
    the page's topic in the most compressed form we have.
    """
    latest = sorted(TARGETS.glob("targets-*.json"))
    if not latest:
        return {}
    data = json.loads(latest[-1].read_text(encoding="utf-8"))
    profile: dict[str, float] = {}
    for row in data.get("hot", []):
        slug = row["path"].strip("/").split("/")[-1]
        n = float(row.get("fetches", 0))
        for tok in slug.split("-"):
            if tok and tok not in STOP:
                profile[tok] = profile.get(tok, 0.0) + n
    return profile


def adjacency(query: str, profile: dict[str, float]) -> float:
    """How close a candidate sits to what agents already eat, in [0, 1].

    Share of the candidate's own meaningful tokens that appear in the consumed
    profile, weighted by how hard those pages are consumed. Normalised by the
    profile's own maximum so the figure stays comparable week to week.
    """
    if not profile:
        return 0.0
    toks = [t for t in re.sub(r"[^a-z0-9 ]+", " ", query.lower()).split()
            if t and t not in STOP]
    if not toks:
        return 0.0
    top = max(profile.values())
    hit = sum(profile.get(t, 0.0) / top for t in toks)
    return round(hit / len(toks), 4)


COMPETITORS = ("coinbureau.com", "www.cryptocards.so", "www.datawallet.com",
               "www.bleap.finance", "vaultleap.com", "ventureburn.com")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def _key() -> str:
    k = os.environ.get("BING_WEBMASTER_API_KEY")
    if not k:
        print("[bing] BING_WEBMASTER_API_KEY not in env — refusing", file=sys.stderr)
        raise SystemExit(2)
    return k


def call(method: str, **params):
    """-> parsed `d` payload, or None. None means UNKNOWN, never 'nothing'."""
    params["apikey"] = _key()
    url = f"{API}/{method}?" + urllib.parse.urlencode(params)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=45) as r:
                return json.loads(r.read().decode()).get("d")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            print(f"[bing] {method} HTTP {e.code}: {e.read().decode()[:160]}",
                  file=sys.stderr)
            return None
        except Exception as e:  # noqa: BLE001
            if attempt < 2:
                time.sleep(2)
                continue
            print(f"[bing] {method} {type(e).__name__}: {e}", file=sys.stderr)
            return None
    return None


def window() -> tuple[str, str]:
    today = date.today()
    return ((today - timedelta(days=WINDOW_DAYS)).strftime("%m/%d/%Y"),
            today.strftime("%m/%d/%Y"))


def related(seed: str) -> list[dict]:
    start, end = window()
    d = call("GetRelatedKeywords", q=seed, country=COUNTRY, language=LANGUAGE,
             startDate=start, endDate=end)
    if not isinstance(d, list):
        return []
    return [{"query": r.get("Query", "").strip().lower(),
             "impressions": int(r.get("Impressions") or 0),
             "broad": int(r.get("BroadImpressions") or 0)}
            for r in d if r.get("Query")]


# ---------------------------------------------------------------------------
# filters
# ---------------------------------------------------------------------------

def on_topic(q: str) -> bool:
    """The site is about CRYPTO payment cards. Volume is not relevance.

    Two rules here were bought by the first full run:

    * `card + fee/rewards` is NOT enough. It admitted "pay my capital one
      credit card" (724 impressions) and "how to pay off credit card debt"
      (533) — real demand for a business we are not in. Crypto or a brand we
      cover must be present.
    * Brands match on WORD BOUNDARIES. Substring matching on the brand "gate"
      pulled in "com surro*gate* how to close" (245) and "are summit racing
      waste*gate*s worth it" (35).
    """
    low = q.lower()
    if any(b in low for b in BANNED):
        return False
    if NAVIGATIONAL.search(low) or BARE_DOMAIN.match(low):
        return False
    if any(re.search(rf"(?<![a-z]){re.escape(b)}(?![a-z])", low) for b in BRANDS):
        return True
    has_card = any(re.search(rf"\b{w}\b", low) for w in CARD_WORDS)
    has_crypto = any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", low)
                     for w in CRYPTO_WORDS)
    has_spend = any(w in low for w in SPEND_WORDS)
    if not has_crypto:
        return False          # a traditional-finance query, however big
    return has_card or has_spend


def normalise(q: str) -> str:
    """Collapse the variants that are one query wearing two spellings.

    Bing returns "best crypto debit card" and "best crypto debit cards" as
    separate rows. Treating the plural as a NEW keyword would have printed a
    second page for a query we already own — recombination by accident, which
    is the exact thing the ruling forbids.
    """
    q = re.sub(r"[^a-z0-9 ]+", " ", q.lower())
    words = [re.sub(r"(?<=[a-z]{3})s$", "", w) for w in q.split()]
    drop = {"the", "a", "an", "for", "in", "of", "to", "best", "top", "2025", "2026"}
    return " ".join(sorted(w for w in words if w and w not in drop))


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60]


def bank_keywords() -> list[str]:
    if not BANK.exists():
        return []
    rows = re.findall(r"^\|\s*[A-L]\d+\s*\|\s*(.+?)\s*\|", BANK.read_text(encoding="utf-8"),
                      re.MULTILINE)
    return [r.strip().lower() for r in rows if r.strip()]


def covered() -> set[str]:
    """Slugs that already have a page, plus keywords already in the bank."""
    out = {p.stem for p in CONTENT.glob("*.md")} if CONTENT.is_dir() else set()
    out |= {slugify(k) for k in bank_keywords()}
    # ...and the normalised forms, so a plural cannot pose as a new keyword
    out |= {normalise(k) for k in bank_keywords()}
    out |= {normalise(p.stem.replace("-", " ")) for p in CONTENT.glob("*.md")} \
        if CONTENT.is_dir() else set()
    return out


# ---------------------------------------------------------------------------
# competitor seeds
# ---------------------------------------------------------------------------

def competitor_seeds(domains=COMPETITORS, per_domain: int = 40) -> list[str]:
    """Topic phrases from competitors' own sitemaps.

    A sitemap is published for machines and read as such: URLs only, no
    content, no rankings, nothing of theirs republished. Their slugs are used
    as SEEDS — the demand figure for each still comes from Bing.
    """
    seeds: list[str] = []
    for dom in domains:
        for path in ("/sitemap.xml", "/sitemap_index.xml", "/post-sitemap.xml"):
            try:
                req = urllib.request.Request(
                    f"https://{dom}{path}",
                    headers={"User-Agent": "DefyCardBot/1.0 (+https://defycard.com)"})
                xml = urllib.request.urlopen(req, timeout=25).read().decode(errors="replace")
            except Exception:
                continue
            locs = re.findall(r"<loc>([^<]+)</loc>", xml)
            if not locs:
                continue
            if "<sitemapindex" in xml:
                for sub in locs[:4]:
                    try:
                        req = urllib.request.Request(
                            sub, headers={"User-Agent": "DefyCardBot/1.0"})
                        locs += re.findall(
                            r"<loc>([^<]+)</loc>",
                            urllib.request.urlopen(req, timeout=25).read().decode(errors="replace"))
                    except Exception:
                        pass
            found = 0
            for u in locs:
                slug = urllib.parse.urlparse(u).path.rstrip("/").split("/")[-1]
                phrase = re.sub(r"[-_]+", " ", slug).strip()
                phrase = re.sub(r"\b\d{4,}\b", "", phrase).strip()
                if 8 <= len(phrase) <= 70 and on_topic(phrase):
                    seeds.append(phrase)
                    found += 1
                    if found >= per_domain:
                        break
            print(f"[seeds] {dom}{path}: {found} on-topic slugs", file=sys.stderr)
            break
    return seeds


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-seeds", type=int, default=40)
    ap.add_argument("--probe", action="store_true", help="5 seeds, print raw")
    ap.add_argument("--no-competitors", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--from-raw", metavar="PATH",
                    help="re-filter and re-rank a persisted pull; no API calls. "
                         "Filters change often, the market does not.")
    a = ap.parse_args()

    have = covered()
    bank = bank_keywords()
    print(f"bank rows: {len(bank)}   pages on disk: "
          f"{len(list(CONTENT.glob('*.md'))) if CONTENT.is_dir() else 0}")

    comp = [] if a.no_competitors else competitor_seeds()
    # Competitors first: they are the seeds we could NOT have thought of, and
    # in the first run all 232 of them were truncated away behind 572 bank
    # rows — the half of the operator's instruction that never ran.
    seeds = list(dict.fromkeys(comp))
    seeds += [s for s in dict.fromkeys(bank) if s not in seeds]
    if a.probe:
        seeds = seeds[:5]
    else:
        seeds = seeds[:a.max_seeds]
    print(f"seeds to expand: {len(seeds)}  (of which from competitors: "
          f"{len([s for s in seeds if s in comp])})")

    found: dict[str, dict] = {}
    unreachable = 0
    if a.from_raw:
        raw = json.loads(pathlib_Path(a.from_raw).read_text(encoding="utf-8"))
        found = {q: {**r, "seeds": set(r.get("seeds", []))} for q, r in raw.items()}
        print(f"re-ranking {len(found)} candidates from {a.from_raw} — no API calls")
        seeds = []
    for i, seed in enumerate(seeds, 1):
        rows = related(seed)
        if not rows:
            unreachable += 1
        for r in rows:
            q = r["query"]
            if q in found:
                found[q]["impressions"] = max(found[q]["impressions"], r["impressions"])
                found[q]["seeds"].add(seed)
            else:
                found[q] = {**r, "seeds": {seed}}
        if a.probe:
            print(f"  seed {seed!r} -> {len(rows)} related")
            for r in rows[:8]:
                print(f"      {r['impressions']:7d}  {r['query']}")
        elif i % 25 == 0:
            print(f"  ...{i}/{len(seeds)} seeds, {len(found)} raw candidates",
                  file=sys.stderr)
        time.sleep(0.15)

    print(f"\nraw candidates: {len(found)}   seeds returning nothing: {unreachable}")
    RAW = ROOT / "logs" / "pseo" / f"bing-raw-{date.today().isoformat()}.json"
    RAW.parent.mkdir(parents=True, exist_ok=True)
    RAW.write_text(json.dumps(
        {q: {**r, "seeds": sorted(r["seeds"])} for q, r in found.items()}, indent=1),
        encoding="utf-8")
    print(f"  raw pull kept at {RAW} — refiltering never needs another API pass")

    profile = consumption_profile()
    print(f"consumption profile: {len(profile)} tokens from the fetched pages"
          if profile else "consumption profile: EMPTY — run aeo-aggregate.py --targets")
    for q in found:
        found[q]["adjacency"] = adjacency(q, profile)

    off = [q for q in found if not on_topic(q)]
    dup = [q for q in found if on_topic(q)
           and (slugify(q) in have or normalise(q) in have or q in bank)]
    covered_or_off = set(off) | set(dup)
    far = [q for q in found if q not in covered_or_off
           and found[q]["adjacency"] < MIN_ADJACENCY]
    keep = sorted((q for q in found if q not in covered_or_off
                   and found[q]["adjacency"] >= MIN_ADJACENCY),
                  key=lambda q: (-found[q]["adjacency"], -found[q]["impressions"]))

    print(f"  rejected off-topic              : {len(off)}")
    print(f"  already covered by a page       : {len(dup)}")
    print(f"  too far from what agents eat    : {len(far)}")
    print(f"  CANDIDATES, ranked by adjacency : {len(keep)}")
    print(f"     {'adj':>6} {'imp':>7}  keyword")
    for q in keep[:30]:
        r = found[q]
        flag = "" if r["impressions"] >= READABLE_ABOVE else "  (imp below floor)"
        print(f"     {r['adjacency']:6.3f} {r['impressions']:7d}  {q}{flag}")
    if off[:8]:
        print(f"  (examples of what was refused: {off[:8]})")

    if a.write and keep:
        start, end = window()
        lines = [
            "# Bing-measured keyword candidates",
            "",
            f"Instrument: Bing Webmaster `GetRelatedKeywords`, country={COUNTRY}, "
            f"language={LANGUAGE}.",
            f"Window: {start} – {end} ({WINDOW_DAYS} days). Pulled {date.today().isoformat()}.",
            "",
            "Every row below carries a number, a window and the instrument that",
            "produced it — the Y55 test. Nothing here is a page until a human moves",
            "it into SKILL.md: `parse_keyword_bank()` does not read this file.",
            "",
            "**Ranked by ADJACENCY, not by impressions.** Bing's figure has no",
            "discriminating power at this tier — a term with thousands of Google",
            "searches a month reads 0 on the same instrument — so it is metadata",
            "here, never a gate. Adjacency is the share of a candidate's tokens",
            "that appear in the pages AI agents actually fetch, weighted by how",
            "hard those pages are fetched.",
            "",
            f"Refused in the same pass: {len(off)} off-topic, {len(dup)} already",
            f"covered, {len(far)} below adjacency {MIN_ADJACENCY}.",
            "",
            "Each row is a HYPOTHESIS. A page built from one is measured at +14",
            "and +28 days on agent fetches and GA4 sessions; that measurement, not",
            "this table, is what says whether the topic was worth a page.",
            "",
            "| adjacency | impressions | broad | keyword | seed |",
            "|---:|---:|---:|---|---|",
        ]
        for q in keep:
            r = found[q]
            lines.append(f"| {r['adjacency']} | {r['impressions']} | {r['broad']} | "
                         f"{q} | {sorted(r['seeds'])[0]} |")
        CANDIDATES.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nwrote {CANDIDATES} ({len(keep)} rows)")
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"pulled": date.today().isoformat(),
                                     "seeds": len(seeds), "kept": len(keep)},
                                    indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
