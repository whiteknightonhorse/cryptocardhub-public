#!/usr/bin/env python3
"""daily-series.py — daily verified-fetch series over the raw-log snapshot.

Built for the 2026-09-01 collision-audit-correction follow-up (Fable's ruling):
classify the SHAPE of the daily ChatGPT-User series (decay vs step) against the
pre-registered predictions in daily-series-prediction-2026-09-01.json. Reuses
aeo-aggregate.py's line parser and IP-range verification so the counting law
is identical to every other number this project has already reported.

Reads ONLY the frozen snapshot ~/logs/aeo/raw-hold/2026-W34-W35 (15 files,
verified-by-read per the cryptocardhub SKILL note) — never the live
/var/log/nginx/access.log*, per the standing instruction not to touch the
crontab-paused rollout by way of a fresh live read.
"""
from __future__ import annotations

import glob
import gzip
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path.home() / "scripts"))
import importlib.util
spec = importlib.util.spec_from_file_location("aeo_aggregate", Path.home() / "scripts" / "aeo-aggregate.py")
aeo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aeo)  # type: ignore

SNAPSHOT_DIR = Path.home() / "logs" / "aeo" / "raw-hold" / "2026-W34-W35"
SINCE = date(2026, 8, 17)
UNTIL = date(2026, 8, 31)

# The ~21-page cohort untouched by the 2026-08-13 dateModified salvo (mtime scan
# of seo-site/src/content/pseo/*.md, 2026-09-01). Listed literally, not
# recomputed live, because the source .md files are gitignored and this is a
# frozen analysis against a frozen log snapshot.
UNTOUCHED_COHORT_PATHS = {
    "/news/news-fca-crypto-pre-application-meetings-2026/",
    "/learn/what-is-liquid-staking/",
    "/geo/best-crypto-card-philippines/",
    "/compare/metamask-card-vs-coinbase/",
    "/tools/crypto-card-vs-traditional-card-calculator/",
    "/compare/crypto-com-vs-nexo-card/",
    "/reviews/wirex-card-review/",
    "/help/crypto-card-stopped-working-in-country/",
    "/news/news-fca-shuts-down-electronic-money-firm/",
    "/news/news-esma-crypto-custody-operational-resilience/",
    "/geo/best-crypto-card-turkey/",
    "/geo/best-crypto-card-washington/",
    "/faq/what-if-my-country-is-not-supported/",
    "/faq/what-if-my-kyc-application-is-rejected/",
    "/faq/is-uploading-my-government-id-to-ether-fi-safe/",
    "/faq/can-i-use-an-expired-government-id-for-kyc/",
    "/faq/is-ether-fi-available-in-my-country/",
    "/faq/can-i-use-a-digital-id-instead-of-a-physical-government-id/",
    "/hub/crypto-card-cashback-top-picks/",
    "/faq/who-actually-issues-the-ether-fi-cash-card-is-it-ether-fi/",
    "/faq/can-i-use-ether-fi-cash-if-i-live-outside-the-supported-coun/",
}
assert len(UNTOUCHED_COHORT_PATHS) == 21


def read_snapshot_lines():
    for path in sorted(glob.glob(str(SNAPSHOT_DIR / "access.log*"))):
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as f:
            yield from f


def build_series(lines, *, agent_filter, path_filter, ranges):
    """-> ({date_iso: count}, set(dates_with_any_line_at_all_in_range))"""
    per_day = defaultdict(int)
    for line in lines:
        rec = aeo.parse_line(line, is_content_path=path_filter)
        if rec is None:
            continue
        if agent_filter is not None and rec["agent"] != agent_filter:
            continue
        d = date.fromisoformat(rec["date"])
        if not (SINCE <= d <= UNTIL):
            continue
        if not aeo.verify_ip(rec["ip"], rec["agent"], ranges):
            continue
        per_day[rec["date"]] += 1
    return per_day


def non_root(path: str) -> bool:
    return path != "/" and aeo.is_article_path(path)


def cohort_path(path: str) -> bool:
    return path in UNTOUCHED_COHORT_PATHS


