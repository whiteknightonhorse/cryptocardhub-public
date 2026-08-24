"""Nightly analytics — close the auto-learning feedback loop.

Per shorts-analysis/SKILL.md §11 + feedback_loop_spec.md §3:

  1. Read video_pattern_log.jsonl — per-video record of which patterns were used.
  2. For each un-scored video published >= 24h ago and < 7 days ago:
     a. Pull view count + retention from YouTube Analytics API (Data + Analytics).
     b. Pull GA4 register_click conversion data (per utm_content = pattern_id).
     c. Score: success if (views >= tier_target) AND (retention >= 60 %).
     d. Per-pattern Beta(α, β) update via pattern_selector.record_video_outcome().
     e. Mark video as scored.
  3. Telegram alert on API failures (3+ days running → freeze weights).

Cron:
  daily   `0 2 * * *`  → main loop (this module's `main()`)
  monthly `0 0 1 * *`  → `--decay`: 15 % decay on every (α, β) for 6-month half-life

Auth: YouTube Data + Analytics API via OAuth refresh-token (Credentials WITHOUT
scopes= per youtube-longform invariant #19). GA4 Reporting API via the same
OAuth credentials if scope grants `analytics.readonly`; otherwise the GA4
branch is skipped gracefully and only YouTube view-count drives scoring.

Atomic state writes via fcntl.flock on creative_patterns_state.json.
"""
from __future__ import annotations
import argparse
import json
import math
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import _config, pattern_selector

_ROOT = _config.root()
_STATE_PATH = _ROOT / "state" / "creative_patterns_state.json"
_VIDEO_LOG_PATH = _ROOT / "state" / "video_pattern_log.jsonl"
_LOG_DIR = _ROOT.parent / "logs" / "analytics"

# 24h grace period — newly published videos haven't accumulated stable view counts
_SCORE_GRACE_HOURS = 24
# don't score videos older than 7 days (signals are stale by then)
_SCORE_WINDOW_DAYS = 7
# success threshold: views >= tier_target AND retention >= 60 %
_RETENTION_SUCCESS_THRESHOLD = 0.60
# monthly decay factor (15 % decay → 6-month half-life)
_DECAY_FACTOR = 0.85

# Tier-target view counts per shorts-analysis/SKILL.md §11.1
# Kept as the SECOND, "hard" level: written to the log, NOT fed to the weights.
_TIER_TARGETS = {
    "youtube":   {"TOFU": 500,  "MOFU": 250, "BOFU": 100, "VIRAL": 500},
    "instagram": {"TOFU": 1000, "MOFU": 400, "BOFU": 150, "VIRAL": 1000},
    "tiktok":    {"TOFU": 2000, "MOFU": 800, "BOFU": 300, "VIRAL": 2000},
}

# --- P0-3: relative success threshold -----------------------------------------
# Absolute tiers are unreachable on a channel with a median of 3 views: an
# instrument that always says "failure" teaches nothing. Success is now
# "top 25 % of this channel's own last 28 days" AND retention >= channel median.
_CHANNEL_WINDOW_DAYS = 28
_TOP_QUANTILE = 0.25
# Below this many scored videos in the window the ranking is noise → absolute tier.
_MIN_HISTORY = 8

# --- P0-4: publication circuit breaker ----------------------------------------
_HEALTH_WINDOW_DAYS = 14
_HEALTH_MIN_MEDIAN_VIEWS = 10
_SUPPRESSED_SLOTS_PER_DAY = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts() -> str:
    return _now().isoformat()


