#!/usr/bin/env python3
"""Weekly DefyCard report (Y50 P0-2 + P0-3). READ-ONLY against GA4.

WHY THIS FILE EXISTS
--------------------
R1 called the site's traffic dead by reading nginx logs. GA4 then showed the
opposite. Two lessons are hard-coded here:

1. HUMAN SESSIONS, NEVER RAW SESSIONS.
   1 426 of 1 976 sessions over 2026-05-06..08-11 were `(direct)` with an 11 %
   engagement rate, 57 % from two datacentre countries and 86 % desktop
   Chrome — headless scanners that GA4's known-bot filter does not catch.
   Reporting "1 976 sessions" overstates the audience by ~2.8x. The only
   number this report will print is:

       human_sessions = non_direct_sessions + engaged_direct_sessions

   Historic scanner traffic cannot be deleted from GA4 (the data is
   immutable), so the correction lives in the reporting layer — here.

2. NO DEFAULTED FIGURES.
   Every number must come from GA4 or from local telemetry. If a fetch fails,
   the figure becomes MISSING and `send()` REFUSES to transmit. A report that
   silently substitutes 0 for "the API errored" is worse than no report: it
   reads as a real measurement and gets acted on.

Usage:
    weekly-report.py --dry-run          # build + print, never sends
    weekly-report.py                    # build + send to Telegram
    weekly-report.py --since 2026-05-06 --until 2026-08-11
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
GA4_SA = Path.home() / ".claude" / "ga4-sa.json"
# Идентификатор свойства читается из окружения, а не стоит в коде: у публичной копии
# репозитория нет причин нести внутренние номера оператора. Само по себе это не ключ —
# читать данные без OAuth им нельзя, — но и в исходнике ему делать нечего.
GA4_PROPERTY = "properties/" + os.environ["GA4_PROPERTY_ID"]

# ---------------------------------------------------------------------------
# AEO-2 — the answer-engine channel, and the thresholds it will be judged by
# ---------------------------------------------------------------------------
#
# THESE NUMBERS WERE DECLARED BEFORE ANY AEO CHANGE SHIPPED AND ARE NOT
# REVISABLE. A threshold that can be moved after the start is not a threshold;
# it is a way of always having succeeded. They are literals here, in the
# reporting code, so that moving one is a visible diff in a reviewed file.
#
# Baseline measured live from GA4 on the declaration date over the 8 weeks
# 2026-06-16..2026-08-10: chatgpt.com 26 + claude.ai 6 + perplexity.ai 1 = 33
# sessions; register_click from those sources = 9 (all chatgpt.com).
AEO_BASELINE_DECLARED_ON = "2026-08-11"
AEO_BASELINE_WINDOW = ("2026-06-16", "2026-08-10")   # 8 weeks
AEO_BASELINE_AI_SESSIONS = 33
AEO_BASELINE_AI_CLICKS = 9
AEO_VERDICT_WEEKS = 8
# "Worked" = 2x on BOTH. The channel is currently falling (June 26 -> August
# ~11/month), so merely holding the baseline is indistinguishable from noise.
AEO_SUCCESS_AI_SESSIONS = 66
AEO_SUCCESS_AI_CLICKS = 18
# "Partially" = 1.5x on EITHER (33*1.5 = 49.5 -> 50; 9*1.5 = 13.5 -> 14).
AEO_PARTIAL_AI_SESSIONS = 50
AEO_PARTIAL_AI_CLICKS = 14

# The literal source list. GA4 reports assistant referrals under these hostnames;
# `utm_source=chatgpt.com` is what OpenAI documents appending to its referrals.
AEO_AI_SOURCES = ("chatgpt.com", "chat.openai.com", "claude.ai", "perplexity.ai",
                  "copilot.microsoft.com", "gemini.google.com")

AEO_DIR = ROOT / "logs" / "aeo"
AEO_BASELINE_FILE = AEO_DIR / "baseline.json"
# Facts about a financial product go stale. AEO-1 shows the verified date on
# the page; past this many days the page carries a visible overdue badge and
# this report carries the same line.
AVAILABILITY_STALE_DAYS = 60

# ---------------------------------------------------------------------------
# The honest-metrics primitive
# ---------------------------------------------------------------------------

DIRECT_SOURCE = "(direct)"


def human_sessions(rows: list[dict[str, Any]]) -> int:
    """non-direct sessions + ENGAGED direct sessions.

    `rows` are GA4 sessionSource rows: {"source", "sessions", "engaged"}.

    Direct traffic here is overwhelmingly headless scanners, so a direct
    session only counts as a human once it proves engagement. Non-direct
    traffic arrived via a referrer a scanner rarely forges, so it counts in
    full. Do NOT "simplify" this to sum(sessions) — that is the bug.
    """
    total = 0
    for r in rows:
        if r["source"] == DIRECT_SOURCE:
            total += int(r["engaged"])
        else:
            total += int(r["sessions"])
    return total


def raw_sessions(rows: list[dict[str, Any]]) -> int:
    """Sum of all sessions. Diagnostic only — never report this as audience."""
    return sum(int(r["sessions"]) for r in rows)


def bot_direct_sessions(rows: list[dict[str, Any]]) -> int:
    """Direct sessions that never engaged — the scanner floor."""
    return sum(int(r["sessions"]) - int(r["engaged"])
               for r in rows if r["source"] == DIRECT_SOURCE)


# ---------------------------------------------------------------------------
# Defaulted-figure guard
# ---------------------------------------------------------------------------

class Missing:
    """A figure that could NOT be measured. Renders as n/a-with-cause and
    forces an ACTION REQUIRED header onto the sent report."""

    def __init__(self, what: str, why: str):
        self.what, self.why = what, why

    def __str__(self) -> str:
        return f"n/a ({self.why})"


def _unmeasured(report: dict[str, Any], prefix: str = "") -> list[str]:
    """Every Missing in the report, including inside nested sections.

    Y51: the AEO figures live under report["aeo"], one level down. A top-level-
    only scan would have let a whole section of n/a figures through to Telegram
    — the exact failure this guard exists to prevent.
    """
    bad: list[str] = []
    for k, v in report.items():
        key = f"{prefix}{k}"
        if isinstance(v, Missing):
            bad.append(f"{key}: {v.why}")
        elif isinstance(v, dict):
            bad.extend(_unmeasured(v, f"{key}."))
    return bad


def action_required(report: dict[str, Any]) -> str | None:
    """The header a report with unmeasured figures must carry, else None.

    Y57: the old guard REFUSED to send while anything was Missing — so the
    report went silent for weeks and the operator never saw which action
    (register the `partner` dimension, fill CRYPTOCOM_REF_URL) was awaited.
    A report that never arrives is the Y55 silent zero one level up. The law
    is now: a Missing figure never blocks the send and never renders as a
    number — it heads the report, with its cause, where a human reads it.
    """
    bad = _unmeasured(report)
    if not bad:
        return None
    return "\n".join(["ACTION REQUIRED — unmeasured figures:"]
                     + [f"  {b}" for b in bad])


# ---------------------------------------------------------------------------
# GA4 (read-only)
# ---------------------------------------------------------------------------

def _ga4_client():
    os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(GA4_SA))
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    return BetaAnalyticsDataClient()


def _run_report(client, dims, mets, since, until, dim_filter=None, limit=200):
    from google.analytics.data_v1beta.types import (
        DateRange, Dimension, Metric, RunReportRequest)
    req = RunReportRequest(
        property=GA4_PROPERTY,
        date_ranges=[DateRange(start_date=since, end_date=until)],
        dimensions=[Dimension(name=d) for d in dims],
        metrics=[Metric(name=m) for m in mets],
        dimension_filter=dim_filter,
        limit=limit,
    )
    r = client.run_report(req)
    return [([v.value for v in row.dimension_values],
             [v.value for v in row.metric_values]) for row in r.rows]


def _click_filter():
    from google.analytics.data_v1beta.types import Filter, FilterExpression
    return FilterExpression(filter=Filter(
        field_name="eventName",
        string_filter=Filter.StringFilter(value="register_click")))


# ---------------------------------------------------------------------------
# AEO-2 collection
# ---------------------------------------------------------------------------

def _ai_source_filter():
    from google.analytics.data_v1beta.types import Filter, FilterExpression
    return FilterExpression(filter=Filter(
        field_name="sessionSource",
        in_list_filter=Filter.InListFilter(values=list(AEO_AI_SOURCES))))


def _and(*exprs):
    from google.analytics.data_v1beta.types import (
        FilterExpression, FilterExpressionList)
    return FilterExpression(and_group=FilterExpressionList(expressions=list(exprs)))


def declare_start(day: str) -> dict[str, Any]:
    """Freeze the baseline + thresholds into logs/aeo/baseline.json, once.

    Written on deploy day, never rewritten: if the file already exists this is
    a no-op that returns what is already on disk. The verdict window is read
    from here, so the clock cannot be quietly restarted after a bad month.
    """
    AEO_DIR.mkdir(parents=True, exist_ok=True)
    if AEO_BASELINE_FILE.exists():
        return json.loads(AEO_BASELINE_FILE.read_text(encoding="utf-8"))
    end = date.fromisoformat(day) + timedelta(weeks=AEO_VERDICT_WEEKS)
    rec = {
        "declared_on": AEO_BASELINE_DECLARED_ON,
        "baseline_window": list(AEO_BASELINE_WINDOW),
        "baseline_ai_sessions": AEO_BASELINE_AI_SESSIONS,
        "baseline_ai_clicks": AEO_BASELINE_AI_CLICKS,
        "success_ai_sessions": AEO_SUCCESS_AI_SESSIONS,
        "success_ai_clicks": AEO_SUCCESS_AI_CLICKS,
        "partial_ai_sessions": AEO_PARTIAL_AI_SESSIONS,
        "partial_ai_clicks": AEO_PARTIAL_AI_CLICKS,
        "verdict_window": [day, end.isoformat()],
    }
    AEO_BASELINE_FILE.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    return rec


def verdict(ai_sessions: int, ai_clicks: int) -> str:
    """The §5 rule, applied to the accumulated verdict window. Literals only."""
    if ai_sessions >= AEO_SUCCESS_AI_SESSIONS and ai_clicks >= AEO_SUCCESS_AI_CLICKS:
        return "WORKED"
    if ai_sessions >= AEO_PARTIAL_AI_SESSIONS or ai_clicks >= AEO_PARTIAL_AI_CLICKS:
        return "PARTIAL"
    return "DID NOT WORK"


def availability_staleness(today: str) -> list[str]:
    """One line per card in config/availability.yaml whose check is overdue.

    Same rule and same threshold as the badge on /geo/availability-matrix/.
    The page is never silent about age, and neither is this report.
    """
    import yaml
    path = ROOT / "config" / "availability.yaml"
    cards = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("cards") or {}
    if not cards:
        return ["config/availability.yaml has no `cards:` section — "
                "the matrix has nothing to publish"]
    out = []
    for key, card in cards.items():
        v = card.get("verified")
        if not v:
            out.append(f"{key}: never verified — hidden from the matrix")
            continue
        age = (date.fromisoformat(today) - date.fromisoformat(str(v))).days
        if age > AVAILABILITY_STALE_DAYS:
            out.append(f"{key}: last verified {v} ({age}d ago) — OVERDUE")
    return out


def unmonetized_partners() -> list[str]:
    """One line per partner serving its public url_fallback because the
    ${VAR} referral URL is empty in .env — those clicks earn 0 commission.

    config/affiliates.yaml promises this report carries that fact until the
    operator creates the referral link (found 2026-08-12: the promise was in
    the comment only). Restricted to partners that declare url_fallback:
    they are the ones actually shipping live links; an empty var on a partner
    with no fallback and no placements produces no clicks to mislabel.
    """
    import re as _re

    import yaml
    aff = yaml.safe_load(
        (ROOT / "config" / "affiliates.yaml").read_text(encoding="utf-8"))
    env: dict[str, str] = {}
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    out = []
    for slug, prog in (aff.get("affiliates") or {}).items():
        if "url_fallback" not in prog:
            continue
        m = _re.fullmatch(r"\$\{(\w+)\}", prog.get("url", ""))
        if m and not (os.environ.get(m.group(1)) or env.get(m.group(1))):
            out.append(
                f"{slug}: {m.group(1)} is empty — clicks go to the public "
                f"page {prog['url_fallback']} and earn nothing"
            )
    return out


def _collect_aeo(client, since: str, until: str) -> dict[str, Any]:
    """Fetches (nginx, via aggregates) + sessions/clicks (GA4) + verdict progress."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "aeo_aggregate", ROOT / "scripts" / "aeo-aggregate.py")
    agg_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agg_mod)

    out: dict[str, Any] = {}

    # --- fetches: aggregates only, NEVER the raw logs ---
    # The logs rotate after ~15 days. Reading them here would make the report
    # unable to describe any week older than that, and would silently report
    # "0 fetches" for the weeks it can no longer see.
    week = agg_mod.last_complete_week(date.fromisoformat(until))
    cur = agg_mod.load_week(week)
    if cur is None:
        out["fetch_week"] = Missing(
            "fetch_week",
            f"no aggregate for {week} in logs/aeo/history.jsonl — "
            "run scripts/aeo-aggregate.py before this report")
    else:
        y, w = week.split("-W")
        prev_mon = date.fromisocalendar(int(y), int(w), 1) - timedelta(days=7)
        py, pw, _ = prev_mon.isocalendar()
        out["fetch_week"] = cur
        out["fetch_week_prev"] = agg_mod.load_week(f"{py}-W{pw:02d}")

    # --- sessions + clicks from the assistant sources ---
    try:
        rows = _run_report(client, ["sessionSource"], ["sessions", "engagedSessions"],
                           since, until, _ai_source_filter())
        out["ai_sessions"] = {d[0]: int(m[0]) for d, m in rows}
        out["ai_engaged"] = sum(int(m[1]) for _, m in rows)
    except Exception as e:  # noqa: BLE001
        out["ai_sessions"] = Missing("ai_sessions", type(e).__name__)
        out["ai_engaged"] = Missing("ai_engaged", type(e).__name__)

    try:
        out["ai_clicks"] = {
            d[0]: int(m[0])
            for d, m in _run_report(client, ["sessionSource"], ["eventCount"],
                                    since, until,
                                    _and(_click_filter(), _ai_source_filter()))
        }
    except Exception as e:  # noqa: BLE001
        out["ai_clicks"] = Missing("ai_clicks", type(e).__name__)

    try:
        out["ai_landing"] = {
            d[0]: int(m[0])
            for d, m in _run_report(client, ["landingPage"], ["sessions"],
                                    since, until, _ai_source_filter(), limit=10)
        }
    except Exception as e:  # noqa: BLE001
        out["ai_landing"] = Missing("ai_landing", type(e).__name__)

    # --- verdict progress, only once the clock has been started ---
    if not AEO_BASELINE_FILE.exists():
        out["verdict"] = Missing(
            "verdict", "verdict window not started — run weekly-report.py "
                       "--declare-start on the day the AEO deploy goes green")
        return out
    base = json.loads(AEO_BASELINE_FILE.read_text(encoding="utf-8"))
    w_since, w_until = base["verdict_window"]
    today = str(date.today())
    try:
        s = sum(int(m[0]) for _, m in _run_report(
            client, ["sessionSource"], ["sessions"], w_since,
            min(w_until, today), _ai_source_filter()))
        c = sum(int(m[0]) for _, m in _run_report(
            client, ["sessionSource"], ["eventCount"], w_since,
            min(w_until, today), _and(_click_filter(), _ai_source_filter())))
        out["verdict"] = {
            "window": [w_since, w_until],
            "complete": today >= w_until,
            "ai_sessions": s, "ai_clicks": c,
            "verdict": verdict(s, c),
        }
    except Exception as e:  # noqa: BLE001
        out["verdict"] = Missing("verdict", type(e).__name__)
    return out


