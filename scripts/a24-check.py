#!/usr/bin/env python3
"""a24-check.py — E-14: measure hypothesis A2.4. Read-only. No verdict.

THE HYPOTHESIS (FABLE_DEFYCARD_PART2.md, §B4 point 1, "разрешена 3.4"):
pages an assistant cites WITH A CLICK-THROUGH are the ones with a direct
answer + a number + a verification date + the issuer's own source in one
paragraph. This script does not read prose to test that claim — E-14's own
acceptance line draws the narrower, MEASURABLE slice of it: does a page's
weekly Bing search demand for the query it already ranks for (from
CANDIDATES.md's Update-candidates block — pages we already have, not
invented ones) move together with (a) how often an answer-time agent
actually fetches that page (`verified_by_page`, logs/aeo/history.jsonl) and
(b) how often a human lands on it FROM chatgpt.com (GA4 landingPage x
sessionSource)?

Three instruments, three independent failure modes — each gets its own
try/except and its own typed status (`ok` / `instrument_dead` / `partial`),
never a guessed zero standing in for "the call failed" (the law this whole
project's ledger keeps re-learning: Y58, the GA4-scope 403, the aeo
verified/claimed split).

Correlation is computed PER WEEK, over the pages both series were measured
for that week, using stdlib `statistics.correlation` (Pearson) — no reason
to add a dependency for one number. Below `MIN_PAGES` pages with data in a
week the answer is the literal string "underpowered", never a number a
reader could mistake for a real correlation on 3 data points.

**No verdict is issued anywhere in this file.** That is deliberate, per
E-14's own acceptance line — the operator + Fable read the numbers.

Writes exactly ONE file: logs/aeo/a24-check-<date>.json. Nothing else —
no Telegram, no failure sidecar log. 0 model calls (no Claude/Anthropic
client is imported or constructed anywhere in this file).

Usage (T-0138: run through the venv — `google-analytics-data` is not
importable from the system python3 that the bare shebang would pick up):
    video-generator/venv/bin/python scripts/a24-check.py   # measure, write the JSON, print a summary
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import statistics
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "logs" / "aeo"

# Literal, per E-14's own acceptance text ("по неделям W34-W37").
WEEKS: tuple[str, ...] = ("2026-W34", "2026-W35", "2026-W36", "2026-W37")
# Literal, per E-14's own acceptance text ("N >= 8 страниц, иначе underpowered").
MIN_PAGES = 8

_WCF_DATE_RE = re.compile(r"^/Date\((-?\d+)\)/$")


def _load(name: str, relpath: str):
    """Load a hyphenated sibling script as a module, by path — the same
    trick promote-candidates.py/w36-measure.py already use in this repo."""
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


pc = _load("promote_candidates_a24", "scripts/promote-candidates.py")
aeo = _load("aeo_aggregate_a24", "scripts/aeo-aggregate.py")
bk = pc.bk  # LAW #ONE-PLACE: bing-keywords.py owns normalize()/_get(), reused not re-typed


def _chatgpt_filter():
    """Isolated so tests can monkeypatch it, the same way test_weekly_report.py
    stubs `_click_filter()`/`_cta_view_filter()` — the `google` package this
    needs is not installed outside the production venv."""
    from google.analytics.data_v1beta.types import Filter, FilterExpression
    return FilterExpression(filter=Filter(
        field_name="sessionSource",
        string_filter=Filter.StringFilter(value="chatgpt.com")))


def _bing_week(raw: str) -> str | None:
    """Bing's weekly `Date` field comes back as either a plain ISO date
    (the shape every GetQueryStats test fixture in this repo uses) or the
    WCF `/Date(ms)/` epoch form (the confirmed LIVE shape — see
    bing-keywords.py's GetQueryPageStats docstring). Both land on the Monday
    of an ISO week, so this always resolves to an ISO week key or None."""
    raw = (raw or "").strip()
    m = _WCF_DATE_RE.match(raw)
    if m:
        d = datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).date()
    else:
        try:
            d = date.fromisoformat(raw)
        except ValueError:
            return None
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def ranked_pages(path: Path = pc.CANDIDATES) -> dict[str, list[str]]:
    """-> {path: [normalized query, ...]} from CANDIDATES.md's Update-
    candidates block — OUR pages that Bing already shows for a query, not a
    guess at which page "should" own it. `path` is explicit (not just the
    default) so tests can point it at a fixture, same as
    parse_update_candidates() itself does."""
    out: dict[str, list[str]] = {}
    for row in pc.parse_update_candidates(path):
        out.setdefault(row["path"], []).append(bk.normalize(row["query"]))
    return out


def bing_weekly(queries: set[str]) -> tuple[dict[str, dict[str, int]], dict[str, Any]]:
    """-> ({normalized_query: {week: impressions}}, status). Never guesses a
    zero for a dead instrument — a missing key is absent from `queries`."""
    if not os.environ.get("BING_WEBMASTER_API_KEY"):
        return {}, {"status": "instrument_dead",
                    "why": "BING_WEBMASTER_API_KEY not in environment"}
    try:
        rows = bk._get("GetQueryStats") or []
    except Exception as e:  # noqa: BLE001 — network/API failure, not a zero
        return {}, {"status": "instrument_dead", "why": f"{type(e).__name__}: {e}"}
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        norm = bk.normalize((r.get("Query") or "").strip())
        if norm not in queries:
            continue
        week = _bing_week(r.get("Date"))
        if week not in WEEKS:
            continue
        wk = out.setdefault(norm, {})
        wk[week] = wk.get(week, 0) + int(r.get("Impressions") or 0)
    return out, {"status": "ok"}


def page_bing_weekly(ranked: dict[str, list[str]],
                     by_query: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    """Sum a page's mapped queries' weekly impressions — a page with two
    ranked queries (e.g. "uphold debit card" + "uphold credit card" both
    -> /reviews/uphold-card-review/) counts once, at the PAGE level."""
    out: dict[str, dict[str, int]] = {}
    for path, queries in ranked.items():
        wk: dict[str, int] = {w: 0 for w in WEEKS}
        for q in queries:
            for week, impr in by_query.get(q, {}).items():
                wk[week] += impr
        out[path] = wk
    return out


def verified_weekly(paths: set[str]) -> tuple[dict[str, dict[str, int]], dict[str, Any]]:
    """-> ({path: {week: verified_by_page count}}, status). A week with no
    aggregate on disk is `partial`, never a silent zero for that week."""
    out: dict[str, dict[str, int]] = {p: {} for p in paths}
    missing = []
    for week in WEEKS:
        agg = aeo.load_week(week)
        if agg is None:
            missing.append(week)
            continue
        by_page = agg.get("verified_by_page") or {}
        for p in paths:
            out[p][week] = int(by_page.get(p, 0))
    status = {"status": "ok"} if not missing else {"status": "partial", "missing_weeks": missing}
    return out, status


def ga4_weekly(paths: set[str]) -> tuple[dict[str, dict[str, int]], dict[str, Any]]:
    """-> ({path: {week: sessions with source=chatgpt.com landing on path}},
    status). Isolated try/except around import AND the calls: a missing
    GA4_PROPERTY_ID (weekly-report.py reads it at import time) must fail
    this ONE leg, not the whole script.

    ONE `_run_report` call PER WEEK, dims=["landingPage"] only, since/until
    set to that week's Monday-Sunday — the exact call shape weekly-report.py's
    `ai_landing` field already runs live in production every Monday (single
    `landingPage` dimension + a sessionSource-based filter + a since/until
    window). ruling-1 on E-14 rejected the first version of this function,
    which asked GA4 for `isoYear`/`isoWeek` as REPORT DIMENSIONS to bucket
    by week in one call — this codebase has never made a live GA4 call with
    that 3-dimension combination, so the shape was guessed, not proven (the
    exact trap this project's own ledger names: verify a live shape before
    trusting a fixture of it). Bucketing by week in Python via 4 calls in an
    already-live-verified shape costs nothing and needs no new dimension
    name.

    A week whose call fails is `missing`, not a guessed zero (mirrors
    `verified_weekly`'s `partial` handling): `ok` only if all 4 weeks
    answered, `partial` if some did, `instrument_dead` if the client itself
    never came up or every week failed.

    ruling-2 on E-14: live GA4 `landingPage` values carry NO trailing slash
    (confirmed live shape, logs/aeo/cron-report.log:
    "/learn/how-do-crypto-debit-cards-work", "/reviews/gnosis-pay-card-review")
    while CANDIDATES.md's ranked paths DO ("/reviews/uphold-card-review/").
    A strict `path in out` check therefore matched nothing in production and
    returned a fake `status: ok` with a guessed zero for every page — exactly
    the class of bug this file's own docstring names. Both sides are
    normalized (trailing slash stripped) before comparison; the dict key
    returned to callers stays the original, slash-having path from `paths`."""
    try:
        wr = _load("weekly_report_a24", "scripts/weekly-report.py")
        client = wr._ga4_client()
    except Exception as e:  # noqa: BLE001 — missing GA4_PROPERTY_ID/creds
        return {}, {"status": "instrument_dead", "why": f"{type(e).__name__}: {e}"}
    out: dict[str, dict[str, int]] = {p: {} for p in paths}
    stripped_to_canonical = {p.rstrip("/"): p for p in paths}
    failed: list[str] = []
    for week in WEEKS:
        since, until = aeo.iso_week_bounds(week)
        try:
            rows = wr._run_report(client, ["landingPage"], ["sessions"],
                                  since.isoformat(), until.isoformat(),
                                  _chatgpt_filter(), limit=1000)
        except Exception as e:  # noqa: BLE001 — one dead week, not the whole leg
            failed.append(week)
            continue
        for p in paths:
            out[p][week] = 0
        for (landing,), (sessions,) in rows:
            raw_path = urlparse(landing).path or landing
            canonical = stripped_to_canonical.get(raw_path.rstrip("/"))
            if canonical:
                out[canonical][week] = out[canonical].get(week, 0) + int(sessions)
    if len(failed) == len(WEEKS):
        return {}, {"status": "instrument_dead", "why": "all weekly GA4 calls failed"}
    if failed:
        return out, {"status": "partial", "missing_weeks": failed}
    return out, {"status": "ok"}


def correlate(pairs: list[tuple[float, float]], min_n: int = MIN_PAGES) -> dict[str, Any]:
    """Pearson r over `pairs`, gated at `min_n`. Below it: the literal string
    "underpowered", never a number computed on too few points to mean
    anything (E-14's own words)."""
    n = len(pairs)
    if n < min_n:
        return {"n": n, "r": "underpowered"}
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    if len(set(xs)) < 2 or len(set(ys)) < 2:
        return {"n": n, "r": None, "note": "a constant series has no correlation"}
    return {"n": n, "r": round(statistics.correlation(xs, ys), 4)}


def build_report(candidates_path: Path = pc.CANDIDATES) -> dict[str, Any]:
    ranked = ranked_pages(candidates_path)
    paths = sorted(ranked)
    all_queries = {q for qs in ranked.values() for q in qs}

    bing_by_query, bing_status = bing_weekly(all_queries)
    page_bing = page_bing_weekly(ranked, bing_by_query) if bing_status["status"] == "ok" else {}

    verified_by_page_wk, verified_status = verified_weekly(set(paths))
    ga4_by_page_wk, ga4_status = ga4_weekly(set(paths))

    by_week: dict[str, Any] = {}
    for week in WEEKS:
        rows = []
        for p in paths:
            rows.append({
                "path": p,
                "queries": ranked[p],
                "bing_impressions": (page_bing.get(p, {}).get(week)
                                     if bing_status["status"] == "ok" else None),
                "verified_by_page": verified_by_page_wk.get(p, {}).get(week),
                "ga4_chatgpt_sessions": (ga4_by_page_wk.get(p, {}).get(week)
                                        if ga4_status["status"] in ("ok", "partial")
                                        else None),
            })
        bv_pairs = [(r["bing_impressions"], r["verified_by_page"]) for r in rows
                   if r["bing_impressions"] is not None and r["verified_by_page"] is not None]
        vg_pairs = [(r["verified_by_page"], r["ga4_chatgpt_sessions"]) for r in rows
                   if r["verified_by_page"] is not None and r["ga4_chatgpt_sessions"] is not None]
        by_week[week] = {
            "pages": rows,
            "correlation_bing_vs_verified": (
                correlate(bv_pairs) if bing_status["status"] == "ok"
                else {"n": 0, "r": "instrument_dead"}),
            "correlation_verified_vs_ga4_chatgpt": (
                correlate(vg_pairs) if ga4_status["status"] in ("ok", "partial")
                else {"n": 0, "r": "instrument_dead"}),
        }

    return {
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "hypothesis": "A2.4 (FABLE_DEFYCARD_PART2.md §B4.1) — numbers only, no verdict",
        "weeks": list(WEEKS),
        "min_pages_for_correlation": MIN_PAGES,
        "ranked_pages_source": "CANDIDATES.md Update-candidates block",
        "ranked_pages_n": len(paths),
        "instruments": {
            "bing_get_query_stats": bing_status,
            "aeo_verified_by_page": verified_status,
            "ga4_landing_chatgpt": ga4_status,
        },
        "by_week": by_week,
    }


def main() -> int:
    report = build_report(pc.CANDIDATES)  # live lookup, not build_report's frozen default
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"a24-check-{date.today().isoformat()}.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"ranked pages: {report['ranked_pages_n']}  "
         f"instruments: " + ", ".join(f"{k}={v['status']}"
                                      for k, v in report["instruments"].items()))
    for week in WEEKS:
        wk = report["by_week"][week]
        bv, vg = wk["correlation_bing_vs_verified"], wk["correlation_verified_vs_ga4_chatgpt"]
        print(f"  {week}  r(bing,verified) n={bv['n']} r={bv['r']}  "
             f"r(verified,chatgpt) n={vg['n']} r={vg['r']}")
    print(f"wrote {out_path}")
    # T-0142-04: a dead instrument must fail loudly (rc=1), never a quiet rc=0
    # standing in for "measured" — the same law this file's docstring already
    # names for a guessed zero, applied to the exit code.
    if any(v["status"] == "instrument_dead" for v in report["instruments"].values()):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