def _log(event: str, **kw: Any) -> None:
    """Append-only JSONL log to logs/analytics/<date>.jsonl."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = _LOG_DIR / f"{_now().strftime('%Y-%m-%d')}.jsonl"
    rec = {"ts": _ts(), "event": event, **kw}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")


def _tg_alert(msg: str) -> None:
    """Telegram alert on hard failures. Silent if no creds."""
    token = os.environ.get("TG_BOT_TOKEN")
    chat = os.environ.get("TG_CHAT_ID")
    if not token or not chat:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat, "text": f"[analytics] {msg}"},
            timeout=15,
        )
    except Exception:
        pass


# -----------------------------------------------------------------------------
# Video pattern log — append-only record of what each published video used
# -----------------------------------------------------------------------------

def append_video_log(video_id: str, platform: str, topic_id: int,
                     topic_class: str, patterns_used: dict[str, str],
                     transport: str = "direct") -> None:
    """Called by pipeline/main.py after a successful publish.

    Records the (video_id, platform, topic, patterns) tuple so analytics.py
    can later score the video and update the pattern weights.
    """
    _VIDEO_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "video_id": video_id,
        "platform": platform,
        "ts_published": _ts(),
        "topic_id": topic_id,
        "topic_class": topic_class,
        "patterns_used": patterns_used,
        "transport": transport,
        "scored": False,
        "score_attempts": 0,
    }
    with _VIDEO_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def _read_video_log() -> list[dict[str, Any]]:
    if not _VIDEO_LOG_PATH.exists():
        return []
    out: list[dict[str, Any]] = []
    with _VIDEO_LOG_PATH.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _write_video_log(entries: list[dict[str, Any]]) -> None:
    """Atomic rewrite — used after marking entries as scored."""
    tmp = _VIDEO_LOG_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
    tmp.replace(_VIDEO_LOG_PATH)


# -----------------------------------------------------------------------------
# YouTube Analytics + Data API
# -----------------------------------------------------------------------------

def _yt_credentials():
    """Per youtube-longform invariant #19: NO scopes= kwarg.

    Google returns invalid_scope if you list a scope that wasn't in the
    original grant. The refresh token already carries the scopes that were
    granted when the operator ran the auth flow.
    """
    from google.oauth2.credentials import Credentials
    return Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )


def _have_yt_creds() -> bool:
    return all(os.environ.get(k) for k in (
        "YOUTUBE_REFRESH_TOKEN", "YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET",
    ))


def pull_youtube_stats(video_id: str) -> dict[str, Any] | None:
    """Pulls view + engagement + retention metrics for one video.

    Returns dict with keys:
      view_count, like_count, comment_count, avg_view_duration_sec,
      avg_view_percentage, retention_60s

    Returns None if API auth missing OR API call fails OR video not found.
    """
    if not _have_yt_creds():
        return None
    try:
        from googleapiclient.discovery import build
        creds = _yt_credentials()
        # --- Data API v3 — static counters ---
        yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
        resp = yt.videos().list(part="statistics,contentDetails",
                                 id=video_id).execute()
        items = resp.get("items", [])
        if not items:
            return None
        stats = items[0].get("statistics", {})
        view_count = int(stats.get("viewCount", 0))
        like_count = int(stats.get("likeCount", 0))
        comment_count = int(stats.get("commentCount", 0))

        # --- Analytics API v2 — retention metrics ---
        # Best-effort; if the operator's token doesn't carry analytics.readonly,
        # we just skip retention and score on view count alone.
        avg_view_pct = None
        avg_view_dur = None
        try:
            yta = build("youtubeAnalytics", "v2", credentials=creds,
                        cache_discovery=False)
            today = _now().strftime("%Y-%m-%d")
            week_ago = (_now() - timedelta(days=7)).strftime("%Y-%m-%d")
            r = yta.reports().query(
                ids="channel==MINE",
                startDate=week_ago, endDate=today,
                metrics="averageViewPercentage,averageViewDuration",
                dimensions="video",
                filters=f"video=={video_id}",
            ).execute()
            rows = r.get("rows", [])
            if rows:
                # rows[0] = [video_id, avg_view_pct, avg_view_duration_sec]
                avg_view_pct = float(rows[0][1])
                avg_view_dur = float(rows[0][2])
        except Exception:
            pass

        return {
            "view_count": view_count,
            "like_count": like_count,
            "comment_count": comment_count,
            "avg_view_percentage": avg_view_pct,
            "avg_view_duration_sec": avg_view_dur,
            # Use avg_view_pct/100 as proxy for retention; conservative fallback 0.0
            "retention": (avg_view_pct or 0.0) / 100.0,
        }
    except Exception as e:
        _log("yt_stats_error", video_id=video_id, error=str(e)[:200])
        return None


# -----------------------------------------------------------------------------
# GA4 Reporting — per-utm_content register_click conversion
# -----------------------------------------------------------------------------

def pull_ga4_register_clicks(date_from: str, date_to: str) -> dict[str, int]:
    """Returns a dict {utm_content: count} for register_click events in the date
    range. utm_content carries the cta_bridge pattern_id so we can attribute.

    Returns {} if GA4 reporting isn't configured (graceful degradation).
    """
    property_id = os.environ.get("GA4_PROPERTY_ID")
    if not property_id or not _have_yt_creds():
        return {}
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import (
            DateRange, Dimension, Metric, RunReportRequest, FilterExpression,
            Filter,
        )
        creds = _yt_credentials()
        client = BetaAnalyticsDataClient(credentials=creds)
        req = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=date_from, end_date=date_to)],
            dimensions=[Dimension(name="customEvent:utm_content")],
            metrics=[Metric(name="eventCount")],
            dimension_filter=FilterExpression(
                filter=Filter(field_name="eventName",
                              string_filter=Filter.StringFilter(value="register_click"))
            ),
        )
        resp = client.run_report(req)
        out: dict[str, int] = {}
        for row in resp.rows:
            utm = row.dimension_values[0].value or "unknown"
            cnt = int(row.metric_values[0].value or 0)
            out[utm] = out.get(utm, 0) + cnt
        return out
    except Exception as e:
        _log("ga4_error", error=str(e)[:200])
        return {}


# -----------------------------------------------------------------------------
# Scoring + pattern weight update
# -----------------------------------------------------------------------------

def _tier_target(platform: str, topic_class: str) -> int:
    return _TIER_TARGETS.get(platform, {}).get(topic_class, 500)


def channel_history(platform: str = "youtube", days: int = _CHANNEL_WINDOW_DAYS,
                    now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """{video_id: {views, retention, success, ts}} from `scored` events in the
    last `days`. One record per video — the most recent event wins.

    The analytics JSONL log is the only place the channel's own measured
    performance lives; reading it back is what makes a *relative* threshold
    possible without a new state file.
    """
    now = now or _now()
    cutoff = now - timedelta(days=days)
    out: dict[str, dict[str, Any]] = {}
    if not _LOG_DIR.exists():
        return out
    for path in sorted(_LOG_DIR.glob("*.jsonl")):
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("event") != "scored" or rec.get("platform") != platform:
                    continue
                if rec.get("views") is None:
                    continue
                try:
                    ts = datetime.fromisoformat(rec["ts"])
                except (ValueError, KeyError):
                    continue
                if ts < cutoff or ts > now:
                    continue
                vid = rec.get("video_id")
                prev = out.get(vid)
                if prev and prev["ts"] >= ts:
                    continue
                out[vid] = {
                    "views": int(rec.get("views") or 0),
                    "retention": float(rec.get("retention") or 0.0),
                    "success": bool(rec.get("success")),
                    "ts": ts,
                }
    return out


def _top_quantile_ids(pool: dict[str, dict[str, Any]]) -> set[str]:
    """video_ids in the top `_TOP_QUANTILE` of the pool by view count.

    Rank-based, not value-based: on a channel where most videos have identical
    view counts a value quantile would mark the whole pool a success. Ties break
    deterministically by video_id, and the set is always a strict subset.
    """
    n = len(pool)
    if n < 2:
        return set()
    k = min(max(1, math.ceil(n * _TOP_QUANTILE)), n - 1)
    ranked = sorted(pool.items(), key=lambda kv: (-kv[1]["views"], kv[0]))
    return {vid for vid, _ in ranked[:k]}


def score_video(entry: dict[str, Any], stats: dict[str, Any] | None,
                history: dict[str, dict[str, Any]] | None = None) -> tuple[bool, str]:
    """Returns (success, reason). On no stats → (False, 'no_stats').

    Relative threshold (P0-3): success = this video lands in the top
    `_TOP_QUANTILE` of the channel's own last-28-day pool by views AND its
    retention is at or above the pool median. The absolute tier verdict is
    still computed and reported in `reason` as the hard level.
    """
    if not stats:
        return False, "no_stats"
    target = _tier_target(entry["platform"], entry.get("topic_class", "MOFU"))
    views = stats.get("view_count", 0)
    retention = stats.get("retention", 0.0)
    hard = views >= target and retention >= _RETENTION_SUCCESS_THRESHOLD

    if history is not None and len(history) >= _MIN_HISTORY:
        pool = dict(history)
        pool[entry["video_id"]] = {"views": views, "retention": retention}
        top = _top_quantile_ids(pool)
        med_ret = statistics.median(v["retention"] for v in pool.values())
        success = entry["video_id"] in top and retention >= med_ret
        return success, (
            f"rel top{int(_TOP_QUANTILE * 100)}%={entry['video_id'] in top} "
            f"views={views} retention={retention:.2f}>=med({med_ret:.2f}) "
            f"n={len(pool)} hard={hard}"
        )

    if hard:
        return True, f"views={views}>=tier({target}) AND retention={retention:.2f}>={_RETENTION_SUCCESS_THRESHOLD}"
    return False, (
        f"views={views}/{target} retention={retention:.2f}/{_RETENTION_SUCCESS_THRESHOLD} "
        f"(absolute — history<{_MIN_HISTORY})"
    )


def score_and_update(entry: dict[str, Any],
                     history: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Pull stats, score, update pattern weights. Returns the updated entry.

    Mutates `entry`: sets `scored=True`, `score_result`, `stats_pulled_at`.
    """
    if entry["platform"] == "youtube":
        stats = pull_youtube_stats(entry["video_id"])
    else:
        # IG + TT analytics paths not wired yet — see feedback_loop_spec.md §6
        # Fall back to skipping (no signal → no weight change).
        stats = None
    success, reason = score_video(entry, stats, history)
    if stats is None:
        # Track score attempts; auto-deprecate after 3 failed pulls
        entry["score_attempts"] = entry.get("score_attempts", 0) + 1
        entry["last_score_error"] = reason
        # Don't mark scored — retry tomorrow
        _log("score_skipped", video_id=entry["video_id"],
             platform=entry["platform"], reason=reason,
             attempts=entry["score_attempts"])
        return entry

    # Update pattern Beta(α, β) per axis
    pattern_selector.record_video_outcome(
        platform=entry["platform"],
        video_id=entry["video_id"],
        patterns_used=entry["patterns_used"],
        success=success,
    )

    entry["scored"] = True
    entry["score_result"] = {"success": success, "reason": reason, "stats": stats}
    entry["stats_pulled_at"] = _ts()
    _log("scored", video_id=entry["video_id"], platform=entry["platform"],
         success=success, reason=reason, views=stats.get("view_count"),
         retention=stats.get("retention"))
    return entry


