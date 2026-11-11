#!/usr/bin/env python3
"""quick-wins.py — CCH-SEO-26: four weekly quick-win lists from Bing and GSC.

Read-only against Bing Webmaster and Search Console; edits no page. Writes
logs/seo/quick-wins-<YYYY-MM-DD>.json; weekly-report.py render() prints the
top 5 rows of each list (it reads the file, never re-hits the APIs — Y55).

Run with video-generator/venv/bin/python (google libs, Ga4SdkMissing lesson).
Runbook (operator adds, this card does not touch crontab):
  35 6 * * 1  cd ~/ && video-generator/venv/bin/python scripts/quick-wins.py
  (Monday 06:35 UTC, before weekly-report 06:40)

Shape facts, captured live 2026-11-10 (scripts/tests/fixtures/quick-wins/):
  * Bing GetQueryStats = one row per (query, WEEK), dated on a Friday, at most
    100 rows per week. Rows are SUMMED per query per window BEFORE any
    threshold (2026-09-14 ledger row). Position is impressions-weighted.
  * GetQueryStats carries no page. The page comes from GetQueryPageStats,
    which puts the page URL in its `Query` field; looked up only for queries
    that already landed in a list.
  * GetLinkCounts answered {"Links": [], "TotalPages": 0}: "no data", printed
    as n/a with its cause, never as 0.

The unit of a row is a query (Bing) or a (query, page) pair (GSC). "Falling
pages" is therefore a falling (query, page): the page a falling query ranked on.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "logs" / "seo"
INDEX_STATUS = ROOT / "logs" / "gsc" / "index-status.json"
HOST = "https://defycard.com"
WINDOW_DAYS = 28

# Thresholds as literals (Y49: the law is written out, not derived).
STRIKE_POS = (8.0, 15.0)
STRIKE_MIN_IMP = 10
LOWCTR_MIN_IMP, LOWCTR_MAX_POS, LOWCTR_MAX_CTR = 50, 10.0, 0.01
FALL_MIN_PREV, FALL_MAX_RATIO = 20, 0.6
NOCLICK_MIN_IMP = 20

LISTS = ("striking_distance", "low_ctr", "falling", "no_clicks")


def _load(name: str, fname: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / fname)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------

def windows(today: date) -> tuple[tuple[date, date], tuple[date, date]]:
    """(current, previous): the last 28 FULL days (ending yesterday) and the 28 before."""
    cur_end = today - timedelta(days=1)
    cur = (cur_end - timedelta(days=WINDOW_DAYS - 1), cur_end)
    prev_end = cur[0] - timedelta(days=1)
    return cur, (prev_end - timedelta(days=WINDOW_DAYS - 1), prev_end)


def bing_date(s: str) -> date:
    """'/Date(1787270400000)/' -> date (UTC)."""
    ms = int(re.search(r"\((-?\d+)", s).group(1))
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()


def is_noise(query: str) -> bool:
    q = query.lower().strip()
    return q.startswith("site:") or "defycard" in q


def _empty() -> dict:
    return {"impressions": 0, "clicks": 0, "pos_w": 0.0}


def bing_aggregate(rows: list[dict], cur: tuple[date, date], prev: tuple[date, date],
                   normalize) -> list[dict]:
    """SUM the query x week rows per normalized query in each window.

    -> entries {engine, query, page, cur{impressions,clicks,position}, prev{...}}.
    """
    agg: dict[str, dict] = {}
    for r in rows:
        q = (r.get("Query") or "").strip()
        if not q or is_noise(q):
            continue
        d = bing_date(r["Date"])
        if cur[0] <= d <= cur[1]:
            w = "cur"
        elif prev[0] <= d <= prev[1]:
            w = "prev"
        else:
            continue
        e = agg.setdefault(normalize(q), {"query": q, "cur": _empty(), "prev": _empty()})
        imp = int(r.get("Impressions") or 0)
        e[w]["impressions"] += imp
        e[w]["clicks"] += int(r.get("Clicks") or 0)
        e[w]["pos_w"] += float(r.get("AvgImpressionPosition") or 0) * imp
    return [_entry("bing", e["query"], None, e["cur"], e["prev"]) for e in agg.values()]


def _entry(engine, query, page, cur, prev) -> dict:
    def fin(w):
        imp = w["impressions"]
        pos = round(w["pos_w"] / imp, 1) if imp else 0.0
        return {"impressions": imp, "clicks": w["clicks"], "position": pos}
    return {"engine": engine, "query": query, "page": page, "cur": fin(cur), "prev": fin(prev)}


def gsc_entries(cur_rows: list[dict], prev_rows: list[dict]) -> list[dict]:
    """searchanalytics rows with keys [query, page] -> entries."""
    def w(r):
        return {"impressions": int(r["impressions"]), "clicks": int(r["clicks"]),
                "pos_w": float(r["position"]) * int(r["impressions"])}
    cur = {tuple(r["keys"]): r for r in cur_rows}
    prev = {tuple(r["keys"]): r for r in prev_rows}
    out = []
    for k in cur.keys() | prev.keys():
        if is_noise(k[0]):
            continue
        out.append(_entry("gsc", k[0], k[1],
                          w(cur[k]) if k in cur else _empty(),
                          w(prev[k]) if k in prev else _empty()))
    return out


def build_lists(entries: list[dict]) -> dict[str, list[dict]]:
    lo, hi = STRIKE_POS
    lists: dict[str, list[dict]] = {k: [] for k in LISTS}
    for e in entries:
        c, p = e["cur"], e["prev"]
        row = {"engine": e["engine"], "query": e["query"], "page": e["page"],
               "impressions": c["impressions"], "clicks": c["clicks"],
               "position": c["position"] if c["impressions"] else p["position"]}
        if lo <= c["position"] <= hi and c["impressions"] >= STRIKE_MIN_IMP:
            lists["striking_distance"].append(row)
        if (c["impressions"] >= LOWCTR_MIN_IMP and 0 < c["position"] <= LOWCTR_MAX_POS
                and c["clicks"] / c["impressions"] < LOWCTR_MAX_CTR):
            lists["low_ctr"].append(row)
        if p["impressions"] >= FALL_MIN_PREV and c["impressions"] <= FALL_MAX_RATIO * p["impressions"]:
            lists["falling"].append({**row, "prev_impressions": p["impressions"]})
        if c["impressions"] >= NOCLICK_MIN_IMP and c["clicks"] == 0:
            lists["no_clicks"].append(row)
    for k in LISTS:
        key = (lambda r: (-r["prev_impressions"], r["query"])) if k == "falling" \
            else (lambda r: (-r["impressions"], r["query"]))
        lists[k].sort(key=key)
    return lists


def canonical(page: str | None) -> str | None:
    if not page:
        return None
    return HOST + (urllib.parse.urlparse(page).path or "/")


def index_state(page: str | None, status: dict | None) -> str:
    """Google's own verdict for the page, from logs/gsc/index-status.json."""
    if status is None:
        return "unknown (index-status.json unreadable)"
    u = canonical(page)
    if u is None:
        return "unknown (no page)"
    v = (status.get("urls") or {}).get(u)
    if v is None:
        return "not inspected"
    return "indexed" if v.get("verdict") == "PASS" else f"not indexed ({v.get('coverage')})"


