#!/usr/bin/env python3
"""w36-measure.py — the ONE self-executing W36 measurement (Fable's open remainder).

WHY THIS FILE EXISTS
---------------------
The 2026-09-01 investigation closed with "shape: decay-like; LEVEL: not
distinguished" because every prior baseline (2026-W31..33) lost its raw logs
to rotation before it could be verified. Only a PROSPECTIVE measurement on
the first fully-clean week can distinguish level — this script IS that
measurement, not a reminder to take it. It is meant to run once, unattended,
from cron, on 2026-09-09.

THE WINDOW — 7 FULL days, 2026-09-02..2026-09-08. NOT 09-01.
--------------------------------------------------------------
The nginx `host=` field (the thing that finally lets a request be attributed
to defycard.com instead of guessed from path shape — see
collision-audit-2026-09-01.json, where '/' proved indistinguishable across
three sites sharing one log file) went live 2026-09-01 09:15 UTC. 09-01 is
therefore a TRUNCATED day for this instrument. A window that starts on a
partial day already produced a false reading once in this project's history
(a 24h drop misread as a week-over-week rate) — see
[[law-a-guessed-constant-is-a-cause]]-adjacent lesson in the SKILL. Second
time is not free: the window starts 09-02.

THREE PRE-REGISTERED BRANCHES (Fable, frozen here BEFORE the number exists)
-----------------------------------------------------------------------------
  1. plateau_holds_10_45      -> decay is closed for good, the plateau IS the
                                  new norm.
  2. descent_continues_to_zero -> the plateau was a tail of the decay, reopen
                                  that branch.
  3. step_to_new_flat_level    -> a sharp break to a DIFFERENT flat level with
                                  no dated event behind it -> a live, ongoing
                                  addressable shift, reinstate the step
                                  hypothesis.
`classify()` only NAMES the best-fitting candidate. It never renders a
verdict — that stays with the operator + Fable (brief step 3).

INSTRUMENT-CLEANLINESS SELF-CHECK (brief step 3)
--------------------------------------------------
A moratorium on corpus-wide `dateModified` edits is standing until this
measurement is taken, precisely so this script's own result cannot be the
thing it is trying to measure. `contamination_check()` counts corpus files
touched inside the window versus an equal-length control window right
before it, and marks the result `contaminated` if the window looks like a
batch edit rather than ordinary daily cadence.

FAILURE MUST NOT BE SILENT (brief step 5)
--------------------------------------------
Any exception is caught, logged with its stage + traceback to
logs/aeo/w36-measure-failures.log, and reported to Telegram as a DISTINCT
"NOT measured" line — never the same silence a healthy quiet night produces.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import statistics
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, relpath: str):
    """Load a hyphenated sibling script as a module, by path (same trick
    daily-series.py and scripts/tests/test_promote_candidates.py already use
    in this repo — hyphenated filenames aren't importable as `import x`)."""
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


aeo = _load("aeo_aggregate_w36", "scripts/aeo-aggregate.py")
tg = _load("telegram_lib_w36", "scripts/telegram_lib.py")

# --- the pre-registered window (see module docstring) -----------------------
DEFAULT_SINCE = date(2026, 9, 2)
DEFAULT_UNTIL = date(2026, 9, 8)  # inclusive -> 7 full days

# --- Fable's plateau band, frozen before this script ever saw a number ------
PLATEAU_LOW, PLATEAU_HIGH = 10, 45

DEFY_HOST = "defycard.com"
CORPUS_DIR = ROOT / "seo-site" / "src" / "content" / "pseo"

# host= is appended AFTER the existing 6 quoted/bracketed fields, anchored to
# end-of-line ($) on purpose: a legacy line with no host= field (anything
# before 2026-09-01 09:15 UTC) must NOT match and must NOT be silently
# attributed to any site — see module docstring on why 09-01 is excluded.
_LINE_HOST_RE = re.compile(
    r'^(\S+) \S+ \S+ \[([^\]]+)\] "(?:GET|HEAD) ([^ "]+)[^"]*" (\d{3}) \S+ '
    r'"[^"]*" "([^"]*)" host=(\S+)$'
)


def normalize_host(host: str) -> str:
    h = host.strip().lower()
    return h[4:] if h.startswith("www.") else h


def non_root(path: str) -> bool:
    """Article-shaped AND not the collision-prone '/' — see collision-audit."""
    return path != "/" and aeo.is_article_path(path)


def parse_line_host(line: str, is_content_path) -> dict[str, Any] | None:
    """Like aeo.parse_line, but names the SITE from the new host= field
    instead of guessing it from path shape (the exact collision the
    2026-09-01 audit found: every site's '/' looks identical in this shared
    log)."""
    m = _LINE_HOST_RE.match(line)
    if not m:
        return None
    ip, ts_raw, path, status, ua, host = m.groups()
    agent = aeo.agent_of(ua)
    if agent is None or status != "200":
        return None
    path = path.split("?", 1)[0]
    if not is_content_path(path):
        return None
    try:
        ts = datetime.strptime(ts_raw.split()[0], "%d/%b/%Y:%H:%M:%S").date()
    except ValueError:
        return None
    return {"date": ts.isoformat(), "path": path, "agent": agent, "ip": ip,
            "host": normalize_host(host)}


def dense_series(per_day: dict[str, int], since: date, until: date) -> list[dict]:
    out = []
    d = since
    while d <= until:
        out.append({"date": d.isoformat(), "n": per_day.get(d.isoformat(), 0)})
        d += timedelta(days=1)
    return out


def build_daily_series(lines, *, agent: str, since: date, until: date,
                       ranges: dict) -> tuple[list[dict], set[str]]:
    """-> (dense per-day VERIFIED count list, hosts actually seen in-window)."""
    per_day: dict[str, int] = {}
    hosts_seen: set[str] = set()
    for line in lines:
        rec = parse_line_host(line, non_root)
        if rec is None:
            continue
        d = date.fromisoformat(rec["date"])
        if not (since <= d <= until):
            continue
        hosts_seen.add(rec["host"])
        if rec["host"] != DEFY_HOST or rec["agent"] != agent:
            continue
        if not aeo.verify_ip(rec["ip"], agent, ranges):
            continue
        per_day[rec["date"]] = per_day.get(rec["date"], 0) + 1
    return dense_series(per_day, since, until), hosts_seen


def classify(values: list[int]) -> dict[str, Any]:
    """Name the best-fitting of Fable's 3 pre-registered branches. Never a
    verdict (brief step 3) — see module docstring."""
    n = len(values)
    in_band = all(PLATEAU_LOW <= v <= PLATEAU_HIGH for v in values)
    half = max(1, n // 2)
    first_mean = statistics.mean(values[:half])
    second_mean = statistics.mean(values[half:]) if values[half:] else first_mean

    # Step candidate: the split point (anywhere but the very ends) whose two
    # sides differ most, checked for being internally FLAT on both sides —
    # that is what tells a step apart from ordinary day-to-day noise.
    best_gap, best_i = 0.0, None
    for i in range(2, n - 1):
        a, b = values[:i], values[i:]
        gap = abs(statistics.mean(a) - statistics.mean(b))
        if gap > best_gap:
            best_gap, best_i = gap, i
    step_candidate = False
    if best_i is not None:
        a, b = values[:best_i], values[best_i:]
        step_candidate = (best_gap >= 15 and max(a) - min(a) <= 10
                          and max(b) - min(b) <= 10 and not in_band)

    descent_candidate = (not in_band and values[-1] <= 8
                         and second_mean < first_mean * 0.6)

    # step is checked before descent: a step candidate (flat, then flat at a
    # DIFFERENT level) is a more specific match than the loose "ends low"
    # descent test, which would otherwise also fire on a step's tail.
    if in_band:
        branch = "plateau_holds_10_45"
    elif step_candidate:
        branch = "step_to_new_flat_level"
    elif descent_candidate:
        branch = "descent_continues_to_zero"
    else:
        branch = "none_of_the_three_cleanly"

    return {
        "candidate_branch": branch,
        "in_band_10_45": in_band,
        "first_half_mean": round(first_mean, 2),
        "second_half_mean": round(second_mean, 2),
        "best_step_index": best_i,
        "best_step_gap": round(best_gap, 2),
        "note": "candidate label only — the verdict belongs to the operator + "
                "Fable (brief step 3), this script never renders one",
    }


def contamination_check(since: date, until: date,
                        corpus_dir: Path = CORPUS_DIR) -> dict[str, Any]:
    """Instrument-cleanliness self-check (brief step 3). ponytail: mtime-only
    heuristic — there is no birth-time / edit-event log yet to tell a NEW page
    from a re-touched one, so this flags a spike far outside the site's own
    recent cadence, not literally every edit. Upgrade path: a per-file
    publish/edit event log once one exists, instead of mtime.
    """
    if not corpus_dir.exists():
        return {"status": "unmeasurable", "reason": f"{corpus_dir} not found"}
    span = (until - since).days + 1
    control_since = since - timedelta(days=span)
    control_until = since - timedelta(days=1)
    files = list(corpus_dir.glob("*.md"))

    def _count(lo: date, hi: date) -> int:
        c = 0
        for p in files:
            try:
                mt = date.fromtimestamp(p.stat().st_mtime)
            except OSError:
                continue
            if lo <= mt <= hi:
                c += 1
        return c

    window_n = _count(since, until)
    control_n = _count(control_since, control_until)
    corpus_total = len(files)
    # self-calibrated against the site's OWN recent activity, not a guessed
    # constant — see [[law-a-guessed-constant-is-a-cause]].
    threshold = max(3 * control_n, round(0.10 * corpus_total))
    contaminated = window_n > threshold and window_n > 50
    return {
        "status": "contaminated" if contaminated else "clean",
        "window_files_touched": window_n,
        "control_window": [control_since.isoformat(), control_until.isoformat()],
        "control_files_touched": control_n,
        "corpus_total": corpus_total,
        "threshold_used": threshold,
    }


def pull_ga4_sessions(since: date, until: date) -> dict[str, Any]:
    """AI-source human sessions for the same window, GA4-side. Isolated in
    its own try/except (brief step 5's "never let one leg's failure erase the
    others" — the log-side series must still ship even if GA4 is down)."""
    try:
        wr = _load("weekly_report_w36", "scripts/weekly-report.py")
        client = wr._ga4_client()
        rows = [{"source": d[0], "sessions": int(m[0]), "engaged": int(m[1])}
                for d, m in wr._run_report(client, ["sessionSource"],
                                           ["sessions", "engagedSessions"],
                                           since.isoformat(), until.isoformat(),
                                           wr._ai_source_filter())]
        return {"status": "ok", "by_source": rows,
                "human_sessions": wr.human_sessions(rows)}
    except Exception as e:  # noqa: BLE001 — GA4 outage must not kill the run
        return {"status": "failed", "error": f"{type(e).__name__}: {e}"}


def compose_message(result: dict[str, Any]) -> str:
    w = result["window"]
    cg = result["chatgpt_user"]
    cl = result["claude_user_vendor_control"]
    ga4 = result["ga4_human_sessions_ai_sources"]
    cont = result["instrument_contamination_check"]
    daily_str = ", ".join(f"{r['date'][5:]}:{r['n']}" for r in cg["daily"])
    lines = [
        f"W36 measurement, window {w['since']}..{w['until']} ({w['days']} full days).",
        "World: VERIFIED (UA claim AND source IP in vendor's own published "
        "range) ChatGPT-User fetches of non-root defycard.com article pages, "
        "site named by the nginx host= field (live since 2026-09-01 09:15 "
        "UTC), not guessed from path shape. Vendor control: Claude-User, "
        "same filter, same host.",
        f"ChatGPT-User daily (mm-dd:n): {daily_str}",
        f"ChatGPT-User: N={cg['N']}  median={cg['median']}",
        f"Claude-User (control): N={cl['N']}  median={cl['median']}",
        f"Candidate branch: {cg['classification']['candidate_branch']} "
        f"(plateau band {PLATEAU_LOW}-{PLATEAU_HIGH}/day) — NOT a verdict, "
        "for operator + Fable to confirm.",
    ]
    if ga4.get("status") == "ok":
        lines.append(f"GA4 human sessions, AI sources, same window: {ga4['human_sessions']}")
    else:
        lines.append(f"GA4 human sessions: UNMEASURED ({ga4.get('error', ga4.get('status'))})")
    if cont["status"] == "contaminated":
        lines.append(
            f"⛔ INSTRUMENT CONTAMINATED: {cont['window_files_touched']} corpus "
            f"files touched in-window vs {cont['control_files_touched']} in the "
            f"control window {cont['control_window']} (threshold "
            f"{cont['threshold_used']}) — read this result with that in mind.")
    else:
        lines.append(
            f"Instrument clean: {cont['window_files_touched']} corpus files "
            f"touched in-window (control {cont['control_files_touched']}, "
            f"threshold {cont['threshold_used']}).")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default=DEFAULT_SINCE.isoformat())
    ap.add_argument("--until", default=DEFAULT_UNTIL.isoformat())
    ap.add_argument("--out-tag", default=None,
                    help="output filename suffix; defaults to --until")
    args = ap.parse_args(argv)
    since = date.fromisoformat(args.since)
    until = date.fromisoformat(args.until)
    out_tag = args.out_tag or until.isoformat()

    aeo_dir = ROOT / "logs" / "aeo"
    out_path = aeo_dir / f"w36-measure-{out_tag}.json"
    fail_log = aeo_dir / "w36-measure-failures.log"

    stage = "start"
    try:
        stage = "load_bot_ranges"
        ranges, _ranges_meta = aeo.load_all_bot_ranges()

        stage = "read_logs"
        lines = list(aeo._read_logs())
        if not lines:
            raise RuntimeError("zero log lines read from nginx — sudo fallback "
                               "or NGINX_GLOB path may be broken")

        stage = "build_series_chatgpt"
        chatgpt_series, hosts_a = build_daily_series(
            lines, agent="ChatGPT-User", since=since, until=until, ranges=ranges)
        stage = "build_series_claude"
        claude_series, hosts_b = build_daily_series(
            lines, agent="Claude-User", since=since, until=until, ranges=ranges)

        chatgpt_values = [r["n"] for r in chatgpt_series]
        claude_values = [r["n"] for r in claude_series]

        stage = "contamination_check"
        contamination = contamination_check(since, until)

        stage = "ga4"
        ga4 = pull_ga4_sessions(since, until)

        result: dict[str, Any] = {
            "measured_at_utc": datetime.utcnow().isoformat() + "Z",
            "window": {"since": since.isoformat(), "until": until.isoformat(),
                      "days": (until - since).days + 1},
            "world": {
                "filter": "VERIFIED (UA claim AND source IP in vendor's own "
                          "published range) ChatGPT-User/Claude-User fetches "
                          "of non-root defycard.com article pages",
                "site_distinguished_by": "nginx access_log host= field (live "
                                         "since 2026-09-01 09:15 UTC), NOT "
                                         "path shape",
                "vendor_primary": "ChatGPT-User",
                "vendor_control": "Claude-User (internal vendor control)",
                "hosts_seen_in_window": sorted(hosts_a | hosts_b),
            },
            "chatgpt_user": {
                "daily": chatgpt_series,
                "N": sum(chatgpt_values),
                "median": statistics.median(chatgpt_values),
                "classification": classify(chatgpt_values),
            },
            "claude_user_vendor_control": {
                "daily": claude_series,
                "N": sum(claude_values),
                "median": statistics.median(claude_values),
            },
            "ga4_human_sessions_ai_sources": ga4,
            "instrument_contamination_check": contamination,
            "status": "measured",
        }
        aeo_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

        msg = compose_message(result)
        result["telegram_sent"] = tg.alert(msg, prefix="[cryptocardhub][W36]")
        out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(msg)
        print(f"wrote {out_path}")
        return 0
    except Exception as e:  # noqa: BLE001 — this run must never fail silently
        tb = traceback.format_exc()
        aeo_dir.mkdir(parents=True, exist_ok=True)
        with fail_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "failed_at_utc": datetime.utcnow().isoformat() + "Z",
                "requested_window": [since.isoformat(), until.isoformat()],
                "stage": stage,
                "error": f"{type(e).__name__}: {e}",
                "traceback": tb,
            }) + "\n")
        fail_msg = (f"замер W36 НЕ выполнен, причина: [{stage}] "
                   f"{type(e).__name__}: {e}. Trace: {fail_log}")
        tg.alert(fail_msg, prefix="[cryptocardhub][W36][FAILED]")
        print(fail_msg, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