# -----------------------------------------------------------------------------
# Decay (monthly cron)
# -----------------------------------------------------------------------------

def apply_decay(dry_run: bool = False) -> dict[str, int]:
    """Apply 15 % decay to every (α, β) in creative_patterns_state.json.

    Per shorts-analysis/SKILL.md §11.3 — gives recent performance more weight,
    6-month half-life.
    """
    state = pattern_selector._state()
    pat_state = state.get("patterns", {})
    n_decayed = 0
    for pid, runtime in pat_state.items():
        old_s = runtime.get("success_count", 0)
        old_f = runtime.get("failure_count", 0)
        runtime["success_count"] = old_s * _DECAY_FACTOR
        runtime["failure_count"] = old_f * _DECAY_FACTOR
        if old_s > 0 or old_f > 0:
            n_decayed += 1
    state["last_decay_at"] = _ts()
    if not dry_run:
        from .pattern_selector import _STATE_PATH as PSP
        PSP.parent.mkdir(parents=True, exist_ok=True)
        tmp = PSP.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(PSP)
    _log("decay", n_patterns_decayed=n_decayed, factor=_DECAY_FACTOR, dry_run=dry_run)
    return {"n_patterns_decayed": n_decayed, "factor": _DECAY_FACTOR}


# -----------------------------------------------------------------------------
# Main loop
# -----------------------------------------------------------------------------

