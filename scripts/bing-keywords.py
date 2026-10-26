#!/usr/bin/env python3
"""Y62 — build keyword-bank candidates from MEASURED Bing search demand.

Why Bing and not GSC: ChatGPT's search runs on Bing's index, ChatGPT is 97% of
this site's answer-engine fetches, and it converts at 32.3% against 0.56% for
direct. Bing Webmaster Tools is therefore not a second-best substitute here —
it is the closest instrument to the channel that actually sells.

The law this obeys (Fable, Y55 and the keyword-bank ruling):

  * Nothing enters the bank without a number, a window and the instrument that
    produced it. A query that merely EXISTS is a candidate, never a row.
  * A cluster must have a path to money: a partner reachable in the page's
    geography. Volume without a partner is traffic we cannot sell.
  * Queries that already have a dedicated page are not demand for a new page —
    that was the whole lesson of the 404 sweep, where "demand" turned out to be
    crawlers following links we had invented ourselves.

FABLE 2026-09-14 (FABLE_DEFYCARD_ANSWER.md, ЧАСТЬ A1/A4 step 1) — the bug this
round fixed: `GetQueryStats` does not return one row per query. It returns one
row per (query, week) — 07.09 wrote "3 candidates" that were the SAME query
("binance card usage for everyday purchases") in three different weekly rows
(82/56/48 impressions = one query, 215 total). `collect()` now groups by
`normalize(query)` and sums BEFORE the threshold is applied, so the header's
own words ("below 10 impressions") describe what the code actually tests.

Every uncovered query is also checked against `GetQueryPageStats` — "which of
OUR pages does Bing already show for this query" — before it can become a
brand-new page. A query where we already rank is not demand for a new URL, it
is a task to strengthen the page that already ranks (A2.3: the six 2026-09-01
promotions did exactly the opposite and produced five near-duplicates of
existing pages, one cannibalising a converting page's own impressions). So
CANDIDATES.md now carries two blocks:

  ## New-page candidates    — sum >= MIN_IMPRESSIONS, seen in >= 2 weekly
                               rows, Bing shows NONE of our pages for it, and
                               its slug is not a near-duplicate (Jaccard >=
                               0.5) of an existing one.
  ## Update candidates      — Bing already shows one of our pages for it,
                               regardless of the new-page floor: strengthening
                               an existing URL is lower-risk than the floor
                               that protects the decision to mint a new one.

Writes CANDIDATES.md, which `parse_keyword_bank()` deliberately does NOT read.
Promotion into SKILL.md is a separate, deliberate act (promote-candidates.py).
Every classified query also gets one line in `logs/pseo/bing-candidates.jsonl`
so the funnel (new / update / near_duplicate / below_floor / single_week) is
auditable, not just the surviving candidates.

Usage:
  python3 scripts/bing-keywords.py            # report what Bing has
  python3 scripts/bing-keywords.py --write     # write CANDIDATES.md + funnel log
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
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from slug_lib import slugify  # noqa: E402 -- LAW #ONE-PLACE canonical slugify, must match the writer
SITE = "https://defycard.com"
API = "https://ssl.bing.com/webmaster/api.svc/json"
CANDIDATES = ROOT / ".claude" / "skills" / "keywords" / "CANDIDATES.md"
FUNNEL_LOG = ROOT / "logs" / "pseo" / "bing-candidates.jsonl"

# A query is worth a page only if it reads like someone deciding or buying.
# Literal list so the test can pin it (Y49).
INTENT_TOKENS = ("card", "cashback", "fee", "fees", "vs", "review", "best",
                 "buy", "apply", "kyc", "limit", "cheapest", "compare")

MIN_IMPRESSIONS = 10  # below this a single crawler or a curious human is indistinguishable from demand
MIN_WEEKS_SEEN = 2    # a query in only one weekly row has not yet shown it is a repeat behaviour (A2.2)
NEAR_DUPLICATE_JACCARD = 0.5  # token-overlap floor above which a "new" slug is really a rewrite of one we have
PAGE_STATS_DELAY = 0.35   # seconds between GetQueryPageStats calls — cuts ThrottleHost from ~1-in-5 to near 0
THROTTLE_RETRIES = 4      # 1s, 2s, 4s, 8s backoff before giving up on a single query


class BingMeasurementFailed(Exception):
    """Bing answered but the call could not be completed (e.g. ThrottleHost).

    This is NOT "Bing shows no page for this query" — a caller that treated
    it that way would misclassify an already-ranked query as brand-new demand
    (exactly A2.3's defect, one layer down: 2026-09-14's first live run hit
    ThrottleHost on ~1-in-5 of 270 GetQueryPageStats calls with no retry and
    no distinct failure state, so a genuinely-ranked query fell through empty
    `ranked_by` into `new_candidates`/`near_duplicate` as if Bing had truly
    shown nothing)."""


def _get(method: str, **params) -> object:
    key = os.environ.get("BING_WEBMASTER_API_KEY")
    if not key:
        sys.exit("BING_WEBMASTER_API_KEY is not in the environment — it lives in ~/.env")
    q = urllib.parse.urlencode({"apikey": key, "siteUrl": SITE, **params})
    url = f"{API}/{method}?{q}"
    last_body = ""
    for attempt in range(THROTTLE_RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.loads(r.read().decode()).get("d")
        except urllib.error.HTTPError as e:
            last_body = e.read().decode(errors="replace")
            if "Throttle" not in last_body or attempt == THROTTLE_RETRIES - 1:
                raise BingMeasurementFailed(f"{method} {params}: {e.code} {last_body}") from e
            time.sleep(2 ** attempt)
    raise BingMeasurementFailed(f"{method} {params}: exhausted retries: {last_body}")


def existing_slugs() -> set[str]:
    d = ROOT / "seo-site" / "src" / "content" / "pseo"
    return {p.stem for p in d.glob("*.md")} if d.is_dir() else set()


def is_sellable(query: str) -> bool:
    """Intent predicate. Volume alone is not a reason to write a page."""
    words = query.lower().split()
    return any(t in words or any(w.startswith(t) for w in words) for t in INTENT_TOKENS)


def normalize(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace — the SAME key used to
    aggregate Bing's query x week rows and to dedup against the bank
    (promote-candidates.py imports this rather than keeping a second copy)."""
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def jaccard(a: set[str], b: set[str]) -> float:
    u = a | b
    return (len(a & b) / len(u)) if u else 1.0


