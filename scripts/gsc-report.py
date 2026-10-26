#!/usr/bin/env python3
"""gsc-report.py — Google Search Console as the second demand instrument (E-12).

WHY THIS FILE EXISTS
--------------------
FABLE_DEFYCARD_PART2.md, wave 2: GSC access already exists and already works
(`sc-domain:defycard.com` -> siteFullUser under the same service account key
that reads GA4). Google is NOT a growth channel here (Bing is the working
search channel, per the same document) — this script exists only so §6's
money copy has a second, independent instrument checking it, next to AEO's
agent-fetch instrument.

Read-only, same contract as weekly-report.py: an API failure becomes
`{"status": "instrument_dead"}`, never a silent zero (Y55's rule) — the
caller (weekly-report.py's ACTION REQUIRED header) is what surfaces it to a
human, this script does not send its own alert.

Runbook: cron `30 6 * * 1` — ten minutes before the Monday weekly report,
same pattern as bing-keywords.py feeding _collect_bing(). Writes
logs/gsc/report.json; weekly-report.py's _collect_gsc() reads that file, it
never re-hits the API (Y55: "read from the funnel file, not a second live
hit").

home `gsc_*.py` (gsc_status.py, gsc_probe.py, gsc_owner.py, ...) are the
dispatcher's own scratch scripts from setting this up — not in this repo, not
touched here.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
GA4_SA = Path.home() / ".claude" / "ga4-sa.json"
SITE_URL = "sc-domain:defycard.com"
SITE_HOST = "https://defycard.com"  # URL Inspection rejects a bare path with
# 403 "not part of this property" — it only accepts a full URL (ruling-1).
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

# URL Inspection quota is 2 000/day (Google's own published limit). Inspecting
# the top 20 most agent-fetched URLs/week spends 20 of it — nowhere near the
# ceiling, so no rationing logic is needed, only a stated bound.
INSPECT_LIMIT = 20

# --inspect-all: whole-sitemap sweep. Google's quota is 2 000/day; this run
# spends at most 1 800, the weekly top-20 spends 20, plus 1 spare: < 2 000.
INSPECT_ALL_MAX = 1800

REPORT_FILE = ROOT / "logs" / "gsc" / "report.json"
INDEX_STATUS_FILE = ROOT / "logs" / "gsc" / "index-status.json"
SITEMAPS = [ROOT / "seo-site" / "dist" / "sitemap-0.xml",
            ROOT / "seo-site" / "dist" / "sitemap-videos.xml"]


def _client():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(
        str(GA4_SA), scopes=SCOPES)
    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------------------
# Pure logic — testable with literal fixtures, no live API involved (Y49).
# ---------------------------------------------------------------------------

def is_indexed(verdict: str) -> bool:
    """THE indexed law, one place: Google's own verdict is PASS. Coverage
    strings lie by omission — "URL is unknown to Google" and "Duplicate,
    Google chose different canonical" contain no "not indexed" yet are not in
    the index (CCH-SEO-01 ruling-1)."""
    return verdict == "PASS"


def count_not_indexed(verdicts: list[str]) -> dict[str, int]:
    """How many of the inspected URLs are NOT in Google's index (verdict != PASS)."""
    return {"inspected": len(verdicts),
            "not_indexed": sum(1 for v in verdicts if not is_indexed(v))}


def render_line(data: dict[str, Any]) -> str:
    """The one line weekly-report.py prints — format lives here, once."""
    if data.get("status") == "instrument_dead":
        return f"ACTION REQUIRED: google: {data.get('why', 'unknown')}"
    idx = data.get("indexing")
    # count_not_indexed([]) returns {"inspected": 0, "not_indexed": 0} — a
    # non-empty dict, so `if idx:` alone is true and prints the false "0/0"
    # ("100% indexed") ruling-1 caught. inspected must be > 0 to report a
    # ratio at all.
    not_indexed = (f"{idx['not_indexed']}/{idx['inspected']}"
                   if idx and idx["inspected"] else "n/a")
    return (f"google: {data['impressions']} imp / {data['clicks']} clicks / "
            f"pos {data['position']} / {data['pages']} pages / "
            f"not-indexed {not_indexed}")


# ---------------------------------------------------------------------------
# Live collection
# ---------------------------------------------------------------------------

def fetch_totals(client, since: str, until: str) -> dict[str, Any]:
    r = client.searchanalytics().query(siteUrl=SITE_URL, body={
        "startDate": since, "endDate": until, "rowLimit": 1}).execute()
    row = (r.get("rows") or [{}])[0]
    return {
        "impressions": int(row.get("impressions", 0)),
        "clicks": int(row.get("clicks", 0)),
        "position": round(float(row.get("position", 0.0)), 1),
    }


def fetch_top(client, since: str, until: str, dimension: str, limit: int = 10) -> list[dict[str, Any]]:
    r = client.searchanalytics().query(siteUrl=SITE_URL, body={
        "startDate": since, "endDate": until,
        "dimensions": [dimension], "rowLimit": limit}).execute()
    return [{"key": row["keys"][0], "clicks": int(row.get("clicks", 0)),
             "impressions": int(row.get("impressions", 0))}
            for row in r.get("rows", [])]


def fetch_pages_with_impressions(client, since: str, until: str) -> int:
    """GSC only ever returns rows with >=1 impression, so the row count of a
    page-dimension query IS "pages with >=1 impression" — no extra filter."""
    r = client.searchanalytics().query(siteUrl=SITE_URL, body={
        "startDate": since, "endDate": until,
        "dimensions": ["page"], "rowLimit": 25000}).execute()
    return len(r.get("rows", []))