def main_loop(dry_run: bool = False) -> dict[str, Any]:
    """Score all un-scored videos in [now-7d, now-24h] window."""
    entries = _read_video_log()
    now = _now()
    grace_cutoff = now - timedelta(hours=_SCORE_GRACE_HOURS)
    stale_cutoff = now - timedelta(days=_SCORE_WINDOW_DAYS)

    # Channel's own last-28-day pool — the reference the relative threshold
    # ranks each candidate against. Read once per run, not once per video.
    history = channel_history(days=_CHANNEL_WINDOW_DAYS, now=now)

    n_scored = 0
    n_skipped = 0
    n_in_window = 0
    updated_entries = []
    for entry in entries:
        if entry.get("scored"):
            updated_entries.append(entry)
            continue
        try:
            ts = datetime.fromisoformat(entry["ts_published"])
        except (ValueError, KeyError):
            updated_entries.append(entry)
            continue
        if ts > grace_cutoff:
            # Too fresh — let it accumulate
            updated_entries.append(entry)
            continue
        if ts < stale_cutoff:
            # Too old to score reliably — give up
            entry["scored"] = True
            entry["score_result"] = {"success": False, "reason": "stale_window_expired"}
            updated_entries.append(entry)
            continue

        n_in_window += 1
        # Auto-deprecate after 3 failed score attempts
        if entry.get("score_attempts", 0) >= 3:
            entry["scored"] = True
            entry["score_result"] = {"success": False, "reason": "max_score_attempts_reached"}
            updated_entries.append(entry)
            n_skipped += 1
            continue

        if dry_run:
            _log("dry_run_would_score", video_id=entry["video_id"],
                 platform=entry["platform"])
            updated_entries.append(entry)
            continue

        updated = score_and_update(entry, history)
        updated_entries.append(updated)
        if updated.get("scored"):
            n_scored += 1
        else:
            n_skipped += 1

    # P0-1: `updated_entries != entries` compared two lists holding the SAME
    # dict objects, mutated in place — always equal, so the log was never
    # rewritten and every video was re-scored nightly for its whole 7-day
    # window. Write unconditionally; the write is atomic and cheap.
    if not dry_run:
        _write_video_log(updated_entries)

    summary = {
        "ts": _ts(),
        "entries_total": len(entries),
        "in_window": n_in_window,
        "scored_now": n_scored,
        "skipped_for_retry": n_skipped,
        "history_size": len(history),
        "dry_run": dry_run,
    }
    _log("main_loop_summary", **summary)
    return summary