def nearest_duplicate(slug: str, existing: set[str], threshold: float = NEAR_DUPLICATE_JACCARD) -> str | None:
    """-> the existing slug this one overlaps most with, if that overlap is a
    near-duplicate (>= threshold). None if the slug is genuinely distinct.

    A2.3: five of the six 2026-09-01 promotions were exactly this — a slug one
    or two tokens away from a page already on disk, printed as if it were new.
    """
    toks = set(slug.split("-"))
    best_slug, best_score = None, 0.0
    for s in existing:
        score = jaccard(toks, set(s.split("-")))
        if score > best_score:
            best_slug, best_score = s, score
    return best_slug if best_score >= threshold else None


def query_page_stats(query: str) -> list[dict]:
    """Which of OUR pages Bing already shows for this query, if any.

    -> [{"path": "/reviews/binance-card-review/", "impressions": 171,
         "clicks": 0, "position": 6.0}, ...], sorted by impressions desc.
    Empty list = Bing shows no page of ours for this query at all.

    Raises BingMeasurementFailed if the call itself could not be completed
    (e.g. ThrottleHost after retries) — that is NOT the same as an empty
    list and callers must not treat it as one (2026-09-14 live-run bug: the
    field read here was also wrong — GetQueryPageStats puts the ranking
    PAGE'S URL in the response's `Query` field, confirmed against a live
    call; `Page`/`Url` are always absent, so every result silently came back
    empty and the Update channel this step exists to build was permanently
    empty even when the call succeeded).
    """
    rows = _get("GetQueryPageStats", query=query) or []
    time.sleep(PAGE_STATS_DELAY)
    out = []
    for r in rows:
        page = (r.get("Query") or r.get("Page") or r.get("Url") or "").strip()
        if not page:
            continue
        out.append({
            "path": urllib.parse.urlparse(page).path or page,
            "impressions": int(r.get("Impressions") or 0),
            "clicks": int(r.get("Clicks") or 0),
            "position": float(r.get("AvgImpressionPosition") or r.get("Position") or 0),
        })
    out.sort(key=lambda x: -x["impressions"])
    return out


def collect() -> dict:
    rows = _get("GetQueryStats") or []
    have = existing_slugs()
    today = date.today().isoformat()

    # --- step 1: group query x week rows by normalized query, SUM first ----
    agg: dict[str, dict] = {}
    for r in rows:
        q = (r.get("Query") or "").strip()
        if not q:
            continue
        norm = normalize(q)
        a = agg.setdefault(norm, {"query": q, "impressions": 0, "clicks": 0, "weeks": set()})
        a["impressions"] += int(r.get("Impressions") or 0)
        a["clicks"] += int(r.get("Clicks") or 0)
        wk = (r.get("Date") or "").strip()
        if wk:
            a["weeks"].add(wk)

    covered = off_intent = near_duplicate = below_floor = single_week = measurement_failed = 0
    new_candidates: list[dict] = []
    update_candidates: list[dict] = []
    funnel: list[dict] = []

    for norm, a in agg.items():
        q, impressions, clicks = a["query"], a["impressions"], a["clicks"]
        slug = slugify(q)
        weeks_seen = len(a["weeks"])

        if slug in have:
            covered += 1
            continue
        if not is_sellable(q):
            off_intent += 1
            continue

        try:
            ranked = query_page_stats(q)
        except BingMeasurementFailed as e:
            # Never guess. An unmeasured query is NOT "Bing shows nothing" —
            # treating it that way is exactly A2.3's defect: a query Bing
            # already ranks us for would fall into new_candidates/
            # near_duplicate as if it were untouched demand.
            measurement_failed += 1
            funnel.append({"event": "refused", "reason": "measurement_failed",
                           "query": q, "error": str(e), "fetched_at": today})
            continue
        base = {"query": q, "impressions": impressions, "clicks": clicks,
               "slug": slug, "weeks_seen": weeks_seen}

        if ranked:
            update_candidates.append({**base, "ranked_by": ranked})
            funnel.append({"event": "update", "reason": "ranked_by",
                           "query": q, "fetched_at": today})
            continue

        dup = nearest_duplicate(slug, have)
        if dup:
            near_duplicate += 1
            funnel.append({"event": "refused", "reason": "near_duplicate",
                           "query": q, "matches": dup, "fetched_at": today})
            continue
        if impressions < MIN_IMPRESSIONS:
            below_floor += 1
            funnel.append({"event": "refused", "reason": "below_floor",
                           "query": q, "impressions": impressions, "fetched_at": today})
            continue
        if weeks_seen < MIN_WEEKS_SEEN:
            single_week += 1
            funnel.append({"event": "refused", "reason": "single_week",
                           "query": q, "weeks_seen": weeks_seen, "fetched_at": today})
            continue

        new_candidates.append(base)
        funnel.append({"event": "new", "query": q, "fetched_at": today})

    new_candidates.sort(key=lambda c: -c["impressions"])
    update_candidates.sort(key=lambda c: -c["impressions"])

    return {"fetched_at": datetime.now(timezone.utc).isoformat(),
            "queries_returned": len(agg), "covered": covered,
            "off_intent": off_intent, "near_duplicate": near_duplicate,
            "below_floor": below_floor, "single_week": single_week,
            "measurement_failed": measurement_failed,
            "new_candidates": new_candidates, "update_candidates": update_candidates,
            "funnel": funnel, "window_end": today}


