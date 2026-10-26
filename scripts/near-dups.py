#!/usr/bin/env python3
"""near-dups.py — CCH-SEO-08: read-only near-duplicate audit of the pSEO corpus.

A pair is listed when keyword-token Jaccard >= 0.6 (primary_keyword + h1) OR
body 8-gram overlap >= 0.25 (faq-children.overlap — the FAQ delta gate's own
function, imported, not rewritten). Each page gets GSC 90 d, GA4 human
sessions 90 d and the CCH-SEO-01 index-status fields attached; any instrument
failure becomes "n/a (<cause>)" and the pair is still listed. Proposed KEEPER =
max human sessions, then GSC impressions, then the OLDER generated_at.

Nothing is edited, deleted, redirected or noindexed — that is CCH-SEO-09,
after the operator's decision. Deterministic: no model calls.
"""
from __future__ import annotations

import functools
import importlib.util
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "seo-site" / "src" / "content" / "pseo"
INDEX_STATUS = ROOT / "logs" / "gsc" / "index-status.json"
OUT_DIR = ROOT / "logs" / "seo"
SITE = "https://defycard.com"

JACCARD_MIN = 0.6
OVERLAP_MIN = 0.25
WINDOW_DAYS = 90

# Literal copy of STOP in seo-site/src/lib/related.mjs (CCH-SEO-05). Python and
# JS cannot share it — change both together.
STOP = set("a the for of in to is vs and or with card cards crypto 2026 best".split(" "))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tokens(page: dict) -> set[str]:
    text = f"{page['primary_keyword']} {page['h1']}".lower()
    return {w for w in re.split(r"[^a-z0-9]+", text) if w and w not in STOP}


def jaccard(a: set[str], b: set[str]) -> float:
    return round(len(a & b) / len(a | b), 4) if a and b else 0.0


def load_pages(content: Path = CONTENT) -> list[dict]:
    pages = []
    for f in sorted(content.glob("*.md")):
        m = re.match(r"---\n(.*?)\n---\n(.*)", f.read_text(encoding="utf-8"), re.S)
        if not m:
            continue
        fm = yaml.safe_load(m.group(1)) or {}
        pages.append({
            "path": f.name, "url": f"{SITE}/{fm.get('bucket')}/{fm.get('slug', f.stem)}/",
            "primary_keyword": str(fm.get("primary_keyword") or ""),
            "h1": str(fm.get("h1") or ""),
            "generated_at": str(fm.get("generated_at") or ""),
            "body": m.group(2),
        })
    return pages


def find_pairs(pages: list[dict], overlap_fn) -> list[dict]:
    toks = [tokens(p) for p in pages]
    pairs = []
    for i in range(len(pages)):
        for j in range(i + 1, len(pages)):
            jac = jaccard(toks[i], toks[j])
            # overlap is directional (share of the FIRST text's shingles in the second)
            ovl = max(overlap_fn(pages[i]["body"], pages[j]["body"]),
                      overlap_fn(pages[j]["body"], pages[i]["body"]))
            if jac >= JACCARD_MIN or ovl >= OVERLAP_MIN:
                pairs.append({"a": pages[i], "b": pages[j], "jaccard": jac, "overlap": ovl})
    return pairs


def keeper_key(page: dict, stats: dict) -> tuple:
    """Lower sorts first = keeper. Failed instruments count as 0, older wins ties."""
    s = stats.get(page["url"].rstrip("/"), {})
    return (-s.get("human", 0), -s.get("impressions", 0), page["generated_at"])


def pick(pair: dict, human: dict, gsc: dict) -> tuple[dict, dict]:
    def merged(p):
        k = p["url"].rstrip("/")
        return {"human": human.get(k, 0), "impressions": gsc.get(k, {}).get("impressions", 0)}
    stats = {p["url"].rstrip("/"): merged(p) for p in (pair["a"], pair["b"])}
    a, b = sorted((pair["a"], pair["b"]), key=lambda p: keeper_key(p, stats))
    return a, b


def human_by_page(rows) -> dict[str, int]:
    """rows: (landingPage, sessionSource, sessions, engagedSessions). Reuses
    weekly-report.human_sessions per page."""
    wr = _load("weekly_report", "scripts/weekly-report.py")
    per: dict[str, list] = {}
    for path, src, sessions, engaged in rows:
        per.setdefault(SITE + path.rstrip("/"), []).append(
            {"source": src, "sessions": sessions, "engaged": engaged})
    return {k: wr.human_sessions(v) for k, v in per.items()}