# -----------------------------------------------------------------------------
# P0-4 — publication circuit breaker
# -----------------------------------------------------------------------------

def channel_health(platform: str = "youtube", days: int = _HEALTH_WINDOW_DAYS,
                   now: datetime | None = None) -> dict[str, Any]:
    """Is the channel healthy enough to keep publishing at full cadence?

    Fail-closed: no measured history at all → treated as suppressed. A silent
    "no data, carry on" is exactly the failure mode that burned three months.
    """
    hist = channel_history(platform=platform, days=days, now=now)
    if not hist:
        return {"platform": platform, "n": 0, "median_views": None,
                "success_rate": None, "suppressed": True, "reason": "no_data"}
    views = [v["views"] for v in hist.values()]
    median_views = statistics.median(views)
    success_rate = sum(1 for v in hist.values() if v["success"]) / len(hist)
    suppressed = median_views < _HEALTH_MIN_MEDIAN_VIEWS
    return {
        "platform": platform,
        "n": len(hist),
        "median_views": median_views,
        "success_rate": round(success_rate, 4),
        "suppressed": suppressed,
        "reason": (f"median_views={median_views}<{_HEALTH_MIN_MEDIAN_VIEWS}"
                   if suppressed else
                   f"median_views={median_views}>={_HEALTH_MIN_MEDIAN_VIEWS}"),
    }


def _slots_fired_today(platform: str, now: datetime | None = None) -> int:
    """Count slots already fired today for a platform, per state/schedule.json."""
    from . import schedule as _schedule
    now = now or _now()
    today = now.astimezone(_schedule.ET).date().isoformat()
    slots = _schedule.load().get("by_platform", {}).get(platform, [])
    return sum(1 for s in slots
               if s.get("fired") and str(s.get("due_at_iso", "")).startswith(today))


def cadence_allowed(platform: str = "youtube",
                    now: datetime | None = None) -> tuple[bool, dict[str, Any]]:
    """Circuit breaker: while the channel is suppressed, at most
    `_SUPPRESSED_SLOTS_PER_DAY` slot per day gets through.

    This NEVER enables publishing — the master switch (VIDEO_GENERATOR_ENABLED)
    is checked before this gate is ever reached. It can only take slots away.
    """
    health = channel_health(platform=platform, now=now)
    if not health["suppressed"]:
        return True, {"allowed": True, **health, "fired_today": None}
    fired = _slots_fired_today(platform, now=now)
    allowed = fired < _SUPPRESSED_SLOTS_PER_DAY
    return allowed, {"allowed": allowed, "fired_today": fired,
                     "cap": _SUPPRESSED_SLOTS_PER_DAY, **health}


def _cadence_alert_once(platform: str, payload: dict[str, Any],
                        now: datetime | None = None) -> bool:
    """Telegram alert, at most once per platform per day (the watcher runs
    every 10 min — 144 identical alerts a day is the same as none)."""
    now = now or _now()
    marker = (_ROOT / "state" /
              f".cadence_alert_{platform}_{now.strftime('%Y-%m-%d')}")
    if marker.exists():
        return False
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(payload, default=str), encoding="utf-8")
    _tg_alert(
        f"channel suppressed — {platform} capped at {_SUPPRESSED_SLOTS_PER_DAY} "
        f"slot/day (experiment mode). median_views={payload.get('median_views')} "
        f"success_rate={payload.get('success_rate')} n={payload.get('n')}"
    )
    return True