def _collect_longform(client, since: str, until: str) -> dict[str, Any]:
    """Long-form publications and the sessions they sent (Y59).

    Written in the same commit as the publisher, not promised for later: a
    comment claiming a report covers something is code that must exist (Y57).
    """
    out: dict[str, Any] = {}
    try:
        import sqlite3
        db = ROOT / "video-generator" / "state" / "published.db"
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as cx:
            rows = cx.execute(
                "SELECT ts, external_id FROM publish_log "
                "WHERE notes = 'longform' AND ts >= ? AND ts <= ?",
                (since, until + "T23:59:59")).fetchall()
        out["longform_published"] = [{"ts": t, "video_id": v} for t, v in rows]
    except Exception as e:  # noqa: BLE001
        out["longform_published"] = Missing("longform_published", type(e).__name__)
    try:
        rows = _run_report(client, ["sessionMedium"], ["sessions"], since, until)
        out["longform_sessions"] = sum(int(m[0] or 0) for d, m in rows
                                       if d and d[0] == "longform")
    except Exception as e:  # noqa: BLE001
        out["longform_sessions"] = Missing("longform_sessions", type(e).__name__)
    return out


def _collect_pseo(since: str, until: str) -> dict[str, Any]:
    """The pSEO engine's own funnel, and the ACTION REQUIRED it now raises.

    The engine is not broken — it finished: 544 pages already cover every
    cluster its keyword bank produces, so `build_queue` returns empty and the
    probabilistic tick short-circuits. For eight days that produced 97 idle
    fires and zero pages, announced only as the words "queue empty" in a cron
    log. A completed contract still has to reach the surface a human reads
    (Y55/Y57), and the honest form of it is not a number but an expected
    action.

    Silenced two ways: top the bank up (queue > 0), or drop a dated
    `scripts/state/pseo-universe-complete` file to acknowledge the state.
    """
    out: dict[str, Any] = {}
    flag = ROOT / "scripts" / "state" / "pseo-universe-complete"
    idle = 0
    pages = 0
    funnel: dict[str, Any] = {}
    try:
        for f in sorted((ROOT / "logs" / "pseo").glob("*.jsonl")):
            if not (since <= f.stem <= until):
                continue
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("{"):
                    try:
                        rec = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    if rec.get("event") == "queue_empty":
                        idle += 1
                        funnel = {k: rec[k] for k in
                                  ("clusters_total", "covered", "frozen", "thin")
                                  if k in rec}
                elif "queue empty" in line:
                    # Legacy plain-text form, written before the JSONL event
                    # existed. Counting only the new form would report eight
                    # days of idling as zero — the very silence being fixed.
                    idle += 1
                elif "wrote " in line or "generated" in line.lower():
                    pages += 1
    except Exception as e:  # noqa: BLE001
        out["pseo"] = Missing("pseo", type(e).__name__)
        return out

    out["pseo"] = {"pages_7d": pages, "idle_ticks": idle,
                   "queue": 0 if idle else None, **funnel}

    if pages == 0 and idle > 0 and not flag.exists():
        cov = funnel.get("covered", "?")
        tot = funnel.get("clusters_total", "?")
        out["pseo_expansion"] = Missing(
            "pseo_expansion",
            f"queue empty {idle} ticks/7d — universe exhausted ({cov}/{tot} "
            f"clusters covered); expand the keyword bank from measured demand "
            f"(GSC impressions without a page, nginx 404s) or touch "
            f"scripts/state/pseo-universe-complete to acknowledge",
        )
    return out


