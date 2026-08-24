#!/usr/bin/env python3
"""faq-verdict.py — Y75: judge a FAQ-children cohort by the FAMILY, not the child.

WHY THE FAMILY
--------------
A child page answers a question its parent already answers in a FAQ box. If a
retriever starts citing the child, the parent's own fetches may fall — and a
child measured alone would call that a success while the family stood still or
shrank. Fable's rule: compare `fetches(parent) + fetches(children)` against the
parent's own baseline from before the cohort opened.

  family above baseline  -> additive. The tap opens for the next cohort.
  child grows, parent falls further -> cannibalisation. The tap closes.

TWO THRESHOLDS, BOTH LITERAL (mirrored in scripts/tests/test_faq_verdict.py):
  * at least 30 % of the cohort's children got >= 1 agent fetch;
  * the sum across families is >= the parents' baseline.

WHAT THIS REFUSES TO DO
-----------------------
* judge before the window has elapsed — a verdict at +3 days is a coin toss
  wearing a number;
* judge without a baseline — if no complete week of per-page data precedes the
  cohort, the answer is UNMEASURABLE, not "failed". Only one such week exists
  today (2026-W32); `by_page` was added to the aggregate in Y63, so anything
  earlier has no per-page figures at all and the script says so instead of
  quietly comparing against zero.

Usage:
    faq-verdict.py                 # judge whatever is due
    faq-verdict.py --cohort 1
    faq-verdict.py --force         # judge early, and SAY that it is early
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "logs" / "pseo" / "faq-cohorts.json"
HISTORY = ROOT / "logs" / "aeo" / "history.jsonl"
LOG = ROOT / "logs" / "pseo"

# --- the law, as literals --------------------------------------------------
MIN_CHILDREN_FETCHED = 0.30      # share of the cohort with >= 1 agent fetch
WINDOW_14, WINDOW_28 = 14, 28


def weeks() -> list[dict]:
    if not HISTORY.is_file():
        return []
    out = []
    for line in HISTORY.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return sorted(out, key=lambda r: r.get("since", ""))


def daily_rate(rows: list[dict], paths: set[str]) -> tuple[float, int]:
    """-> (fetches per day across `paths`, days covered).

    Per DAY, not per week: the windows are 14 and 28 days and the aggregates
    are weekly, so anything else would compare unlike spans.
    """
    total = days = 0
    for r in rows:
        by_page = r.get("by_page")
        if not by_page:
            continue          # a week from before by_page existed carries no signal
        total += sum(v for k, v in by_page.items() if k in paths)
        days += int(r.get("days_covered") or 7)
    return (total / days if days else 0.0), days


def split_weeks(opened: date) -> tuple[list[dict], list[dict]]:
    """Weeks entirely BEFORE the cohort opened, and weeks entirely after."""
    before, after = [], []
    for r in weeks():
        try:
            since = date.fromisoformat(r["since"])
            until = date.fromisoformat(r["until"])
        except (KeyError, ValueError):
            continue
        if until < opened:
            before.append(r)
        elif since >= opened:
            after.append(r)
    return before, after


def judge(cohort: dict, today: date, force: bool) -> dict:
    opened = date.fromisoformat(cohort["opened"])
    age = (today - opened).days
    window = WINDOW_28 if age >= WINDOW_28 else WINDOW_14
    if age < WINDOW_14 and not force:
        return {"status": "too_early", "age_days": age,
                "summary": f"{age}d since the cohort opened; the first reading "
                           f"is at +{WINDOW_14}"}

    parents = {i["parent"] for i in cohort["items"]}
    children = {f"/faq/{i['slug']}/" for i in cohort["items"]}

    before, after = split_weeks(opened)
    base_rate, base_days = daily_rate(before, parents)
    if base_days == 0:
        return {"status": "unmeasurable", "age_days": age,
                "summary": "no complete week with per-page figures precedes the "
                           "cohort — a baseline cannot be built, and comparing "
                           "against zero would manufacture a verdict"}
    now_parent, after_days = daily_rate(after, parents)
    now_child, _ = daily_rate(after, children)
    if after_days == 0:
        return {"status": "no_data_yet", "age_days": age,
                "summary": "no complete week has been aggregated since the "
                           "cohort opened"}

    fetched = 0
    for r in after:
        bp = r.get("by_page") or {}
        fetched = max(fetched, sum(1 for c in children if bp.get(c, 0) > 0))
    share = fetched / max(1, len(children))

    family = now_parent + now_child
    additive = family >= base_rate
    reach = share >= MIN_CHILDREN_FETCHED
    passed = additive and reach

    return {
        "status": "pass" if passed else "fail",
        "age_days": age,
        "window": window,
        "early": bool(force and age < WINDOW_14),
        "baseline_parent_per_day": round(base_rate, 2),
        "baseline_days": base_days,
        "now_parent_per_day": round(now_parent, 2),
        "now_children_per_day": round(now_child, 2),
        "family_per_day": round(family, 2),
        "additive": additive,
        "children_fetched": fetched,
        "children_total": len(children),
        "children_share": round(share, 3),
        "reach_met": reach,
        "summary": (
            f"family {family:.2f}/day vs parent baseline {base_rate:.2f}/day "
            f"({'additive' if additive else 'BELOW baseline'}); "
            f"{fetched}/{len(children)} children fetched "
            f"({share:.0%}, need {MIN_CHILDREN_FETCHED:.0%})"
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", type=int, default=None, help="1-based index")
    ap.add_argument("--force", action="store_true",
                    help="judge before the window closes, and label it early")
    ap.add_argument("--write", action="store_true",
                    help="record the verdict in the ledger")
    a = ap.parse_args()

    if not LEDGER.is_file():
        print("[verdict] no cohort ledger", file=sys.stderr)
        return 1
    led = json.loads(LEDGER.read_text(encoding="utf-8"))
    today = date.today()
    changed = False

    for n, cohort in enumerate(led.get("cohorts", []), 1):
        if a.cohort and n != a.cohort:
            continue
        opened = date.fromisoformat(cohort["opened"])
        cohort.setdefault("verdict_due_14", (opened + timedelta(days=WINDOW_14)).isoformat())
        cohort.setdefault("verdict_due_28", (opened + timedelta(days=WINDOW_28)).isoformat())
        if cohort.get("verdict_due_14") is None:
            cohort["verdict_due_14"] = (opened + timedelta(days=WINDOW_14)).isoformat()
            cohort["verdict_due_28"] = (opened + timedelta(days=WINDOW_28)).isoformat()
            changed = True

        v = judge(cohort, today, a.force)
        v["cohort"] = n
        v["judged"] = today.isoformat()
        print(json.dumps({"event": "faq_verdict", **v}, ensure_ascii=False))
        print(f"[verdict] cohort {n} (opened {cohort['opened']}, "
              f"due {cohort['verdict_due_14']}): {v['status']} — {v['summary']}")

        if a.write and v["status"] in ("pass", "fail"):
            key = f"verdict_{v['window']}"
            cohort[key] = v
            changed = True

    if a.write and changed:
        LEDGER.write_text(json.dumps(led, indent=1, ensure_ascii=False) + "\n",
                          encoding="utf-8")
        LOG.mkdir(parents=True, exist_ok=True)
        print(f"[verdict] ledger updated")
    return 0


def tap_is_open() -> tuple[bool, str]:
    """May a NEW cohort be opened? Imported by faq-children.py.

    Fable's rule: cohort N+1 does not open until cohort N has been read at +14.
    The tap is not "how many pages may we print" but "how many hypotheses can
    we judge".
    """
    if not LEDGER.is_file():
        return True, "no cohort yet"
    led = json.loads(LEDGER.read_text(encoding="utf-8"))
    cohorts = led.get("cohorts") or []
    if not cohorts:
        return True, "no cohort yet"
    last = cohorts[-1]
    if last.get("verdict_14") or last.get("verdict_28"):
        return True, f"cohort {len(cohorts)} has been read"
    opened = last.get("opened", "?")
    due = last.get("verdict_due_14") or "?"
    return False, (f"cohort {len(cohorts)} (opened {opened}) has no +14 reading "
                   f"yet — due {due}. Judge it before opening another.")


if __name__ == "__main__":
    raise SystemExit(main())
