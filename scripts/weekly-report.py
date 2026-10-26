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
import re
import statistics
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
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

# ---------------------------------------------------------------------------
# E-15 — YouTube traffic sources (insightTrafficSourceType) for H1
# ---------------------------------------------------------------------------
#
# Runbook R5 ("grant yt-analytics.readonly") is CANCELLED, 2026-09-14 — the
# scope was already granted: `tokeninfo` on YOUTUBE_REFRESH_TOKEN lists
# yt-analytics.readonly, and YouTube Analytics API v2 answers HTTP 200 for
# insightTrafficSourceType. impressions/CTR stay unavailable for an unrelated
# reason ("Unknown identifier (impressions)" — not in this API at all, not a
# scope gap) and R5 would not have fixed that either way.
#
# Baseline measured live 2026-09-14, the 30 days before H1's earliest
# possible start: EXT_URL sessions 2026-08-15..2026-09-13 = 1. Frozen as a
# literal so growth is provable, not felt (Y49's rule).
H1_TRAFFIC_BASELINE_DECLARED_ON = "2026-09-14"
H1_TRAFFIC_BASELINE_WINDOW = ("2026-08-15", "2026-09-13")
H1_TRAFFIC_BASELINE_EXT_URL = 1
# Facts about a financial product go stale. AEO-1 shows the verified date on
# the page; past this many days the page carries a visible overdue badge and
# this report carries the same line.
AVAILABILITY_STALE_DAYS = 60

# ---------------------------------------------------------------------------
# E-13 — CTA funnel, ether.fi cabinet export, click->registration assumption
# ---------------------------------------------------------------------------
#
# R7: the operator drops a monthly XLSX export from the ether.fi affiliate
# dashboard here by hand (T-0142-12 ruling-3: the cabinet only offers XLSX,
# and asking the operator to convert it is a manual step that eventually
# gets skipped). Sheet and column names are literals, verified against a
# real export (ruling-3 §"Что чем является") — not case-insensitive
# substring matching, so a renamed/missing sheet or column fails loudly as
# Missing instead of silently matching the wrong thing.
CABINET_SHEET_PERF = "Referral Performance"
CABINET_SHEET_REWARDS = "Affiliate Rewards"
CABINET_SHEET_PAYOUTS = "Rewards History"
CABINET_STALE_DAYS = 35
# B6 (FABLE_DEFYCARD_PART2.md): the plan's own stated conversion assumption —
# literals here so a change to the assumption is a visible diff, not a
# silently drifted constant.
CABINET_CLICKS_PER_REGISTRATION = 10
CABINET_REGISTRATIONS_PER_QUALIFIED = 3
# The day the register_click tracker went live (project memory:
# project_ga4_register_click.md) — the window B6 compares clicks against, so
# both sides of the ratio cover the same span instead of "clicks this week"
# vs "signups all-time" (ruling-3's "B6 сравнивается на одном окне").
CABINET_CLICKS_SINCE = "2026-05-06"

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
# Localization Phase-2 gate reminder (T-0137, ruling-1, 2026-09-21)
# ---------------------------------------------------------------------------
#
# localization/SKILL.md §0 says "check on every /handoff" — a gate that
# depends on a human remembering to open a skill file on the right day. This
# does not run the §0.1 checklist (that stays a human action, per the
# ruling); it only stops 2026-11-05 from passing unnoticed, on the surface
# the operator already reads every Monday. Dates are literals, matching §0's
# own formula verbatim (Y49: the law is a literal, not a re-derivation).
LOCALIZATION_START = date(2026, 5, 5)
LOCALIZATION_PHASE2_GATE = date(2026, 11, 5)


def localization_phase(today: date) -> dict[str, Any]:
    """Phase-1/Phase-2 status per localization/SKILL.md §0's own formula.

    Below the gate date: a countdown label, nothing else. On/after the gate
    date: `localization_gate` becomes Missing — action_required() then heads
    the report with it, same as any other unmeasured figure (Y57)."""
    months = ((today.year - LOCALIZATION_START.year) * 12
              + (today.month - LOCALIZATION_START.month))
    out: dict[str, Any] = {"months": months, "gate": LOCALIZATION_PHASE2_GATE.isoformat()}
    if today >= LOCALIZATION_PHASE2_GATE:
        out["localization_gate"] = Missing(
            "localization_gate",
            f"{LOCALIZATION_PHASE2_GATE.isoformat()} reached — run "
            "localization/SKILL.md §0.1 by hand, record the verdict in §0.4")
    else:
        out["label"] = f"Phase 1 (EN only) — month {months}/6"
        out["days_left"] = (LOCALIZATION_PHASE2_GATE - today).days
    return out


def render_localization(r: dict[str, Any]) -> str:
    loc = r.get("localization")
    if not isinstance(loc, dict):
        return "LOCALIZATION: n/a (not collected in this run)"
    lg = loc.get("localization_gate")
    if isinstance(lg, Missing):
        return f"LOCALIZATION: {lg}"
    return (f"LOCALIZATION: {loc['label']} · §0.1 gate {loc['gate']} "
            f"in {loc['days_left']} days")


# ---------------------------------------------------------------------------
# GA4 (read-only)
# ---------------------------------------------------------------------------

class Ga4SdkMissing(ImportError):
    """google-analytics-data is not importable from THIS interpreter — a wrong
    python3, not a dead GA4. The type NAME carries the cause into every alert
    that prints only type(e).__name__."""


def _ga4_client():
    os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(GA4_SA))
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
    except ImportError as e:
        raise Ga4SdkMissing(
            f"google-analytics-data not importable from {sys.executable}; "
            f"run with {ROOT / 'video-generator/venv/bin/python'}") from e
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


def _cta_view_filter():
    from google.analytics.data_v1beta.types import Filter, FilterExpression
    return FilterExpression(filter=Filter(
        field_name="eventName",
        string_filter=Filter.StringFilter(value="cta_view")))


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


def _load_affiliates_env() -> tuple[dict[str, Any], dict[str, str]]:
    """The affiliates registry plus a resolved view of the referral ${VAR}s.

    Shared by unmonetized_partners() and referral_gaps() — one parse of
    config/affiliates.yaml + .env, not two copies that could drift.
    """
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
    return aff.get("affiliates") or {}, env


def _empty_referral_var(prog: dict[str, Any], env: dict[str, str]) -> str | None:
    """The ${VAR} name if `prog`'s referral url is unset, else None."""
    m = re.fullmatch(r"\$\{(\w+)\}", prog.get("url", ""))
    if m and not (os.environ.get(m.group(1)) or env.get(m.group(1))):
        return m.group(1)
    return None