def _collect_bing() -> dict[str, Any]:
    """Bing Webmaster query volume — the instrument the keyword bank waits on.

    ChatGPT search rides Bing's index and ChatGPT is 97% of this site's
    answer-engine traffic, so this is the closest measurable proxy for the
    channel that converts. Reported every week so the day data first appears
    is noticed by the operator rather than by whoever happens to look.
    """
    import urllib.parse, urllib.request
    key = os.environ.get("BING_WEBMASTER_API_KEY")
    if not key:
        return {"bing": Missing("bing", "BING_WEBMASTER_API_KEY not set")}
    try:
        q = urllib.parse.urlencode({"apikey": key, "siteUrl": "https://defycard.com"})
        with urllib.request.urlopen(
                f"https://ssl.bing.com/webmaster/api.svc/json/GetQueryStats?{q}",
                timeout=45) as r:
            rows = json.loads(r.read().decode()).get("d") or []
        return {"bing": {"queries": len(rows)}}
    except Exception as e:  # noqa: BLE001
        return {"bing": Missing("bing", type(e).__name__)}


def collect(since: str, until: str) -> dict[str, Any]:
    """Every figure in the report. Any failure becomes Missing, never 0."""
    out: dict[str, Any] = {"since": since, "until": until}

    try:
        client = _ga4_client()
    except Exception as e:  # noqa: BLE001
        why = f"GA4 client init failed: {type(e).__name__}"
        for k in ("human_sessions", "raw_sessions", "bot_direct", "by_source",
                  "clicks_by_partner", "clicks_by_source", "utm_content", "aeo"):
            out[k] = Missing(k, why)
        return out

    # --- sessions by source (the honest-metrics base) ---
    try:
        rows = [{"source": d[0], "sessions": int(m[0]), "engaged": int(m[1])}
                for d, m in _run_report(client, ["sessionSource"],
                                        ["sessions", "engagedSessions"], since, until)]
        out["by_source"] = rows
        out["human_sessions"] = human_sessions(rows)
        out["raw_sessions"] = raw_sessions(rows)
        out["bot_direct"] = bot_direct_sessions(rows)
    except Exception as e:  # noqa: BLE001
        why = f"sessionSource query failed: {type(e).__name__}"
        for k in ("by_source", "human_sessions", "raw_sessions", "bot_direct"):
            out[k] = Missing(k, why)

    # --- register_click, split by partner (P0-1 made `partner` a param) ---
    try:
        out["clicks_by_partner"] = {
            (d[0] or "(unset)"): int(m[0])
            for d, m in _run_report(client, ["customEvent:partner"], ["eventCount"],
                                    since, until, _click_filter())
        }
    except Exception as e:  # noqa: BLE001
        # Expected until the operator registers `partner` as a custom dimension
        # in GA4 Admin. Reported as MISSING, never as zero clicks.
        out["clicks_by_partner"] = Missing(
            "clicks_by_partner",
            "custom dimension 'partner' not registered in GA4 Admin "
            f"({type(e).__name__}) — register it to split clicks per program")

    try:
        out["clicks_by_source"] = {
            d[0]: int(m[0])
            for d, m in _run_report(client, ["sessionSource"], ["eventCount"],
                                    since, until, _click_filter())
        }
    except Exception as e:  # noqa: BLE001
        out["clicks_by_source"] = Missing("clicks_by_source", type(e).__name__)

    # --- which video drove the visit (P0-3: standard dim, no admin work) ---
    try:
        out["utm_content"] = {
            (d[0] or "(empty)"): int(m[0])
            for d, m in _run_report(client, ["sessionManualAdContent"],
                                    ["sessions"], since, until, limit=50)
        }
    except Exception as e:  # noqa: BLE001
        out["utm_content"] = Missing("utm_content", type(e).__name__)

    # --- AEO-2: the answer-engine channel ---
    try:
        out["aeo"] = _collect_aeo(client, since, until)
    except Exception as e:  # noqa: BLE001
        out["aeo"] = Missing("aeo", f"AEO section failed: {type(e).__name__}")

    try:
        out["availability_stale"] = availability_staleness(until)
    except Exception as e:  # noqa: BLE001
        out["availability_stale"] = Missing("availability_stale", type(e).__name__)

    try:
        out["unmonetized_partners"] = unmonetized_partners()
    except Exception as e:  # noqa: BLE001
        out["unmonetized_partners"] = Missing("unmonetized_partners", type(e).__name__)

    # --- video telemetry (local, not GA4) ---
    try:
        sys.path.insert(0, str(ROOT / "video-generator"))
        from pipeline import analytics
        # channel_history() windows on `scored` EVENTS, not publish dates: a
        # clip published in June that was re-scored yesterday is in here. With
        # publishing paused this count is "videos measured", not "videos
        # released" — labelling it "published" would invent a cadence that is
        # not running.
        hist = analytics.channel_history(platform="youtube", days=7)
        out["videos_7d"] = len(hist)
        views = [v["views"] for v in hist.values()]
        rets = [v["retention"] for v in hist.values() if v.get("retention") is not None]
        out["median_views_7d"] = (statistics.median(views) if views
                                  else Missing("median_views_7d", "no videos in window"))
        out["retention_7d"] = (round(statistics.median(rets), 3) if rets
                               else Missing("retention_7d", "no retention samples"))
    except Exception as e:  # noqa: BLE001
        why = f"video telemetry unavailable: {type(e).__name__}"
        for k in ("videos_7d", "median_views_7d", "retention_7d"):
            out[k] = Missing(k, why)

    out.update(_collect_longform(client, since, until))
    out.update(_collect_pseo(since, until))
    out.update(_collect_bing())
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_aeo(r: dict[str, Any]) -> list[str]:
    """The AEO-2 section: the funnel, then progress against the frozen rule.

    Funnel order is deliberate — fetches/day is two orders of magnitude above
    sessions/wk. Printing sessions alone hides that assistants read this site
    constantly and simply don't send the reader over.
    """
    a = r.get("aeo")
    L = ["AEO — ANSWER-ENGINE CHANNEL:"]
    if isinstance(a, Missing) or a is None:
        L.append(f"  {a if a is not None else Missing('aeo', 'section not collected')}")
        return L

    fw = a.get("fetch_week")
    if isinstance(fw, Missing):
        L.append(f"  fetches: {fw}")
    else:
        prev = a.get("fetch_week_prev")
        delta = ""
        if prev and prev.get("ai_fetches_per_day"):
            d = fw["ai_fetches_per_day"] - prev["ai_fetches_per_day"]
            delta = f"  (prev wk {prev['ai_fetches_per_day']}/day, {d:+.1f})"
        L.append(f"  fetches   {fw['ai_fetches']} in {fw['since']}..{fw['until']} "
                 f"= {fw['ai_fetches_per_day']}/day{delta}")
        L.append(f"  pages     {fw['ai_pages_fetched']} distinct URLs fetched by assistants")
        L.append("  by agent  " + ", ".join(f"{k} {v}" for k, v in fw["by_agent"].items()))
        top = list(fw.get("by_bucket", {}).items())[:5]
        L.append("  top buckets " + ", ".join(f"{k} {v}" for k, v in top))

    s, c = a.get("ai_sessions"), a.get("ai_clicks")
    if isinstance(s, Missing) or isinstance(c, Missing):
        L.append(f"  sessions/clicks: {s if isinstance(s, Missing) else c}")
    else:
        tot_s, tot_c = sum(s.values()), sum(c.values())
        rate = f"{100 * tot_c / tot_s:.1f}%" if tot_s else "—"
        L.append(f"  sessions  {tot_s} this window ({', '.join(f'{k} {v}' for k, v in s.items()) or 'none'})")
        L.append(f"  clicks    {tot_c} ({rate} of AI sessions)")

    land = a.get("ai_landing")
    if not isinstance(land, Missing) and land:
        L.append("  landing   " + ", ".join(f"{k} {v}" for k, v in list(land.items())[:4]))

    v = a.get("verdict")
    if isinstance(v, Missing):
        L.append(f"  verdict   {v}")
    elif v:
        L.append(f"  verdict window {v['window'][0]}..{v['window'][1]} "
                 f"({'COMPLETE' if v['complete'] else 'in progress'})")
        L.append(f"    sessions {v['ai_sessions']} / need {AEO_SUCCESS_AI_SESSIONS} "
                 f"(baseline {AEO_BASELINE_AI_SESSIONS}, declared {AEO_BASELINE_DECLARED_ON})")
        L.append(f"    clicks   {v['ai_clicks']} / need {AEO_SUCCESS_AI_CLICKS} "
                 f"(baseline {AEO_BASELINE_AI_CLICKS})")
        L.append(f"    -> {v['verdict']}"
                 + ("" if v["complete"] else "  (provisional — window still open)"))

    stale = r.get("availability_stale")
    if isinstance(stale, Missing):
        L.append(f"  availability: {stale}")
    elif stale:
        L.extend([f"  STALE DATA  {line}" for line in stale])
    else:
        L.append(f"  availability: all cards verified within {AVAILABILITY_STALE_DAYS} days")

    alerts = ROOT / "logs" / "aeo" / "availability-alerts.json"
    if alerts.exists():
        try:
            rows = json.loads(alerts.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rows = []
        for row in rows:
            L.append(f"  DIFF vs ISSUER  {row.get('card')}: {row.get('summary')} "
                     f"— needs a HUMAN edit to config/availability.yaml")
    return L


def _streams_lines() -> list[str]:
    """One figure per stream. Unmeasured is said out loud, never rendered as 0."""
    import json as _json
    from datetime import date as _date, timedelta as _td
    out = []

    # 1. truth — how recently the issuer was actually checked
    try:
        import yaml as _yaml
        rates = _yaml.safe_load((ROOT / "config" / "rates.yaml").read_text(encoding="utf-8"))
        checked = (rates.get("source") or {}).get("verified")
        age = (_date.today() - _date.fromisoformat(str(checked))).days
        alerts = ROOT / "logs" / "aeo" / "availability-alerts.json"
        n_alerts = len(_json.loads(alerts.read_text(encoding="utf-8"))) if alerts.exists() else 0
        out.append(f"  truth      rates verified {checked} ({age}d ago), "
                   f"{n_alerts} open divergence(s)")
    except Exception as e:  # noqa: BLE001
        out.append(f"  truth      UNMEASURED ({type(e).__name__})")

    # 2. FAQ children — the cohort and whether it has been read
    try:
        led = _json.loads((ROOT / "logs" / "pseo" / "faq-cohorts.json")
                          .read_text(encoding="utf-8"))
        cohorts = led.get("cohorts") or []
        if not cohorts:
            out.append("  children   no cohort opened yet")
        else:
            last = cohorts[-1]
            published = sum(
                1 for i in last["items"]
                if (ROOT / "seo-site" / "src" / "content" / "pseo"
                    / f"{i['slug']}.md").exists())
            refused = sum(1 for i in last["items"] if i.get("refused"))
            v = last.get("verdict_14") or last.get("verdict_28")
            verdict = v["status"] if v else f"unread, due {last.get('verdict_due_14')}"
            out.append(f"  children   cohort {len(cohorts)}: {published}/"
                       f"{len(last['items'])} published, {refused} refused by the "
                       f"delta gate; verdict {verdict}")
    except Exception as e:  # noqa: BLE001
        out.append(f"  children   UNMEASURED ({type(e).__name__})")

    # 3. digest — did last month have anything to say
    try:
        journal = ROOT / "logs" / "aeo" / "fact-journal.jsonl"
        n = 0
        if journal.is_file():
            cutoff = _date.today() - _td(days=31)
            for line in journal.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = _json.loads(line)
                if _date.fromisoformat(row["observed"]) >= cutoff:
                    n += 1
        out.append(f"  digest     {n} market change(s) journalled in 31d "
                   f"({'a digest is due' if n else 'a quiet month gets no page'})")
    except Exception as e:  # noqa: BLE001
        out.append(f"  digest     UNMEASURED ({type(e).__name__})")
    return out


def render(r: dict[str, Any]) -> str:
    L = [f"DefyCard weekly — {r['since']} .. {r['until']}", ""]

    hs, rs, bd = r["human_sessions"], r["raw_sessions"], r["bot_direct"]
    L.append(f"HUMAN SESSIONS: {hs}")
    if not isinstance(hs, Missing) and not isinstance(rs, Missing):
        L.append(f"  raw sessions {rs} — {rs - hs} discarded as non-human")
        L.append(f"  bot-direct (direct, never engaged): {bd}")
    L.append("")

    src = r.get("by_source")
    clicks = r.get("clicks_by_source")
    if not isinstance(src, Missing) and not isinstance(clicks, Missing):
        L.append("CONVERSION BY SOURCE (clicks / human sessions):")
        for row in sorted(src, key=lambda x: -x["sessions"])[:8]:
            s = row["source"]
            hu = row["engaged"] if s == DIRECT_SOURCE else row["sessions"]
            c = clicks.get(s, 0)
            pct = f"{100 * c / hu:.1f}%" if hu else "—"
            L.append(f"  {s:<22} {c:>3} / {hu:<5} {pct}")
    else:
        L.append(f"CONVERSION BY SOURCE: {src if isinstance(src, Missing) else clicks}")
    L.append("")

    cbp = r["clicks_by_partner"]
    L.append("CLICKS BY PARTNER:")
    if isinstance(cbp, Missing):
        L.append(f"  {cbp}")
    else:
        for k, v in sorted(cbp.items(), key=lambda kv: -kv[1]):
            L.append(f"  {k:<22} {v}")
    L.append("")
    L.append("THREE STREAMS (Y66 — what keeps the site alive):")
    for line in _streams_lines():
        L.append(line)
    L.append("")
    L.append("pSEO ENGINE:")
    _ps = r.get("pseo")
    if _ps is None:
        L.append("  n/a (not collected in this run)")
    elif isinstance(_ps, Missing):
        L.append(f"  {_ps}")
    else:
        L.append(f"  pages this week       {_ps.get('pages_7d')}")
        L.append(f"  idle ticks            {_ps.get('idle_ticks')}")
        if _ps.get("clusters_total") is not None:
            L.append(f"  clusters covered      {_ps.get('covered')}/{_ps.get('clusters_total')}"
                     f"  (frozen {_ps.get('frozen')}, thin {_ps.get('thin')})")

    _bg = r.get("bing")
    if _bg is not None:
        if isinstance(_bg, Missing):
            L.append(f"  bing queries          {_bg}")
        else:
            n = _bg.get("queries", 0)
            L.append(f"  bing queries          {n}"
                     + ("  <- DATA HAS ARRIVED: run scripts/bing-keywords.py --write"
                        if n else "  (none yet — Bing is still accumulating)"))

    L.append("")
    L.append("LONG-FORM:")
    _lfp = r.get("longform_published")
    if _lfp is None:
        # An absent key is not a zero. A renderer that crashes on one takes the
        # whole report down, which is the loudest way to report nothing at all.
        L.append("  n/a (not collected in this run)")
    elif isinstance(_lfp, Missing):
        L.append(f"  {_lfp}")
    else:
        L.append(f"  published this week   {len(_lfp)}")
        for _row in _lfp:
            L.append(f"    {_row['ts'][:10]}  https://youtu.be/{_row['video_id']}")
    _lfs = r.get("longform_sessions")
    if _lfs is not None:
        L.append(f"  sessions from longform  {_lfs}")
    L.append("")

    unm = r.get("unmonetized_partners") or []
    if isinstance(unm, Missing):
        L.append(f"  unmonetized: {unm}")
    else:
        L.extend(f"  $0 COMMISSION  {line}" for line in unm)
    L.append("")

    uc = r["utm_content"]
    L.append("BY VIDEO (utm_content -> sessionManualAdContent):")
    if isinstance(uc, Missing):
        L.append(f"  {uc}")
    else:
        real = {k: v for k, v in uc.items() if k not in ("(not set)", "(empty)", "")}
        L.extend([f"  {k:<22} {v}" for k, v in sorted(real.items(), key=lambda kv: -kv[1])]
                 or ["  no tagged video traffic yet"])
    L.append("")

    L.extend(render_aeo(r))
    L.append("")

    L.append("VIDEO (scored in last 7d — NOT a publish count):")
    L.append(f"  videos measured  {r['videos_7d']}")
    L.append(f"  median views     {r['median_views_7d']}")
    L.append(f"  median retention {r['retention_7d']}")
    return "\n".join(L)


def send(report: dict[str, Any], text: str) -> None:
    """Transmit to Telegram. Unmeasured figures head the text, never block it."""
    header = action_required(report)
    if header:
        text = header + "\n\n" + text
    token, chat = os.environ.get("TG_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not token or not chat:
        raise RuntimeError("TG_BOT_TOKEN / TG_CHAT_ID not set")
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    urllib.request.urlopen(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=30
    ).read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print, never send")
    ap.add_argument("--since", default=str(date.today() - timedelta(days=7)))
    ap.add_argument("--until", default=str(date.today()))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--declare-start", action="store_true",
                    help="freeze the AEO baseline + 8-week verdict window into "
                         "logs/aeo/baseline.json (deploy day, once — never rewrites)")
    a = ap.parse_args()

    if a.declare_start:
        rec = declare_start(str(date.today()))
        print(json.dumps(rec, indent=2))
        print(f"\nverdict window: {rec['verdict_window'][0]} .. {rec['verdict_window'][1]}")
        return 0

    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

    report = collect(a.since, a.until)
    text = render(report)
    if a.json:
        print(json.dumps({k: (str(v) if isinstance(v, Missing) else v)
                          for k, v in report.items()}, indent=2, default=str))
    print(text)

    if a.dry_run:
        missing = [b.split(":")[0] for b in _unmeasured(report)]
        print(f"\n[dry-run] not sent. unmeasured figures: {missing or 'none'}")
        return 0
    send(report, text)
    print("\nsent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