def dense_series(per_day: dict) -> list[dict]:
    out = []
    d = SINCE
    while d <= UNTIL:
        iso = d.isoformat()
        out.append({"date": iso, "n": per_day.get(iso, 0)})
        d += timedelta(days=1)
    return out


def classify_shape(series: list[dict]) -> dict:
    """Cheap descriptive stats to support (not replace) visual/human classification.
    No verdict here — step 3 of the brief forbids ruling on the branch.
    """
    vals = [row["n"] for row in series]
    n = len(vals)
    half = n // 2
    first_half = vals[:half]
    second_half = vals[half:]
    peak_idx = max(range(n), key=lambda i: vals[i])
    return {
        "n_days": n,
        "values": vals,
        "peak_date": series[peak_idx]["date"],
        "peak_value": vals[peak_idx],
        "peak_is_in_first_3_days": peak_idx < 3,
        "first_half_mean": round(sum(first_half) / len(first_half), 2) if first_half else None,
        "second_half_mean": round(sum(second_half) / len(second_half), 2) if second_half else None,
        "max_single_day_drop": max((vals[i] - vals[i + 1] for i in range(n - 1)), default=0),
        "max_single_day_drop_date": series[max(range(n - 1), key=lambda i: vals[i] - vals[i + 1])]["date"] if n > 1 else None,
    }


def main():
    lines = list(read_snapshot_lines())  # materialize once, reused 3x
    ranges, ranges_meta = aeo.load_all_bot_ranges()

    actual_dates_seen = set()
    for line in lines:
        m = aeo._LINE_RE.match(line)
        if m:
            try:
                d = datetime.strptime(m.group(2).split()[0], "%d/%b/%Y:%H:%M:%S").date()
                if SINCE <= d <= UNTIL:
                    actual_dates_seen.add(d.isoformat())
            except ValueError:
                pass
    days_covered = sorted(actual_dates_seen)
    missing_days = sorted(
        (SINCE + timedelta(days=i)).isoformat()
        for i in range((UNTIL - SINCE).days + 1)
        if (SINCE + timedelta(days=i)).isoformat() not in actual_dates_seen
    )

    result = {
        "requested_window": {"since": SINCE.isoformat(), "until": UNTIL.isoformat()},
        "snapshot_dir": str(SNAPSHOT_DIR),
        "actual_days_with_any_log_line_in_window": days_covered,
        "missing_days_in_snapshot": missing_days,
        "series": {},
    }

    for name, agent, pfilter, min_n in [
        ("chatgpt_user_nonroot", "ChatGPT-User", non_root, 30),
        ("claude_user_nonroot", "Claude-User", non_root, 30),
        ("untouched_cohort_21pages_all_agents", None, cohort_path, 30),
    ]:
        per_day = build_series(lines, agent_filter=agent, path_filter=pfilter, ranges=ranges)
        series = dense_series(per_day)
        total_n = sum(row["n"] for row in series)
        world = {
            "days_requested": f"{SINCE.isoformat()}..{UNTIL.isoformat()}",
            "days_actually_covered_by_snapshot": days_covered,
            "missing_days": missing_days,
            "path_filter": "non-root article-shaped paths (bucket in ARTICLE_BUCKETS, excludes '/')" if pfilter is non_root else "the 21-page cohort with .md mtime != 2026-08-13 (untouched by the 08-13 dateModified salvo)",
            "vendor": agent or "all three answer-time agents (ChatGPT-User + Claude-User + Perplexity-User)",
            "verification": "VERIFIED only (claimed AND source IP in vendor's own published range) per aeo-aggregate.py",
            "N": total_n,
        }
        entry = {"world": world}
        if total_n < min_n:
            entry["status"] = "underpowered"
            entry["note"] = f"N={total_n} < {min_n} — no shape conclusion drawn, series still recorded for the record"
            entry["daily"] = series
        else:
            entry["status"] = "ok"
            entry["daily"] = series
            entry["shape_stats"] = classify_shape(series)
        result["series"][name] = entry

    out_path = Path.home() / "logs" / "aeo" / "daily-series-2026-09-01.json"
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path}")
    for name, entry in result["series"].items():
        print(f"{name}: N={entry['world']['N']} status={entry['status']}")


if __name__ == "__main__":
    main()