def top_fetched_urls(limit: int = INSPECT_LIMIT) -> list[str]:
    """The N most agent-fetched URLs for the last complete week, reused from
    the AEO instrument (verified_by_page) — "fetched" is not re-defined here,
    it is the same UA+vendor-IP-verified count aeo-aggregate.py already
    computes. Falls back to the claimed `by_page` for weeks recorded before
    verification existed, same rule as aeo-aggregate.build_targets."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "aeo_aggregate", ROOT / "scripts" / "aeo-aggregate.py")
    agg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agg)
    week = agg.load_week(agg.last_complete_week(date.today()))
    if not week:
        return []
    by_page = week["verified_by_page"] if "verified_by_page" in week else (week.get("by_page") or {})
    # Filter "/" out before slicing, not after — filtering after would drop
    # to 19 whenever "/" lands inside the top `limit`.
    ranked = [p for p, _ in sorted(by_page.items(), key=lambda kv: -kv[1]) if p != "/"]
    return ranked[:limit]


def inspect_indexing(client, paths: list[str]) -> dict[str, int]:
    """One inspect() call per path. A single URL failing must not blank the
    other 19 — it is simply excluded from `inspected`.

    `paths` come from top_fetched_urls() as site-relative paths
    ("/how-to/..."). The URL Inspection API 403s ("not part of this
    property") on a bare path — it only accepts the full URL, so SITE_HOST is
    prefixed here before the call (ruling-1: this was the live 403 on all 19
    inspections that made every run report a false 0/0)."""
    states = []
    for path in paths:
        try:
            r = client.urlInspection().index().inspect(body={
                "inspectionUrl": SITE_HOST + path, "siteUrl": SITE_URL}).execute()
            states.append(r.get("inspectionResult", {})
                          .get("indexStatusResult", {}).get("verdict", ""))
        except Exception:  # noqa: BLE001
            continue
    return count_not_indexed(states)


def sitemap_urls(files=None) -> list[str]:
    """Every <loc>, deduped, order-preserving (sitemap-videos repeats articles)."""
    seen: dict[str, None] = {}
    for f in (SITEMAPS if files is None else files):
        for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", Path(f).read_text(encoding="utf-8")):
            seen.setdefault(u)
    return list(seen)


def inspect_all(client, urls: list[str], sleep=time.sleep) -> dict[str, dict[str, str]]:
    """One inspect() per URL, at most INSPECT_ALL_MAX. A failing URL is
    recorded as instrument_error (Y55), never skipped; HTTP 429 sleeps 60 s
    once for that URL, then records the error."""
    out: dict[str, dict[str, str]] = {}
    for url in urls[:INSPECT_ALL_MAX]:
        for attempt in (0, 1):
            try:
                r = client.urlInspection().index().inspect(body={
                    "inspectionUrl": url, "siteUrl": SITE_URL}).execute()
                st = r.get("inspectionResult", {}).get("indexStatusResult", {})
                out[url] = {"coverage": st.get("coverageState", ""),
                            "verdict": st.get("verdict", ""),
                            "last_crawl": st.get("lastCrawlTime", ""),
                            "canonical_google": st.get("googleCanonical", "")}
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 0 and getattr(getattr(e, "resp", None), "status", None) == 429:
                    sleep(60)
                    continue
                out[url] = {"coverage": "instrument_error",
                            "why": f"{type(e).__name__}: {str(e)[:120]}"}
                break
    return out


def bucket_of(url: str) -> str:
    seg = url.split("://", 1)[-1].split("/", 1)[-1].strip("/").split("/")[0]
    return seg or "root"


def summarize(urls: dict[str, dict[str, str]]) -> dict[str, dict[str, int]]:
    """by first path segment -> {indexed, not_indexed, error}. indexed = is_indexed(verdict),
    instrument_error apart."""
    out: dict[str, dict[str, int]] = {}
    for u, v in urls.items():
        b = out.setdefault(bucket_of(u), {"indexed": 0, "not_indexed": 0, "error": 0})
        c = v.get("coverage", "")
        b["error" if c == "instrument_error"
          else "indexed" if is_indexed(v.get("verdict", "")) else "not_indexed"] += 1
    return out


def run_inspect_all() -> dict[str, dict[str, int]]:
    urls = sitemap_urls()
    res = inspect_all(_client(), urls)
    INDEX_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_STATUS_FILE.write_text(json.dumps(
        {"run": str(date.today()), "inspected": len(res), "urls": res}, indent=2) + "\n",
        encoding="utf-8")
    return summarize(res)


def collect(since: str, until: str) -> dict[str, Any]:
    try:
        client = _client()
        out: dict[str, Any] = {"status": "ok", "since": since, "until": until,
                               **fetch_totals(client, since, until)}
        out["pages"] = fetch_pages_with_impressions(client, since, until)
        out["top_queries"] = fetch_top(client, since, until, "query")
        out["top_pages"] = fetch_top(client, since, until, "page")
    except Exception as e:  # noqa: BLE001
        return {"status": "instrument_dead", "why": f"{type(e).__name__}: {e}"}

    try:
        urls = top_fetched_urls()
        if urls:
            out["indexing"] = inspect_indexing(client, urls)
    except Exception:  # noqa: BLE001
        pass  # indexing is a bonus figure; totals already collected above
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=str(date.today() - timedelta(days=28)))
    ap.add_argument("--until", default=str(date.today()))
    ap.add_argument("--inspect-all", action="store_true")
    a = ap.parse_args()

    if a.inspect_all:
        print(json.dumps(run_inspect_all(), indent=2))
        return 0

    data = collect(a.since, a.until)
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(render_line(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