def fetch_ga4(since: str, until: str) -> dict[str, int]:
    wr = _load("weekly_report", "scripts/weekly-report.py")
    raw = wr._run_report(wr._ga4_client(), ["landingPage", "sessionSource"],
                         ["sessions", "engagedSessions"], since, until, limit=100000)
    return human_by_page([(d[0], d[1], int(m[0]), int(m[1])) for d, m in raw])


def fetch_gsc(since: str, until: str) -> dict[str, dict]:
    g = _load("gsc_report", "scripts/gsc-report.py")
    r = g._client().searchanalytics().query(siteUrl=g.SITE_URL, body={
        "startDate": since, "endDate": until, "dimensions": ["page"],
        "rowLimit": 25000}).execute()
    return {row["keys"][0].rstrip("/"): {"impressions": int(row.get("impressions", 0)),
                                          "clicks": int(row.get("clicks", 0))}
            for row in r.get("rows", [])}


def fetch_index() -> dict[str, dict]:
    urls = json.loads(INDEX_STATUS.read_text(encoding="utf-8"))["urls"]
    return {u.rstrip("/"): v for u, v in urls.items()}


def guarded(fn, *a):
    """(data, None) or ({}, 'RuntimeError: msg') — a dead instrument never drops a pair."""
    try:
        return fn(*a), None
    except Exception as e:  # noqa: BLE001 — the cause is the output
        return {}, f"{type(e).__name__}: {str(e)[:120]}"


def render(pairs, human, gsc, index, errs, today: str) -> str:
    def na(key):
        return f"n/a ({errs[key]})" if errs.get(key) else None

    def cell(p):
        k = p["url"].rstrip("/")
        h = na("ga4") or human.get(k, 0)
        g = na("gsc") or (f"{gsc.get(k, {}).get('impressions', 0)} imp / "
                          f"{gsc.get(k, {}).get('clicks', 0)} clk")
        i = index.get(k)
        ix = na("index") or ("not in ledger" if i is None else
                             f"{i.get('coverage')} / {i.get('verdict')} / canon={i.get('canonical_google') or '-'}")
        return f"human {h} · {g} · {ix}"

    lines = [f"# Near-duplicate audit — {today}", "",
             f"Pairs: {len(pairs)} (Jaccard >= {JACCARD_MIN} or 8-gram overlap >= {OVERLAP_MIN}). "
             f"Window {WINDOW_DAYS} d. READ-ONLY: nothing edited, redirected or noindexed.",
             f"Instruments: " + (", ".join(f"{k}: n/a ({v})" for k, v in errs.items() if v) or "all ok"),
             "", "| # | KEEPER | LOSER | jaccard | overlap | keeper data | loser data |",
             "|---|---|---|---|---|---|---|"]
    for n, p in enumerate(pairs, 1):
        k, l = pick(p, human, gsc)
        lines.append(f"| {n} | {k['url'][len(SITE):]} | {l['url'][len(SITE):]} | {p['jaccard']:.2f} | "
                     f"{p['overlap']:.2f} | {cell(k)} | {cell(l)} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")  # weekly-report reads GA4_PROPERTY_ID at import
    today = date.today()
    since, until = (today - timedelta(days=WINDOW_DAYS)).isoformat(), (today - timedelta(days=2)).isoformat()
    fc = _load("faq_children", "scripts/faq-children.py")
    fc.shingles = functools.lru_cache(maxsize=None)(fc.shingles)  # 167k pairs re-shingle otherwise
    pairs = find_pairs(load_pages(), fc.overlap)
    human, e1 = guarded(fetch_ga4, since, until)
    gsc, e2 = guarded(fetch_gsc, since, until)
    index, e3 = guarded(fetch_index)
    # Rank on the pair's own order so the report is stable.
    pairs.sort(key=lambda p: (-p["jaccard"], -p["overlap"], p["a"]["path"]))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"near-dups-{today.isoformat()}.md"
    out.write_text(render(pairs, human, gsc, index, {"ga4": e1, "gsc": e2, "index": e3},
                          today.isoformat()), encoding="utf-8")
    print(f"{len(pairs)} pairs -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