def unmonetized_partners() -> list[str]:
    """One line per partner serving its public url_fallback because the
    ${VAR} referral URL is empty in .env — those clicks earn 0 commission.

    config/affiliates.yaml promises this report carries that fact until the
    operator creates the referral link (found 2026-08-12: the promise was in
    the comment only). Restricted to partners that declare url_fallback:
    they are the ones actually shipping live links; an empty var on a partner
    with no fallback and no placements produces no clicks to mislabel.

    T-0142-10 (ruling-3): a partner may carry `unmonetized_note` — the
    reason the gap exists (e.g. cryptocom: the operator cannot join the
    programme at all, jurisdiction) — appended to the line with ` | `.
    """
    affiliates, env = _load_affiliates_env()
    out = []
    for slug, prog in affiliates.items():
        if "url_fallback" not in prog:
            continue
        var = _empty_referral_var(prog, env)
        if var is None:
            continue
        line = (f"{slug}: {var} is empty — clicks go to the public "
                f"page {prog['url_fallback']} and earn nothing")
        note = prog.get("unmonetized_note")
        if note:
            line += f" | {note}"
        out.append(line)
    return out


def _corpus_partner_references(slug: str) -> int:
    """Pages in seo-site/src/content/pseo referencing `slug`, via either
    `primary_partner` frontmatter or a tagged <<AFFILIATE_URL:slug>> marker
    in the body — the two ways a page actually ships a link to a partner."""
    corpus = ROOT / "seo-site" / "src" / "content" / "pseo"
    primary_re = re.compile(r'^primary_partner:\s*"' + re.escape(slug) + r'"', re.M)
    tag_re = re.compile(r"<<AFFILIATE_URL:" + re.escape(slug) + r">>")
    n = 0
    for path in corpus.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        if primary_re.search(text) or tag_re.search(text):
            n += 1
    return n


def referral_gaps() -> dict[str, Missing]:
    """Partners a live page links to for nothing — the ones ACTION REQUIRED
    should actually name (Y57: the header names an action, not just a fact).

    T-0142-10 (ruling-3, Q9.3): cryptocom's ${VAR} has been empty since
    forever and will stay that way — the operator cannot register in that
    programme at all (jurisdiction). Raising it every week trains the
    operator to stop reading the header ("permanent ACTION REQUIRED... is
    noise"). A partner flagged `referral_obtainable: false` in
    config/affiliates.yaml never raises here, no matter how many pages link
    it. plasma has no such flag: filling PLASMA_REF_URL genuinely fixes it —
    that IS the lever this header exists to point at.
    """
    affiliates, env = _load_affiliates_env()
    fallback_partner = None
    avail_path = ROOT / "config" / "availability.yaml"
    if avail_path.exists():
        import yaml
        fallback_partner = (
            yaml.safe_load(avail_path.read_text(encoding="utf-8"))
            .get("etherfi", {}).get("fallback_partner"))
    out: dict[str, Missing] = {}
    for slug, prog in affiliates.items():
        if "url_fallback" not in prog or prog.get("referral_obtainable") is False:
            continue
        var = _empty_referral_var(prog, env)
        if var is None:
            continue
        n = _corpus_partner_references(slug) + (1 if slug == fallback_partner else 0)
        if n < 1:
            continue
        out[slug] = Missing(
            f"referral_gaps.{slug}",
            f"{var} empty — {n} page{'s' if n != 1 else ''} link the public "
            "page and earn nothing")
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
    pause = 0
    funnel: dict[str, Any] = {}
    try:
        for f in sorted((ROOT / "logs" / "pseo").glob("*.jsonl")):
            # T-0142-03 (Fable ruling-1 REJECT): `f.stem` for the FAQ stream
            # is "faq-2026-09-20" -- lexically greater than any bare date, so
            # `since <= f.stem <= until` silently dropped every faq-*.jsonl
            # from the window (280 counted vs 559 measured on real logs).
            # Extract the date and compare that, not the raw stem.
            m = re.search(r"\d{4}-\d{2}-\d{2}", f.stem)
            if not m or not (since <= m.group(0) <= until):
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
                        # T-0142-03 (Fable ruling-2 REJECT): the FAQ stream's
                        # own queue_empty record ({"stream": "faq", "cohort",
                        # "due"}, no clusters_total) sorts after every bank
                        # 2026-*.jsonl file and was overwriting `funnel`
                        # unconditionally, wiping "clusters covered" from the
                        # report. Only the bank stream's record (the one that
                        # carries clusters_total) may set `funnel`.
                        if "clusters_total" in rec:
                            funnel = {k: rec[k] for k in
                                      ("clusters_total", "covered", "frozen", "thin")
                                      if k in rec}
                elif "queue empty" in line:
                    # Legacy plain-text form, written before the JSONL event
                    # existed. Counting only the new form would report eight
                    # days of idling as zero — the very silence being fixed.
                    idle += 1
                elif "skip: fleet_paused" in line:
                    # T-0142-03: 16-20.09 both streams sat in this branch for
                    # 6 straight days and the only trace was a cron log line
                    # nobody reads -- the zero has to carry its cause here too.
                    pause += 1
                elif "wrote " in line or "generated" in line.lower():
                    pages += 1
    except Exception as e:  # noqa: BLE001
        out["pseo"] = Missing("pseo", type(e).__name__)
        return out

    out["pseo"] = {"pages_7d": pages, "idle_ticks": idle, "pause_ticks": pause,
                   "queue": 0 if idle else None, **funnel}

    if pause > 0:
        out["pseo_pause"] = Missing(
            "pseo_pause",
            f"PAUSE: {pause} ticks skipped this week (fleet paused) — "
            f"see reason/by in ~/.fleet-paused-until while it is active",
        )

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
    """Bing demand funnel — read from CANDIDATES.md, not a second live hit.

    Fable 2026-09-14 (FABLE_DEFYCARD_ANSWER.md, A4 step 6): the old line
    counted `GetQueryStats` rows directly and told the operator to "run
    scripts/bing-keywords.py --write" -- the Monday 05:40 UTC cron already
    does, an hour before this report runs. What actually needed reporting was
    the FUNNEL that write produced (new-page candidates / update candidates /
    near-duplicates / below-floor), which `queries: N` alone could not show —
    07.09's "3 candidates" (one query, three weekly rows) looked identical to
    3 genuine candidates until the funnel was broken out (Y55's rule: a zero
    or a small number must carry its own cause on the surface a human reads).
    """
    p = ROOT / ".claude" / "skills" / "keywords" / "CANDIDATES.md"
    if not p.is_file():
        return {"bing": Missing("bing", "CANDIDATES.md not found — bing-keywords.py has not run yet")}
    try:
        text = p.read_text(encoding="utf-8")

        def _n(pattern: str) -> int:
            m = re.search(pattern, text)
            return int(m.group(1)) if m else 0

        return {"bing": {
            "queries": _n(r"queries returned by Bing.*?:\s*\*\*(\d+)\*\*"),
            "new": _n(r"new-page candidates:\s*(\d+)\*\*"),
            "update": _n(r"update candidates[^\n]*:\s*(\d+)\*\*"),
            "near_dup": _n(r"near-duplicate of an existing page:\s*(\d+)"),
            "below_floor": _n(r"below \d+ impressions.*?:\s*(\d+)"),
        }}
    except Exception as e:  # noqa: BLE001
        return {"bing": Missing("bing", type(e).__name__)}


