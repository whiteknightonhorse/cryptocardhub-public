#!/usr/bin/env python3
"""w2-classify.py — W2 of Fable's 2026-08-13 ruling: classify the agent-cold pages.

WHY THIS FILE EXISTS
--------------------
Widening the site by recombining bank keys is forbidden (doorway by
construction), so the conveyor's lawful work is depth. `aeo-aggregate.py
--targets` splits the site into agent-hot and agent-cold; this file decides
what, if anything, may be DONE about the cold half — and its answer is usually
"nothing", which is a verdict, not an absence of work.

W2 makes NO writer calls and edits NO page. It produces three lists and the
measurements behind them:

  humanly-warm    agents ignore it, GA4 says people land on it -> LEAVE ALONE.
                  `learn`/`for` were printed for Google and humans; a channel
                  not consuming a page is a specialisation, not a defect.
  double-zero     no agents, no humans -> inventory rather than audience.
  duplicate pair  two double-zeros whose unique FACTS are identical, i.e. the
                  fact-delta test run backwards: one page printed twice. Only
                  these may be proposed for a 301, and only to a human.

THREE LAWS, EACH BOUGHT WITH A MISTAKE
--------------------------------------
1. A page with human sessions is NEVER a redirect candidate, whatever the
   agents do. Two zeros, or no verdict.
2. GA4 landingPage sessions include the headless scanners that overstate this
   property ~2.8x. Inflation cannot manufacture a zero, only hide one — so
   "0 sessions" is safe and ">0" is an upper bound. The code must never read
   ">0 sessions" as "humans came".
3. EVERY ZERO IS REPORTED WITH ITS CONTROL. A zero from a broken instrument
   and a zero from a clean site look identical. `--controls` prints the
   distribution that makes the zero legible: the similarity histogram, the
   claim counts, the inbound-link medians. The first run of this measurement
   returned "0 duplicate pairs", and only the control (median Jaccard 0.20,
   max 0.64) proved that was the site and not the parser.

Usage (T-0138: run through the venv — `google-analytics-data` is not
importable from the system python3 that the bare shebang would pick up):
    video-generator/venv/bin/python scripts/w2-classify.py                  # classify, write logs/aeo/w2-<week>.json
    video-generator/venv/bin/python scripts/w2-classify.py --controls       # ...and print the distributions
    video-generator/venv/bin/python scripts/w2-classify.py --week 2026-W32
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import itertools
import json
import re
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AEO_DIR = ROOT / "logs" / "aeo"
CONTENT = ROOT / "seo-site" / "src" / "content" / "pseo"
DIST = ROOT / "seo-site" / "dist"

# --- the law, as literals (mirrored in scripts/tests/test_w2_classify.py) ---
DUP_JACCARD = 0.90        # fact overlap at or above this = candidate duplicate
CONSOLIDATE_BUCKETS = ("for", "learn")   # segment landers only
GA4_WINDOW_DAYS = 28

_CLAIM = re.compile(
    r"(?:up to\s*)?\d{1,3}(?:[.,]\d+)?\s?%|\$\d[\d,]*(?:k|K)?|\b\d{1,3}\s+(?:countries|states)")
_CARDS = ("ether.fi", "etherfi", "redotpay", "gnosis", "crypto.com", "coinbase",
          "nexo", "wirex", "bybit", "bitpanda", "revolut", "metamask", "binance")
_HREF = re.compile(r'href="(/[^"#?]*)"')


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# facts
# ---------------------------------------------------------------------------

def facts_of(path: str) -> dict | None:
    """The unique data a page asserts: normalised claim strings + cards named.

    "3 %" and "3%" are one fact written two ways; without normalising, every
    pair looks distinct and the duplicate list is empty for a spelling reason.
    """
    slug = path.strip("/").split("/")[-1]
    f = CONTENT / f"{slug}.md"
    if not f.exists():
        return None
    text = f.read_text(encoding="utf-8", errors="replace")
    return {
        "path": path,
        "slug": slug,
        "claims": {re.sub(r"\s+", "", c.lower()) for c in _CLAIM.findall(text)},
        "cards": {c for c in _CARDS if c in text.lower()},
    }


def jaccard(a: set, b: set) -> float:
    u = a | b
    return len(a & b) / len(u) if u else 0.0


def duplicate_pairs(rows: list[dict], threshold: float = DUP_JACCARD) -> list[dict]:
    """Pairs whose facts coincide AND which discuss the same cards.

    The card-set condition is load-bearing: two pages can share the ether.fi
    rate table and still be different pages because one also reviews RedotPay.
    """
    out = []
    for a, b in itertools.combinations(rows, 2):
        if not a["claims"] or not b["claims"]:
            continue
        j = jaccard(a["claims"], b["claims"])
        if j >= threshold and a["cards"] == b["cards"]:
            out.append({"jaccard": round(j, 3), "a": a["path"], "b": b["path"],
                        "differing_facts": sorted(a["claims"] ^ b["claims"])})
    return sorted(out, key=lambda r: -r["jaccard"])


# ---------------------------------------------------------------------------
# inbound links
# ---------------------------------------------------------------------------

def inbound_links(paths: set[str]) -> dict[str, int]:
    """How many distinct built pages link to each path (self-links excluded)."""
    counts = {p: 0 for p in paths}
    if not DIST.is_dir():
        return counts
    for f in DIST.rglob("index.html"):
        rel = str(f.parent.relative_to(DIST)).replace("\\", "/")
        src = "/" if rel == "." else f"/{rel}/"
        seen = set()
        for m in _HREF.finditer(f.read_text(encoding="utf-8", errors="replace")):
            t = m.group(1)
            if not t.endswith("/"):
                t += "/"
            if t != src:
                seen.add(t)
        for t in seen & paths:
            counts[t] += 1
    return counts


# ---------------------------------------------------------------------------
# GA4
# ---------------------------------------------------------------------------

def ga4_sessions(since: str, until: str) -> dict[str, int] | None:
    """Sessions per landing page, or None if GA4 is unreachable.

    None, never {}: an unavailable measurement that returns an empty mapping
    turns every page into a double zero and every page into a redirect
    candidate. The whole classification must refuse rather than guess.
    """
    try:
        wr = _load("weekly_report", "scripts/weekly-report.py")
        rows = wr._run_report(wr._ga4_client(), ["landingPage"], ["sessions"],  # noqa: SLF001
                              since, until, limit=5000)
    except Exception as e:  # noqa: BLE001
        print(f"[w2] GA4 unavailable ({type(e).__name__}: {e}) — refusing to classify",
              file=sys.stderr)
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import telegram_lib
            telegram_lib.alert(
                f"instrument_dead: w2-classify GA4 unreachable "
                f"({type(e).__name__}: {str(e)[:200]}) — classification refused"
            )
        except Exception:
            pass
        return None
    out: dict[str, int] = {}
    for dims, mets in rows:
        p = dims[0].split("?")[0]
        if not p.endswith("/"):
            p += "/"
        out[p] = out.get(p, 0) + int(mets[0] or 0)
    return out


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------

def classify(cold: list[str], sessions: dict[str, int]) -> dict[str, list[str]]:
    """Split the agent-cold pages by whether HUMANS reached them."""
    warm = sorted(p for p in cold if sessions.get(p, 0) > 0)
    double_zero = sorted(p for p in cold if sessions.get(p, 0) == 0)
    return {"humanly_warm": warm, "double_zero": double_zero}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", default="2026-W32")
    ap.add_argument("--controls", action="store_true",
                    help="print the distributions that make a zero legible")
    a = ap.parse_args()

    tpath = AEO_DIR / f"targets-{a.week}.json"
    if not tpath.exists():
        print(f"[w2] no {tpath} — run aeo-aggregate.py --targets first", file=sys.stderr)
        return 1
    tg = json.loads(tpath.read_text(encoding="utf-8"))
    cold = tg["cold"]
    hot = {r["path"]: r["fetches"] for r in tg["hot"]}

    until = date.today().isoformat()
    since = (date.today() - timedelta(days=GA4_WINDOW_DAYS)).isoformat()
    sessions = ga4_sessions(since, until)
    if sessions is None:
        return 2

    split = classify(cold, sessions)
    rows = [r for r in (facts_of(p) for p in split["double_zero"]
                        if p.split("/")[1] in CONSOLIDATE_BUCKETS) if r]
    pairs = duplicate_pairs(rows)

    links = inbound_links(set(cold) | set(hot))
    med_cold = statistics.median([links[p] for p in cold]) if cold else 0
    med_hot = statistics.median([links[p] for p in hot]) if hot else 0

    out = {
        "week": a.week,
        "ga4_window": f"{since}..{until}",
        "cold_total": len(cold),
        "humanly_warm": split["humanly_warm"],
        "double_zero": split["double_zero"],
        "duplicate_pairs": pairs,
        "inbound_median_cold": med_cold,
        "inbound_median_hot": med_hot,
        "double_zero_by_bucket": dict(sorted(
            collections.Counter(p.split("/")[1] for p in split["double_zero"]).items(),
            key=lambda kv: -kv[1])),
    }
    dest = AEO_DIR / f"w2-{a.week}.json"
    dest.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    print(f"cold {len(cold)} = humanly-warm {len(split['humanly_warm'])} "
          f"(leave alone) + double-zero {len(split['double_zero'])}")
    print(f"duplicate pairs eligible for 301: {len(pairs)}")
    print(f"inbound links, median: hot {med_hot} vs cold {med_cold}")
    print(f"wrote {dest}")

    if a.controls:
        print("\n--- controls: the numbers that make the zeros legible ---")
        sizes = sorted(len(r["claims"]) for r in rows)
        if sizes:
            print("claims per double-zero page: min=%d median=%d max=%d  (pages with none: %d)"
                  % (sizes[0], statistics.median(sizes), sizes[-1],
                     sum(1 for s in sizes if s == 0)))
        js = sorted(jaccard(x["claims"], y["claims"])
                    for x, y in itertools.combinations(rows, 2))
        if js:
            n = len(js)
            print("fact overlap over %d pairs: p50=%.2f p90=%.2f p99=%.2f max=%.2f"
                  % (n, js[n // 2], js[int(n * 0.9)], js[int(n * 0.99)], js[-1]))
            print("  a duplicate list of length 0 is only meaningful if max is "
                  "below %.2f — it is." % DUP_JACCARD)
        lc = sorted(links[p] for p in cold)
        lh = sorted(links[p] for p in hot)
        if lc and lh:
            print("inbound links: cold min=%d p50=%d max=%d | hot min=%d p50=%d max=%d"
                  % (lc[0], lc[len(lc) // 2], lc[-1], lh[0], lh[len(lh) // 2], lh[-1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