# ---------------------------------------------------------------------------
# Live collection
# ---------------------------------------------------------------------------

def collect_bing(today: date) -> tuple[list[dict], int]:
    bk = _load("bing_keywords_qw", "bing-keywords.py")
    cur, prev = windows(today)
    entries = bing_aggregate(bk._get("GetQueryStats") or [], cur, prev, bk.normalize)
    # Page lookup only for queries that landed in a list.
    wanted = {r["query"] for rows in build_lists(entries).values() for r in rows}
    pages: dict[str, str] = {}
    failed = 0
    for e in entries:
        if e["query"] not in wanted:
            continue
        try:
            ps = bk.query_page_stats(e["query"])
        except bk.BingMeasurementFailed:
            failed += 1
            continue
        if ps:
            pages[e["query"]] = ps[0]["path"]
    for e in entries:
        e["page"] = pages.get(e["query"])
    return entries, failed


def bing_inbound_links() -> str:
    bk = _load("bing_keywords_qw", "bing-keywords.py")
    r = bk._get("GetLinkCounts", page=0) or {}
    links = r.get("Links") or []
    if not links:
        return ("n/a (GetLinkCounts returned Links=[] TotalPages="
                f"{r.get('TotalPages')} — no link data, not read as zero)")
    total = sum(int(x.get("Count") or 0) for x in links)
    domains = set()
    for x in links:  # ponytail: first page of links per target, shape unverified (empty on 2026-11-10)
        for d in (bk._get("GetUrlLinks", link=x["Url"], page=0) or {}).get("Details") or []:
            domains.add(urllib.parse.urlparse(d.get("Url", "")).netloc)
    return f"{total} from {len(domains)} domains"


def collect_gsc(today: date) -> list[dict]:
    gsc = _load("gsc_report_qw", "gsc-report.py")
    client = gsc._client()
    cur, prev = windows(today)

    def q(win):
        return client.searchanalytics().query(siteUrl=gsc.SITE_URL, body={
            "startDate": win[0].isoformat(), "endDate": win[1].isoformat(),
            "dimensions": ["query", "page"], "rowLimit": 25000}).execute().get("rows") or []
    return gsc_entries(q(cur), q(prev))


def run(today: date | None = None) -> dict:
    today = today or date.today()
    try:
        status = json.loads(INDEX_STATUS.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        status = None
    entries: list[dict] = []
    engines: dict[str, dict] = {}
    bing_failed = 0
    for name, fn in (("bing", collect_bing), ("gsc", collect_gsc)):
        try:
            got = fn(today)
            if name == "bing":
                got, bing_failed = got
            entries += got
            engines[name] = {"status": "ok", "entries": len(got)}
        except (Exception, SystemExit) as e:  # noqa: BLE001 — SystemExit: _get exits without a key
            engines[name] = {"status": "failed", "why": f"{type(e).__name__}: {str(e)[:200]}"}
    if bing_failed:
        engines["bing"]["page_lookups_failed"] = bing_failed
    try:
        links = bing_inbound_links()
    except (Exception, SystemExit) as e:  # noqa: BLE001
        links = f"n/a ({type(e).__name__}: {str(e)[:200]})"
    lists = build_lists(entries)
    for rows in lists.values():
        for r in rows:
            r["index"] = index_state(r["page"], status)
    cur, prev = windows(today)
    return {"date": today.isoformat(), "current": [d.isoformat() for d in cur],
            "previous": [d.isoformat() for d in prev], "engines": engines,
            "bing_inbound_links": links, "lists": lists}


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    res = run()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / f"quick-wins-{res['date']}.json"
    p.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print(p)
    print(json.dumps({"engines": res["engines"],
                      "rows": {k: len(v) for k, v in res["lists"].items()},
                      "bing_inbound_links": res["bing_inbound_links"]}, indent=1))
    return 0 if all(e["status"] == "ok" for e in res["engines"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