INDEX_STATUS_MAX_AGE_DAYS = 10


def _collect_gsc_index() -> dict[str, Any]:
    """logs/gsc/index-status.json (gsc-report.py --inspect-all) -> one line's
    worth of figures, or Missing when absent/stale (heads ACTION REQUIRED)."""
    p = ROOT / "logs" / "gsc" / "index-status.json"
    if not p.is_file():
        return {"gsc_index": Missing(
            "gsc.index_status", "index-status.json not found — run gsc-report.py --inspect-all")}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        age = (date.today() - date.fromisoformat(d["run"])).days
    except Exception as e:  # noqa: BLE001
        return {"gsc_index": Missing("gsc.index_status", type(e).__name__)}
    if age > INDEX_STATUS_MAX_AGE_DAYS:
        return {"gsc_index": Missing(
            "gsc.index_status",
            f"index-status.json is {age} d old — run gsc-report.py --inspect-all")}
    return {"gsc_index": d}


def _collect_gsc() -> dict[str, Any]:
    """Google Search Console — the second demand instrument (E-12, Fable
    2026-09-14, FABLE_DEFYCARD_PART2.md wave 2). Reads logs/gsc/report.json,
    written by the Monday 06:30 UTC cron (scripts/gsc-report.py) ten minutes
    before this report runs — same rule as _collect_bing(): no second live
    hit against the API, read the funnel file the earlier cron already paid
    for.
    """
    p = ROOT / "logs" / "gsc" / "report.json"
    if not p.is_file():
        return {"gsc": Missing(
            "gsc", "logs/gsc/report.json not found — scripts/gsc-report.py has not run yet")}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"gsc": Missing("gsc", type(e).__name__)}
    if data.get("status") == "instrument_dead":
        return {"gsc": Missing("gsc", data.get("why", "instrument_dead"))}
    return {"gsc": data}


def _collect_a24() -> dict[str, Any]:
    """A2.4 hypothesis check (E-14, read-only, no verdict). Reads the latest
    logs/aeo/a24-check-*.json written by scripts/a24-check.py — same rule as
    _collect_bing()/_collect_gsc(): the report reads an artefact an earlier
    run already paid for, it never recomputes one (E-14 has no cron of its
    own; this is genuinely "not run yet" until a human runs the script)."""
    d = ROOT / "logs" / "aeo"
    files = sorted(d.glob("a24-check-*.json")) if d.is_dir() else []
    if not files:
        return {"a24": Missing(
            "a24", "logs/aeo/a24-check-*.json not found — scripts/a24-check.py has not run yet")}
    try:
        data = json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"a24": Missing("a24", type(e).__name__)}
    return {"a24": data}