def render(data: dict) -> str:
    L = ["# Keyword candidates — measured demand, not yet bank rows", "",
         "Generated by `scripts/bing-keywords.py`. **`parse_keyword_bank()` does not read this",
         "file.** A candidate becomes a bank row only by deliberate promotion into SKILL.md,",
         "carrying its number, window and instrument — a query that merely exists is not demand.",
         "",
         f"- fetched: `{data['fetched_at']}`",
         f"- queries returned by Bing (unique, summed across weekly rows): **{data['queries_returned']}**",
         f"- already covered by a page: {data['covered']}",
         f"- off transactional intent: {data['off_intent']}",
         f"- near-duplicate of an existing page: {data['near_duplicate']}",
         f"- below {MIN_IMPRESSIONS} impressions (summed over the window): {data['below_floor']}",
         f"- seen in only one weekly row (< {MIN_WEEKS_SEEN} weeks): {data['single_week']}",
         f"- GetQueryPageStats could not be measured (retried, not counted as ranked or unranked): "
         f"{data['measurement_failed']}",
         f"- **new-page candidates: {len(data['new_candidates'])}**",
         f"- **update candidates (a page of ours already ranks): {len(data['update_candidates'])}**",
         ""]

    L.append("## New-page candidates")
    L.append("")
    if not data["new_candidates"]:
        L += ["_No candidate cleared the filters. That is an answer, not a failure:_",
              "_a zero here means the measured demand is already served or already ranked._", ""]
    else:
        L += ["| query | impressions | clicks | proposed slug | measurement |",
              "|---|---|---|---|---|"]
        for c in data["new_candidates"]:
            L.append(f"| {c['query']} | {c['impressions']} | {c['clicks']} | "
                     f"`{c['slug']}` | impressions {c['impressions']} / clicks {c['clicks']} "
                     f"/ window ..{data['window_end']} / bing-webmaster |")
        L.append("")

    L.append("## Update candidates")
    L.append("")
    if not data["update_candidates"]:
        L += ["_No query below its own new-page floor is already ranked by a page of ours._", ""]
    else:
        L += ["| query | impressions | clicks | ranked page | page impressions | page clicks | avg position |",
              "|---|---|---|---|---|---|---|"]
        for c in data["update_candidates"]:
            top = c["ranked_by"][0]
            L.append(f"| {c['query']} | {c['impressions']} | {c['clicks']} | "
                     f"`{top['path']}` | {top['impressions']} | {top['clicks']} | "
                     f"{top['position']:g} |")
        L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="write CANDIDATES.md + funnel log")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    data = collect()
    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps({k: v for k, v in data.items()
                      if k not in ("new_candidates", "update_candidates", "funnel")}, indent=2))
    print(f"new candidates: {len(data['new_candidates'])}")
    for c in data["new_candidates"][:10]:
        print(f"  {c['impressions']:>6}  {c['query']}")
    print(f"update candidates: {len(data['update_candidates'])}")
    for c in data["update_candidates"][:10]:
        print(f"  {c['impressions']:>6}  {c['query']} -> {c['ranked_by'][0]['path']}")
    if a.write:
        CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
        CANDIDATES.write_text(render(data), encoding="utf-8")
        print(f"written: {CANDIDATES}")
        FUNNEL_LOG.parent.mkdir(parents=True, exist_ok=True)
        with FUNNEL_LOG.open("a", encoding="utf-8") as f:
            for e in data["funnel"]:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        print(f"written: {FUNNEL_LOG} (+{len(data['funnel'])} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