# -----------------------------------------------------------------------------
# P0-2 — rebuild the poisoned Thompson state from real per-video outcomes
# -----------------------------------------------------------------------------

def rebuild_pattern_state(dry_run: bool = False) -> dict[str, Any]:
    """Rebuild creative_patterns_state.json from ONE outcome per video_id.

    Because of P0-1 every video was re-scored on each of the ~6 nights of its
    window, so each one paid its penalty ~6 times. Keep only the last verdict
    per video_id and recompute (success_count, failure_count) from scratch.
    """
    from .pattern_selector import _STATE_PATH as PSP
    state = pattern_selector._state()
    video_log: list[dict[str, Any]] = state.get("video_log", [])

    before = {
        "video_log": len(video_log),
        "unique_videos": len({v.get("video_id") for v in video_log}),
        "success": sum(p.get("success_count", 0) for p in state.get("patterns", {}).values()),
        "failure": sum(p.get("failure_count", 0) for p in state.get("patterns", {}).values()),
    }

    last: dict[str, dict[str, Any]] = {}
    for rec in video_log:            # file order == chronological append order
        vid = rec.get("video_id")
        if vid:
            last[vid] = rec

    patterns: dict[str, dict[str, float]] = {}
    for rec in last.values():
        for _axis, pid in (rec.get("patterns_used") or {}).items():
            rt = patterns.setdefault(pid, {"success_count": 0, "failure_count": 0})
            key = "success_count" if rec.get("success") else "failure_count"
            rt[key] += 1

    state["patterns"] = patterns
    state["video_log"] = list(last.values())
    state["rebuilt_at"] = _ts()

    after = {
        "video_log": len(state["video_log"]),
        "unique_videos": len(last),
        "success": sum(p["success_count"] for p in patterns.values()),
        "failure": sum(p["failure_count"] for p in patterns.values()),
    }

    if not dry_run:
        if PSP.exists():
            backup = PSP.with_name(
                f"{PSP.name}.bak-{_now().strftime('%Y-%m-%d')}")
            backup.write_text(PSP.read_text(encoding="utf-8"), encoding="utf-8")
        tmp = PSP.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(PSP)

    result = {"before": before, "after": after, "dry_run": dry_run}
    _log("rebuild_pattern_state", **result)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Nightly analytics + monthly decay")
    ap.add_argument("--decay", action="store_true",
                    help="Apply monthly 15 %% decay; skip scoring loop")
    ap.add_argument("--dry-run", action="store_true",
                    help="Read-only — no API calls, no state writes")
    ap.add_argument("--rebuild-state", action="store_true",
                    help="P0-2: rebuild pattern weights from one outcome per video")
    ap.add_argument("--cadence-gate", action="store_true",
                    help="P0-4: exit 0 if a slot may fire, 1 if the channel is suppressed")
    ap.add_argument("--platform", default="youtube")
    args = ap.parse_args(argv)

    try:
        if args.cadence_gate:
            allowed, payload = cadence_allowed(args.platform)
            if not allowed:
                _cadence_alert_once(args.platform, payload)
            _log("cadence_gate", **payload)
            print(json.dumps(payload, indent=2, default=str))
            return 0 if allowed else 1

        if args.rebuild_state:
            result = rebuild_pattern_state(dry_run=args.dry_run)
            print(json.dumps({"action": "rebuild_state", **result}, indent=2))
            return 0

        if args.decay:
            result = apply_decay(dry_run=args.dry_run)
            print(json.dumps({"action": "decay", **result}, indent=2))
            return 0

        result = main_loop(dry_run=args.dry_run)
        print(json.dumps({"action": "score", **result}, indent=2))
        if result.get("skipped_for_retry", 0) > 5:
            _tg_alert(f"{result['skipped_for_retry']} videos failed scoring; check API auth")
        return 0
    except Exception as e:
        _tg_alert(f"FAIL: {type(e).__name__}: {str(e)[:150]}")
        _log("main_error", error=str(e)[:300])
        raise


if __name__ == "__main__":
    sys.exit(main())