def _collect_backup() -> dict[str, Any]:
    """Age/size/verify of the newest backups/daily-*.tar.gz (E-04).

    Same rule as every other figure here (Y57): a missing or unverifiable
    backup is Missing, not a line that quietly disappears — it heads the sent
    text as ACTION REQUIRED instead of blocking the report.
    """
    import subprocess
    d = ROOT / "backups"
    archives = sorted(d.glob("daily-*.tar.gz")) if d.is_dir() else []
    if not archives:
        return {"backup": Missing(
            "backup", "no backups/daily-*.tar.gz — run scripts/backup-daily.sh")}
    newest = max(archives, key=lambda p: p.stat().st_mtime)
    age_days = (date.today() - date.fromtimestamp(newest.stat().st_mtime)).days
    try:
        subprocess.run(["tar", "-tzf", str(newest)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        verified = True
    except Exception:  # noqa: BLE001
        verified = False
    if not verified:
        return {"backup": Missing(
            "backup", f"{newest.name} exists but failed tar -tzf verification")}
    return {"backup": {"path": newest.name, "age_days": age_days,
                       "size_bytes": newest.stat().st_size, "verified": verified}}


INTEL_UNREAD_DAYS = 7


def _collect_intel(today: date | None = None) -> dict[str, Any]:
    """Standing intel-source gaps (T-CCH-INTEL-429-1005). The daily Telegram
    only speaks on transitions; a source nobody has read for a week heads the
    Monday report with its cause and the action (Y57)."""
    import yaml
    today = today or date.today()
    try:
        srcs = (yaml.safe_load((ROOT / "config" / "intel_sources.yaml")
                               .read_text(encoding="utf-8")) or {}).get("sources") or []
    except Exception as exc:  # noqa: BLE001
        return {"config": Missing("intel", f"intel_sources.yaml unreadable: {type(exc).__name__}")}
    last_cause: dict[str, str] = {}
    ev = ROOT / "logs" / "intel" / "events.jsonl"
    if ev.is_file():
        for line in ev.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("kind") in ("fetch_failed", "extract_failed"):
                last_cause[e["source_id"]] = e.get("diff_summary", "")
    out: dict[str, Any] = {"total": len(srcs), "fresh": 0, "unread_ages": {}}
    for s in srcs:
        last_ok = s.get("last_ok")
        age = (today - date.fromisoformat(str(last_ok))).days if last_ok else None
        if age is not None and age <= INTEL_UNREAD_DAYS:
            out["fresh"] += 1
            continue
        out["unread_ages"][s["id"]] = "never" if age is None else f"{age} d"
        since = f"{last_ok} ({age} d)" if last_ok else "never"
        out[s["id"]] = Missing(
            f"intel.{s['id']}",
            f"unread since {since} — {last_cause.get(s['id'], 'no failure recorded')}; "
            f"action: save in Chrome → logs/intel/captures/, run intel-ingest.py "
            f"--from-file {s['id']} <path>")
    return out


def _collect_page_updates() -> dict[str, Any]:
    """Fable 2026-09-14, A4 step 4 — the strengthen-existing-page channel.

    Never Missing: an empty ledger is a legitimate, silence-free zero
    (Y57's rule — the figure heads the line, it does not block it)."""
    p = ROOT / "logs" / "pseo" / "page-updates.jsonl"
    applied = judged = improved = 0
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("event") == "applied":
                applied += 1
            elif row.get("event") == "verdict":
                judged += 1
                pb, pa = row.get("pos_before"), row.get("pos_after")
                if pb is not None and pa is not None and pa < pb:
                    improved += 1
    return {"page_updates": {"applied": applied, "judged": judged, "improved": improved}}


HUMANIZE_ORIGINAL_WIN_RATE = 0.40   # literal: blind judge preferring the original this often = lexicon is wrong
HUMANIZE_MIN_PAIRS = 10


def _collect_pinterest(root: Path = ROOT, today: date | None = None) -> dict[str, Any]:
    """CCH-PIN-02: manifest + logs/pinterest/feed.jsonl -> one line's figures.
    A missing file is Missing (heads ACTION REQUIRED, Y57) — never a 0."""
    man = root / "seo-site" / "src" / "data" / "pinterest-manifest.json"
    log = root / "logs" / "pinterest" / "feed.jsonl"
    if not man.is_file():
        return {"pinterest": Missing("pinterest", "pinterest-manifest.json not found — run scripts/pinterest-feed.py")}
    if not log.is_file():
        return {"pinterest": Missing("pinterest", "logs/pinterest/feed.jsonl not found — run scripts/pinterest-feed.py")}
    cutoff = ((today or date.today()) - timedelta(days=7)).isoformat()
    new = backfill = failed = 0
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("event") != "run" or str(r.get("date", "")) <= cutoff:
            continue
        new += r.get("candidates_new", 0)
        backfill += r.get("candidates_backfill", 0)
        failed += r.get("image_failed", 0)
    return {"pinterest": {"manifest": len(json.loads(man.read_text(encoding="utf-8"))),
                          "new": new, "backfill": backfill, "image_failed": failed}}


def _collect_humanize(root: Path = ROOT, today: date | None = None) -> dict[str, Any]:
    """CCH-HUM-07: the reader of logs/humanize (T-0140: a field nobody reads is a promise).

    Never Missing as a whole — an empty window is a legitimate zero. Only a lost
    blind audit becomes Missing("humanize_lexicon"), so action_required() heads the send.
    """
    d = root / "logs" / "humanize"
    cutoff = (today or date.today()) - timedelta(days=28)
    rows, audits = [], []
    for f in sorted(d.glob("*.jsonl")) if d.is_dir() else []:
        if f.name in ("audit.jsonl", "backfill.jsonl"):
            continue
        try:
            if date.fromisoformat(f.stem) < cutoff:
                continue
        except ValueError:
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("event") == "humanize":
                rows.append(r)
    a = d / "audit.jsonl"
    for line in a.read_text(encoding="utf-8").splitlines() if a.is_file() else []:
        try:
            r = json.loads(line)
            if date.fromisoformat(r["ts"][:10]) >= cutoff:
                audits.append(r)
        except (ValueError, KeyError, TypeError):
            continue
    wins = sum(1 for r in audits if r.get("verdict") == "original")
    med = lambda k: statistics.median([r.get(k, 0) for r in rows]) if rows else 0
    out: dict[str, Any] = {
        "pages": len(rows), "rewritten": sum(1 for r in rows if r.get("rewritten")),
        "refused": sum(1 for r in rows if r.get("refused_reason")),
        "median_flags_before": med("flags_before"), "median_flags_after": med("flags_after"),
        "audit_pairs": len(audits), "original_wins": wins,
    }
    if len(audits) >= HUMANIZE_MIN_PAIRS and wins / len(audits) >= HUMANIZE_ORIGINAL_WIN_RATE:
        out["humanize_lexicon"] = Missing(
            "humanize_lexicon", f"blind judge prefers the original in {wins} of {len(audits)} pairs")
    return {"humanize": out}


def _append_h1_traffic_history(rec: dict[str, Any]) -> None:
    """Rotation-proof weekly record — same shape as logs/aeo/history.jsonl.
    Without it EXT_URL is only ever compared against memory, not a series.
    Path built from ROOT at call time (like every other _collect_* helper
    here), not a module-level constant — so redirecting ROOT in a test
    redirects this too."""
    d = ROOT / "logs" / "video"
    d.mkdir(parents=True, exist_ok=True)
    with (d / "traffic-sources-history.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")


def _collect_h1_traffic(since: str, until: str, dry_run: bool = False) -> dict[str, Any]:
    """insightTrafficSourceType breakdown for the report window (E-15).

    This call IS the weekly snapshot the H1 instrument needs — this report
    already runs on the Monday cron, so no second cron is added. Every real
    call is appended to H1_TRAFFIC_HISTORY regardless of status, so an
    instrument_dead week is visible in the series, not silently absent.
    `--dry-run` never appends: a manual/test invocation is not the weekly
    cron and must not pollute the series it feeds H1's baseline comparison.
    """
    try:
        sys.path.insert(0, str(ROOT / "video-generator"))
        from pipeline import analytics as _vg_analytics
        result = _vg_analytics.pull_traffic_sources(since, until)
    except Exception as e:  # noqa: BLE001
        result = {"status": "instrument_dead", "error": f"{type(e).__name__}: {e}"}
    if not dry_run:
        _append_h1_traffic_history({"since": since, "until": until, **result})
    if result.get("status") != "ok":
        return {"h1_traffic": Missing("h1_traffic", result.get("error", "instrument_dead"))}
    data = result["data"]
    return {"h1_traffic": {
        "since": since, "until": until, "by_source": data,
        "ext_url": data.get("EXT_URL", 0),
    }}


def _youtube_source_filter():
    from google.analytics.data_v1beta.types import Filter, FilterExpression
    return FilterExpression(filter=Filter(
        field_name="sessionSource",
        string_filter=Filter.StringFilter(
            value="youtube.com", match_type=Filter.StringFilter.MatchType.CONTAINS)))


def _collect_h1_mode(client, since: str, until: str) -> dict[str, Any]:
    """E-20's pre-registered success line: `h1: videos N / median views /
    youtube->click` — 4 weeks from first publication, median views >= 10 AND
    >= 1 youtube.com -> register_click session (FABLE_DEFYCARD_PART2.md D7).

    `h1_videos`/`h1_median_views` cover the mode's WHOLE life (every
    `publish_log` row tagged `notes='h1'`, same convention `_collect_longform`
    already uses for its own tag), not a rolling 7-day window — the
    criterion is judged over 4 weeks from the first publish, not this week
    alone. `channel_history`'s default 28-day window matches that criterion
    exactly, so it is not overridden here.
    """
    out: dict[str, Any] = {}
    try:
        import sqlite3
        db = ROOT / "video-generator" / "state" / "published.db"
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as cx:
            rows = cx.execute(
                "SELECT external_id FROM publish_log WHERE notes = 'h1'").fetchall()
        video_ids = {v for (v,) in rows if v}
        out["h1_videos"] = len(video_ids)
        sys.path.insert(0, str(ROOT / "video-generator"))
        from pipeline import analytics
        hist = analytics.channel_history(platform="youtube")
        views = [v["views"] for vid, v in hist.items() if vid in video_ids]
        out["h1_median_views"] = (statistics.median(views) if views
                                  else Missing("h1_median_views", "no H1 videos scored yet"))
    except Exception as e:  # noqa: BLE001
        why = f"{type(e).__name__}: {e}"
        out["h1_videos"] = Missing("h1_videos", why)
        out["h1_median_views"] = Missing("h1_median_views", why)
    try:
        rows = _run_report(client, ["sessionSource"], ["sessions"], since, until,
                           _and(_click_filter(), _youtube_source_filter()))
        out["h1_youtube_clicks"] = sum(int(m[0] or 0) for _d, m in rows)
    except Exception as e:  # noqa: BLE001
        out["h1_youtube_clicks"] = Missing("h1_youtube_clicks", type(e).__name__)
    return out


def _collect_cta_funnel(client, since: str, until: str) -> list[dict[str, Any]]:
    """E-13: cta_view has fired since 2026-08-18 and nothing read it until
    now (FABLE_DEFYCARD_PART2.md line 38). Both events carry the standard
    `pagePath` param GA4 attaches to every hit, so views->clicks per page is
    a plain join — no new event params needed. Ranked client-side (like
    CONVERSION BY SOURCE above) since RunReportRequest here takes no
    order_bys: `_run_report` has always sorted its own callers' way.
    """
    views = {d[0]: int(m[0]) for d, m in _run_report(
        client, ["pagePath"], ["eventCount"], since, until, _cta_view_filter(), limit=200)}
    clicks = {d[0]: int(m[0]) for d, m in _run_report(
        client, ["pagePath"], ["eventCount"], since, until, _click_filter(), limit=200)}
    top = sorted(views.items(), key=lambda kv: -kv[1])[:10]
    return [{"path": p, "views": v, "clicks": clicks.get(p, 0)} for p, v in top]


_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_XLSX_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _xlsx_col(cell_ref: str) -> str:
    return "".join(ch for ch in cell_ref if ch.isalpha())


def _read_xlsx(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Minimal XLSX reader: zipfile + xml.etree, nothing else.

    Handles shared strings (`t="s"`), inline strings (`t="inlineStr"`), and
    plain numeric cells — the only three cell shapes a flat data export uses.
    Returns {sheet name: [{header: value}, ...]} using row 1 as the header.
    # ponytail: no formulas/merged cells — switch to openpyxl if the export
    # ever grows past flat rows (not installed here, not needed for 4
    # six-row sheets).
    """
    with zipfile.ZipFile(path) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rid_target = {r.get("Id"): r.get("Target") for r in rels}
        sheet_targets = {
            s.get("name"): rid_target[s.get(f"{_XLSX_REL_NS}id")]
            for s in wb.find(f"{_XLSX_NS}sheets")
        }
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in sst:
                shared.append("".join(t.text or "" for t in si.iter(f"{_XLSX_NS}t")))

        out: dict[str, list[dict[str, Any]]] = {}
        for name, target in sheet_targets.items():
            xml_path = target if target.startswith("xl/") else f"xl/{target}"
            sheet = ET.fromstring(z.read(xml_path))
            sheet_data = sheet.find(f"{_XLSX_NS}sheetData")
            rows: list[dict[str, Any]] = []
            for row_el in sheet_data:
                cells: dict[str, Any] = {}
                for c in row_el:
                    col = _xlsx_col(c.get("r"))
                    t = c.get("t")
                    if t == "s":
                        cells[col] = shared[int(c.find(f"{_XLSX_NS}v").text)]
                    elif t == "inlineStr":
                        is_el = c.find(f"{_XLSX_NS}is")
                        cells[col] = "".join(
                            tt.text or "" for tt in is_el.iter(f"{_XLSX_NS}t"))
                    else:
                        v = c.find(f"{_XLSX_NS}v")
                        if v is None or v.text is None:
                            cells[col] = None
                        else:
                            num = float(v.text)
                            cells[col] = int(num) if num.is_integer() else num
                rows.append(cells)
            if not rows:
                out[name] = []
                continue
            header = {col: str(val) for col, val in rows[0].items()}
            out[name] = [
                {header[col]: val for col, val in row.items() if col in header}
                for row in rows[1:]
            ]
        return out


def _excel_date(serial: float) -> date:
    """Excel's day-0 epoch (1899-12-30 — the classic off-by-two leap-year
    bug baked into every spreadsheet since Lotus 1-2-3)."""
    return date(1899, 12, 30) + timedelta(days=int(serial))


def _collect_cabinet() -> dict[str, Any]:
    """R7: manual XLSX export from the ether.fi affiliate cabinet.

    Export date = file mtime, not a parsed cell — same rule as
    _collect_backup(): mtime is what the operator actually controls (when
    the file was dropped), immune to whatever the export's own date column
    looks like. A missing sheet/column, or a cell that will not parse as a
    number, becomes Missing naming the sheet/column/file — the old CSV
    reader's `continue` on an unparsable cell was a silent zero (ruling-3).
    """
    d = ROOT / "state" / "etherfi-cabinet"
    xlsxs = list(d.glob("*.xlsx")) if d.is_dir() else []
    if not xlsxs:
        return {"cabinet": Missing(
            "cabinet", "no cabinet export in state/etherfi-cabinet/ — see Runbook R7")}
    newest = max(xlsxs, key=lambda p: p.stat().st_mtime)
    export_date = date.fromtimestamp(newest.stat().st_mtime)
    age_days = (date.today() - export_date).days
    if age_days > CABINET_STALE_DAYS:
        return {"cabinet": Missing(
            "cabinet",
            f"cabinet export older than {CABINET_STALE_DAYS} days "
            f"({newest.name}, {export_date.isoformat()})")}

    try:
        sheets = _read_xlsx(newest)
    except Exception as e:  # noqa: BLE001
        return {"cabinet": Missing(
            "cabinet", f"{newest.name} could not be read as xlsx: {type(e).__name__}")}

    def _sheet(name: str) -> list[dict[str, Any]] | None:
        return sheets.get(name)

    def _num(row: dict[str, Any], col: str, sheet_name: str) -> float:
        if col not in row:
            raise ValueError(
                f"column {col!r} not in sheet {sheet_name!r} of {newest.name} — "
                "export format changed? see Runbook R7")
        v = row[col]
        if not isinstance(v, (int, float)):
            raise ValueError(
                f"cell in column {col!r} of sheet {sheet_name!r} ({newest.name}) "
                f"is not numeric: {v!r}")
        return v

    def _cell_date(row: dict[str, Any], col: str, sheet_name: str) -> date:
        return _excel_date(_num(row, col, sheet_name))

    try:
        perf = _sheet(CABINET_SHEET_PERF)
        if perf is None:
            return {"cabinet": Missing(
                "cabinet", f"sheet {CABINET_SHEET_PERF!r} not in {newest.name} — "
                "export format changed? see Runbook R7")}
        signups = cardholders = spenders = 0
        last_signup = last_spender = None
        for row in perf:
            s = _num(row, "Signups", CABINET_SHEET_PERF)
            c = _num(row, "Cardholders", CABINET_SHEET_PERF)
            sp = _num(row, "Spenders", CABINET_SHEET_PERF)
            signups += s
            cardholders += c
            spenders += sp
            dt = _cell_date(row, "Date", CABINET_SHEET_PERF)
            if s > 0 and (last_signup is None or dt > last_signup):
                last_signup = dt
            if sp > 0 and (last_spender is None or dt > last_spender):
                last_spender = dt

        rewards = _sheet(CABINET_SHEET_REWARDS)
        if rewards is None:
            return {"cabinet": Missing(
                "cabinet", f"sheet {CABINET_SHEET_REWARDS!r} not in {newest.name} — "
                "export format changed? see Runbook R7")}
        qualified = 0
        earned_usd = 0.0
        last_qualified = None
        for row in rewards:
            bonus = _num(row, "New Member Bonus", CABINET_SHEET_REWARDS)
            total = _num(row, "Total", CABINET_SHEET_REWARDS)
            earned_usd += total
            if bonus > 0:
                qualified += 1
                dt = _cell_date(row, "Date", CABINET_SHEET_REWARDS)
                if last_qualified is None or dt > last_qualified:
                    last_qualified = dt

        payouts_sheet = _sheet(CABINET_SHEET_PAYOUTS)
        if payouts_sheet is None:
            return {"cabinet": Missing(
                "cabinet", f"sheet {CABINET_SHEET_PAYOUTS!r} not in {newest.name} — "
                "export format changed? see Runbook R7")}
        paid_usd = 0.0
        payouts = 0
        last_paid = None
        paid_for = None
        for row in payouts_sheet:
            amt = _num(row, "Amount (USD)", CABINET_SHEET_PAYOUTS)
            paid_usd += amt
            payouts += 1
            dt = _cell_date(row, "Paid Date (UTC)", CABINET_SHEET_PAYOUTS)
            if last_paid is None or dt > last_paid:
                last_paid = dt
                paid_for = row.get("Earnings for")
    except ValueError as e:
        return {"cabinet": Missing("cabinet", str(e))}

    return {"cabinet": {
        "signups": signups, "cardholders": cardholders, "spenders": spenders,
        "qualified": qualified, "earned_usd": round(earned_usd, 2),
        "paid_usd": round(paid_usd, 2), "payouts": payouts,
        "last_signup": last_signup.isoformat() if last_signup else None,
        "last_spender": last_spender.isoformat() if last_spender else None,
        "last_qualified": last_qualified.isoformat() if last_qualified else None,
        "last_paid": last_paid.isoformat() if last_paid else None,
        "paid_for": paid_for,
        "export_date": export_date.isoformat(), "file": newest.name,
    }}


def _collect_cabinet_clicks(client, until: str) -> int:
    """register_click events from CABINET_CLICKS_SINCE (tracker launch)
    through `until` — the same window as the cabinet's own signups, so B6
    compares apples to apples (ruling-3)."""
    rows = _run_report(client, [], ["eventCount"], CABINET_CLICKS_SINCE, until,
                       _click_filter())
    return int(rows[0][1][0]) if rows else 0


def collect(since: str, until: str, dry_run: bool = False) -> dict[str, Any]:
    """Every figure in the report. Any failure becomes Missing, never 0."""
    out: dict[str, Any] = {"since": since, "until": until}
    out["localization"] = localization_phase(date.today())

    try:
        client = _ga4_client()
    except Exception as e:  # noqa: BLE001
        why = f"GA4 client init failed: {type(e).__name__}"
        for k in ("human_sessions", "raw_sessions", "bot_direct", "by_source",
                  "clicks_by_partner", "clicks_by_source", "utm_content", "aeo",
                  "cta_funnel"):
            out[k] = Missing(k, why)
        out.update(_collect_cabinet())
        cab = out.get("cabinet")
        if isinstance(cab, dict):
            out["cabinet_clicks_since_launch"] = Missing("cabinet_clicks_since_launch", why)
        else:
            # ruling-3: the sister of the main-path else-branch below — the
            # key must never be absent here either, cabinet's own reason wins
            # over the GA4-client failure when the cabinet itself is Missing.
            out["cabinet_clicks_since_launch"] = Missing(
                "cabinet_clicks_since_launch",
                cab.why if isinstance(cab, Missing) else "cabinet not collected")
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

    # --- E-13: cta_view views -> register_click clicks, top 10 by views ---
    try:
        out["cta_funnel"] = _collect_cta_funnel(client, since, until)
    except Exception as e:  # noqa: BLE001
        out["cta_funnel"] = Missing("cta_funnel", type(e).__name__)

    out.update(_collect_cabinet())
    cab = out.get("cabinet")
    if isinstance(cab, dict):
        try:
            out["cabinet_clicks_since_launch"] = _collect_cabinet_clicks(
                client, cab["export_date"])
        except Exception as e:  # noqa: BLE001
            out["cabinet_clicks_since_launch"] = Missing(
                "cabinet_clicks_since_launch", type(e).__name__)
    else:
        # ruling-2: the key must never simply be absent when the cabinet
        # itself is Missing — that is a silent drop, not a Missing figure.
        out["cabinet_clicks_since_launch"] = Missing(
            "cabinet_clicks_since_launch",
            cab.why if isinstance(cab, Missing) else "cabinet not collected")

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

    try:
        out["referral_gaps"] = referral_gaps()
    except Exception as e:  # noqa: BLE001
        out["referral_gaps"] = Missing("referral_gaps", type(e).__name__)

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

    out.update(_collect_h1_traffic(since, until, dry_run=dry_run))
    out.update(_collect_h1_mode(client, since, until))
    out.update(_collect_longform(client, since, until))
    out.update(_collect_pseo(since, until))
    out.update(_collect_bing())
    out.update(_collect_gsc())
    out.update(_collect_gsc_index())
    out.update(_collect_a24())
    out.update(_collect_page_updates())
    try:
        out.update(_collect_humanize())
    except Exception as e:  # noqa: BLE001
        out["humanize"] = Missing("humanize", type(e).__name__)
    out.update(_collect_pinterest())
    out.update(_collect_backup())
    out["intel"] = _collect_intel()
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
        L.append(f"  claimed   {fw['ai_fetches']} in {fw['since']}..{fw['until']} "
                 f"= {fw['ai_fetches_per_day']}/day{delta}  (UA string match only — NOT the headline)")
        L.append(f"  pages     {fw['ai_pages_fetched']} distinct URLs, claimed")
        L.append("  by agent  " + ", ".join(f"{k} {v}" for k, v in fw["by_agent"].items()) + "  (claimed)")
        top = list(fw.get("by_bucket", {}).items())[:5]
        L.append("  top buckets " + ", ".join(f"{k} {v}" for k, v in top) + "  (claimed)")
        # VERIFIED — claimed AND the source IP is in the vendor's own published
        # range (see aeo-aggregate.py). This is the headline: never `ai_fetches`.
        if "verified_ai_fetches" in fw:
            vdelta = ""
            vprev = prev.get("verified_ai_fetches_per_day") if prev else None
            if vprev is not None and fw.get("verified_ai_fetches_per_day") is not None:
                vd = fw["verified_ai_fetches_per_day"] - vprev
                vdelta = f"  (prev wk {vprev}/day, {vd:+.1f})"
            L.append(f"  VERIFIED  {fw['verified_ai_fetches']} = "
                     f"{fw['verified_ai_fetches_per_day']}/day{vdelta}  <- use this number")
            L.append("  by agent  " + ", ".join(f"{k} {v}" for k, v in fw["verified_by_agent"].items())
                     + "  (verified)")
        else:
            L.append("  VERIFIED  not available for this week (pre-verification data, "
                     "raw logs since rotated — cannot be recovered)")

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
            # An alert that carries `apply` was computed from the issuer's own
            # page and can be replayed verbatim; one that does not still needs
            # a hand edit. The line says which, never just "there is a diff".
            fix = (f"accept with: scripts/availability-check.py --accept {row['id']}"
                   if row.get("apply") and row.get("id")
                   else "needs a HUMAN edit to config/availability.yaml")
            L.append(f"  DIFF vs ISSUER  {row.get('card')}: {row.get('summary')} "
                     f"— {fix}")
    return L


def render_gsc(r: dict[str, Any]) -> str:
    """The `google: ...` line — format owned by gsc-report.py (E-12), imported
    so it lives in exactly one place rather than being retyped here and
    drifting (the slugify() lesson: two copies of one format disagree)."""
    g = r.get("gsc")
    if g is None:
        return "google: n/a (not collected in this run)"
    if isinstance(g, Missing):
        return f"google: {g}"
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gsc_report", ROOT / "scripts" / "gsc-report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.render_line(g)


def render_gsc_index(r: dict[str, Any]) -> str:
    g = r.get("gsc_index")
    if g is None:
        return "google index: n/a (not collected in this run)"
    if isinstance(g, Missing):
        return f"google index: {g}"
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gsc_report", Path(__file__).with_name("gsc-report.py"))
    gsc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gsc)
    ok = lambda us: sum(1 for v in us if gsc.is_indexed(v.get("verdict", "")))
    vals = {u: v for u, v in g["urls"].items()}
    def depth_bucket(u):  # (bucket, is slug page)
        parts = u.split("://", 1)[-1].split("/", 1)[-1].strip("/").split("/")
        return parts[0], len(parts) >= 2
    content = [v for u, v in vals.items() if depth_bucket(u)[0] != "videos" and depth_bucket(u)[1]]
    videos = [v for u, v in vals.items() if depth_bucket(u)[0] == "videos" and depth_bucket(u)[1]]
    errors = sum(1 for v in vals.values() if v.get("coverage") == "instrument_error")
    return (f"google index ({g['run']}): indexed {ok(vals.values())}/{len(vals)} · "
            f"content {ok(content)}/{len(content)} · videos {ok(videos)}/{len(videos)} · "
            f"errors {errors}")


def render_crawl_supply(root: Path | None = None) -> str:
    root = root or ROOT
    from datetime import datetime, timezone
    def mdate(p):
        return datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d")
    def load(p):
        return json.loads(p.read_text(encoding="utf-8"))
    parts = []
    p = root / "logs" / "aeo" / "indexnow-state.json"
    try:
        parts.append(f"indexnow {mdate(p)} ({len(load(p))} urls hashed)")
    except Exception:  # noqa: BLE001
        parts.append(f"indexnow n/a ({p.name} missing)")
    p = root / "video-generator" / "state" / "bing-submitted.json"
    try:
        built = sum(1 for _ in (root / "seo-site" / "dist").rglob("index.html"))
        parts.append(f"bing-submit {mdate(p)} ({len(load(p))}/{built})")
    except Exception:  # noqa: BLE001
        parts.append(f"bing-submit n/a ({p.name} missing)")
    p = root / "logs" / "gsc" / "crawler-404s.json"
    try:
        d = load(p)
        parts.append(f"crawler-404s {len(d.get('urls', d)) if isinstance(d, dict) else len(d)}")
    except Exception:  # noqa: BLE001
        parts.append("crawler-404s n/a (CCH-SEO-02 not run)")
    return "crawl supply: " + " · ".join(parts)


def render_a24(r: dict[str, Any]) -> list[str]:
    """A2.4 (E-14) — numbers only, never a verdict; see a24-check.py's own
    docstring for why. One line per week: N pages behind each correlation
    and the correlation itself, or the literal `underpowered`/`instrument_dead`
    a24-check.py already wrote rather than a re-derived guess."""
    a = r.get("a24")
    if a is None:
        return ["A2.4 CHECK: n/a (not collected in this run)"]
    if isinstance(a, Missing):
        return [f"A2.4 CHECK: {a}"]
    L = [f"A2.4 CHECK (read-only, no verdict — {a['ranked_pages_n']} ranked pages, "
        f"weeks {a['weeks'][0]}..{a['weeks'][-1]}):"]
    for inst, status in a.get("instruments", {}).items():
        if status.get("status") != "ok":
            L.append(f"  {inst}: {status.get('status')} ({status.get('why', '')})")
    for week in a.get("weeks", []):
        wk = a.get("by_week", {}).get(week, {})
        bv = wk.get("correlation_bing_vs_verified", {})
        vg = wk.get("correlation_verified_vs_ga4_chatgpt", {})
        L.append(f"  {week}  r(bing,verified) n={bv.get('n')} r={bv.get('r')} | "
                 f"r(verified,chatgpt) n={vg.get('n')} r={vg.get('r')}")
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

    # 4. R6 candidates (T-0142-13) — measured, never chosen from here
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import r6_candidates
        out.append(r6_candidates.summary_line())
    except Exception as e:  # noqa: BLE001
        out.append(f"  R6 candidates    UNMEASURED ({type(e).__name__})")
    return out


def render(r: dict[str, Any]) -> str:
    L = [f"DefyCard weekly — {r['since']} .. {r['until']}", ""]

    L.append(render_localization(r))
    L.append("")

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

    # --- E-13: cta_view funnel, ether.fi cabinet export, the plan's own
    # clicks->registration assumption (B6). These three print unconditionally
    # (Y57): a Missing input becomes an n/a line here, never a silent drop.
    ct = r.get("cta_funnel")
    L.append("CTA FUNNEL (views -> clicks, top 10 by cta_view):")
    if ct is None:
        L.append("  n/a (not collected in this run)")
    elif isinstance(ct, Missing):
        L.append(f"  {ct}")
    elif not ct:
        L.append("  no cta_view events in window")
    else:
        for row in ct:
            L.append(f"  {row['path']:<42} {row['views']:>4} views -> {row['clicks']:>3} clicks")
    L.append("")

    cab = r.get("cabinet")
    L.append("CABINET (ether.fi affiliate export):")
    if cab is None:
        L.append("  n/a (not collected in this run)")
    elif isinstance(cab, Missing):
        L.append(f"  {cab}")
    else:
        L.append(f"  signups {cab['signups']} (last {cab['last_signup']}) / "
                 f"cardholders {cab['cardholders']} / "
                 f"spenders {cab['spenders']} (last {cab['last_spender']}) / "
                 f"qualified {cab['qualified']} (New Member Bonus rows, "
                 f"last {cab['last_qualified']})")
        L.append(f"  earned ${cab['earned_usd']:.2f} / paid ${cab['paid_usd']:.2f} "
                 f"in {cab['payouts']} payout(s) (last {cab['last_paid']}, "
                 f"for \"{cab['paid_for']}\")")

    clicks = r.get("cabinet_clicks_since_launch")
    if isinstance(cab, dict) and isinstance(clicks, int):
        expected_signups = clicks / CABINET_CLICKS_PER_REGISTRATION
        expected_qualified = cab["signups"] / CABINET_REGISTRATIONS_PER_QUALIFIED
        L.append(
            f"ASSUMPTION (B6): {clicks} clicks {CABINET_CLICKS_SINCE}..{cab['export_date']} / "
            f"{CABINET_CLICKS_PER_REGISTRATION} = {expected_signups:.1f} expected vs "
            f"{cab['signups']} signups; {cab['signups']} / "
            f"{CABINET_REGISTRATIONS_PER_QUALIFIED} = {expected_qualified:.1f} "
            f"expected qualified vs {cab['qualified']}")
    else:
        reason = clicks.why if isinstance(clicks, Missing) else "cabinet not collected"
        L.append(f"ASSUMPTION (B6): n/a ({reason})")
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
        if _ps.get("pause_ticks"):
            L.append(f"  PAUSE: {_ps.get('pause_ticks')} ticks skipped this week")
        if _ps.get("clusters_total") is not None:
            L.append(f"  clusters covered      {_ps.get('covered')}/{_ps.get('clusters_total')}"
                     f"  (frozen {_ps.get('frozen')}, thin {_ps.get('thin')})")

    _bg = r.get("bing")
    if _bg is not None:
        if isinstance(_bg, Missing):
            L.append(f"  bing queries          {_bg}")
        else:
            L.append(f"  bing: {_bg.get('queries', 0)} queries / new {_bg.get('new', 0)} / "
                     f"update {_bg.get('update', 0)} / near-dup {_bg.get('near_dup', 0)} / "
                     f"below-floor {_bg.get('below_floor', 0)}")

    L.append("  " + render_gsc(r))
    L.append("  " + render_gsc_index(r))
    L.append("  " + render_crawl_supply())

    L.append("")
    L.extend(render_a24(r))

    _pu = r.get("page_updates") or {"applied": 0, "judged": 0, "improved": 0}
    L.append(f"  UPDATES: {_pu.get('applied', 0)} applied / {_pu.get('judged', 0)} judged / "
             f"{_pu.get('improved', 0)} improved")

    _pn = r.get("pinterest")
    if isinstance(_pn, dict):
        L.append(f"  pinterest: manifest {_pn['manifest']} (+{_pn['new']} new, "
                 f"+{_pn['backfill']} backfill, 7d) image_failed {_pn['image_failed']}")

    _hz = r.get("humanize")
    if isinstance(_hz, dict):
        L.append(f"  HUMANIZE 28d: {_hz['pages']} pages / {_hz['rewritten']} rewritten / "
                 f"{_hz['refused']} refused / flags {_hz['median_flags_before']}->"
                 f"{_hz['median_flags_after']} / audit {_hz['original_wins']} original wins of "
                 f"{_hz['audit_pairs']} pairs")

    L.append("")
    _bk = r.get("backup")
    if _bk is None:
        L.append("BACKUP: n/a (not collected in this run)")
    elif isinstance(_bk, Missing):
        L.append(f"BACKUP: {_bk}")
    else:
        v = "verified" if _bk["verified"] else "VERIFY FAILED"
        L.append(f"BACKUP: {_bk['path']} ({_bk['age_days']}d old, "
                 f"{_bk['size_bytes']} bytes, {v})")
    _in = r.get("intel")
    if isinstance(_in, dict) and "total" in _in:
        _un = ", ".join(f"{k} {v}" for k, v in _in["unread_ages"].items())
        L.append(f"intel: {_in['total']} sources, {_in['fresh']} read ≤{INTEL_UNREAD_DAYS} d, "
                 f"{len(_in['unread_ages'])} unread" + (f" ({_un})" if _un else ""))
    else:
        L.append("intel: n/a (not collected in this run)")
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
    L.append("")

    L.append("H1 TRAFFIC SOURCES (insightTrafficSourceType, YouTube Analytics v2):")
    ht = r.get("h1_traffic")
    if ht is None:
        L.append("  n/a (not collected in this run)")
    elif isinstance(ht, Missing):
        L.append(f"  {ht}")
    else:
        L.append(f"  EXT_URL {ht['ext_url']}  (baseline {H1_TRAFFIC_BASELINE_EXT_URL} over "
                 f"{H1_TRAFFIC_BASELINE_WINDOW[0]}..{H1_TRAFFIC_BASELINE_WINDOW[1]}, "
                 f"declared {H1_TRAFFIC_BASELINE_DECLARED_ON})")
        top = sorted(ht["by_source"].items(), key=lambda kv: -kv[1])[:6]
        L.append("  by source " + (", ".join(f"{k} {v}" for k, v in top)
                                   if top else "no sources measured"))
    L.append("")

    # E-20's pre-registered line — n/a (not h1) is the honest default for a
    # report built before VG_MODE=h1 ever ran, same convention as H1_TRAFFIC
    # right above; Missing() renders its own cause once collect() has run.
    n, mv, yc = r.get("h1_videos"), r.get("h1_median_views"), r.get("h1_youtube_clicks")
    if n is None and mv is None and yc is None:
        L.append("h1: n/a (not collected in this run)")
    else:
        L.append(f"h1: videos {n} / median views {mv} / youtube→click {yc}")
    return "\n".join(L)


# Telegram's sendMessage caps `text` at 4096 chars (its own documented limit).
# The report grew past it (2685 bytes 09-07 -> 4434 09-14) and every send since
# has 400'd — the whole report silently stopped arriving, the Y57 "silent
# zero" one level up, now applied to the message itself rather than a figure
# inside it. Reserve room for the "[i/n]\n" part prefix so adding it can never
# push a chunk back over the real cap.
TELEGRAM_MAX_LEN = 4096
_PART_PREFIX_RESERVE = 20


def _chunk_message(text: str, limit: int = TELEGRAM_MAX_LEN - _PART_PREFIX_RESERVE) -> list[str]:
    """Split on line boundaries under `limit`; hard-cut a single line that
    alone exceeds it (none of this report's lines do, but the cut must exist
    rather than silently emit an over-cap chunk)."""
    chunks: list[str] = []
    cur = ""
    for line in text.split("\n"):
        piece = line
        while len(piece) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(piece[:limit])
            piece = piece[limit:]
        candidate = f"{cur}\n{piece}" if cur else piece
        if len(candidate) > limit:
            chunks.append(cur)
            cur = piece
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    return chunks


def send(report: dict[str, Any], text: str) -> None:
    """Transmit to Telegram. Unmeasured figures head the text, never block it.

    Split across multiple messages under Telegram's 4096-char cap — one
    oversized message used to 400 and take the whole report down with it.
    """
    header = action_required(report)
    if header:
        text = header + "\n\n" + text
    token, chat = os.environ.get("TG_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not token or not chat:
        raise RuntimeError("TG_BOT_TOKEN / TG_CHAT_ID not set")
    chunks = _chunk_message(text)
    n = len(chunks)
    for i, chunk in enumerate(chunks, 1):
        body = chunk if n == 1 else f"[{i}/{n}]\n{chunk}"
        data = urllib.parse.urlencode({"chat_id": chat, "text": body}).encode()
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

    report = collect(a.since, a.until, dry_run=a.dry_run)
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
