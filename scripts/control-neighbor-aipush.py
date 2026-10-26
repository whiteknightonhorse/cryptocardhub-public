#!/usr/bin/env python3
"""control-neighbor-aipush.py — external control for the AI-traffic decline.

Assigned by the previous ruling and not executed until now (2026-09-01). It
is needed MORE than before the instrument fix, not less: comparing a
neighbor's numbers against defycard's OLD claimed figures would have
compared dirty data to dirty data.

THE CONTROL'S WORLD, NAMED EXPLICITLY (law-a-control-must-state-its-world —
a control that doesn't say what it is proves nothing about what it looks
like it proves):

  filter   = aeo-aggregate.py's VERIFIED law, unchanged: UA substring-matches
             one of ChatGPT-User / Claude-User / Perplexity-User, AND
             status == 200, AND the source IP is inside that vendor's own
             published range (same three JSON files, same on-disk cache as
             defycard's own aggregate; see scripts/aeo-aggregate.py).
  machine  = the SAME box, and — discovered while building this control,
             2026-09-01 — the SAME nginx access.log: `access_log` is set
             once, globally, in /etc/nginx/nginx.conf, and neither
             defycard.com's nor aipush.app's server block overrides it.
             apibase.pro's `/mcp` POSTs and defycard's `/reviews/...` GETs
             land in one undistinguished-by-Host file. That is exactly why
             this control can exist without shipping code to a second
             machine: only the PATH allowlist changes between sites.
  site     = aipush.app, the neighbor. Its path allowlist is a literal set
             of its 23 known real pages (root + 22 flat marketing slugs),
             read from its own live https://aipush.app/sitemap.xml on
             2026-09-01 — NOT a generic single-segment-slug regex. Checked
             on this exact log the same day: /app, /backend, /root, /home
             are all real scanner-probe paths that a bare slug regex would
             have admitted as if they were aipush content.
  window   = 2026-W34 (2026-08-17..23) and 2026-W35 (2026-08-24..30) — the
             only two weeks defycard's own re-verification could recover
             from present raw logs. W34 is PARTIAL (6 of 7 days survive
             rotation as of today; 08-17 is already gone) and is reported as
             such, not silently treated as a full week.

WHAT THIS SCRIPT DOES NOT DO
-----------------------------
It prints the two series side by side and stops. It does NOT conclude
"external shift" or "our defect" — that verdict is reserved by the standing
ruling (Fable adjudicates, this file only measures).

Usage:
    control-neighbor-aipush.py           # print the comparison
    control-neighbor-aipush.py --json    # machine-readable
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


aeo = _load("aeo_aggregate", "scripts/aeo-aggregate.py")

# --- aipush.app's own known content paths, fetched 2026-09-01 -------------
AIPUSH_PATHS = {
    "/", "/analyzer", "/analysis", "/privacy", "/agent-api",
    "/how-to-rank-in-chatgpt", "/how-to-get-mentioned-in-chatgpt", "/llm-seo",
    "/optimize-website-for-ai", "/ai-search-optimization", "/chatgpt-indexing",
    "/how-to-appear-in-ai-answers", "/ai-seo-consultant", "/ai-seo-company",
    "/ai-seo-agency", "/ai-visibility-tracking", "/generative-engine-optimization",
    "/ai-seo-services", "/ai-citation-optimization-guide",
    "/answer-engine-optimization-guide", "/ai-seo-vs-traditional-seo-guide",
    "/competitor-analysis-report", "/ai-competitor-analysis-tool",
    "/competitive-intelligence-report",
}
AIPUSH_I18N_PREFIXES = ("/es", "/zh")

WEEKS = ("2026-W34", "2026-W35")


def is_aipush_content_path(path: str) -> bool:
    if path in AIPUSH_PATHS:
        return True
    for pre in AIPUSH_I18N_PREFIXES:
        if path.startswith(pre + "/") and path[len(pre):] in AIPUSH_PATHS:
            return True
    return False


def _week_count(lines: list[str], since: date, until: date, ranges: dict,
                is_content_path) -> dict[str, Any]:
    by_agent: dict[str, int] = {a: 0 for a in aeo.AI_ANSWER_AGENTS}
    days: set[str] = set()
    verified = 0
    for line in lines:
        rec = aeo.parse_line(line, is_content_path=is_content_path)
        if rec is None:
            continue
        d = date.fromisoformat(rec["date"])
        if not (since <= d <= until):
            continue
        days.add(rec["date"])
        if aeo.verify_ip(rec["ip"], rec["agent"], ranges):
            verified += 1
            by_agent[rec["agent"]] += 1
    return {"verified_chatgpt_user_200": verified, "by_agent": by_agent,
           "days_covered": len(days), "days_in_window": (until - since).days + 1}


def compare() -> dict[str, Any]:
    ranges, ranges_meta = aeo.load_all_bot_ranges()
    lines = list(aeo._read_logs())
    out: dict[str, Any] = {
        "world": {
            "filter": "UA in {ChatGPT-User,Claude-User,Perplexity-User} AND "
                     "status==200 AND source IP verified against the vendor's "
                     "own published range (same as aeo-aggregate.py verified_*)",
            "machine": "shared /var/log/nginx/access.log*, one file for every "
                      "site on this box (confirmed 2026-09-01)",
            "sites": ["defycard.com", "aipush.app"],
            "weeks": list(WEEKS),
        },
        "ranges_meta": ranges_meta,
        "weeks": {},
    }
    for week in WEEKS:
        since, until = aeo.iso_week_bounds(week)
        defycard = _week_count(lines, since, until, ranges, aeo.is_article_path)
        aipush = _week_count(lines, since, until, ranges, is_aipush_content_path)
        out["weeks"][week] = {"since": since.isoformat(), "until": until.isoformat(),
                              "defycard.com": defycard, "aipush.app": aipush}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    result = compare()
    if a.json:
        print(json.dumps(result, indent=2))
        return 0
    print("CONTROL: defycard.com vs aipush.app, same filter, same machine, same window")
    print(json.dumps(result["world"], indent=2))
    for week, row in result["weeks"].items():
        dc, ap_ = row["defycard.com"], row["aipush.app"]
        print(f"\n{week} ({row['since']}..{row['until']}):")
        print(f"  defycard.com  verified_chatgpt_user_200={dc['verified_chatgpt_user_200']:>4} "
             f"({dc['days_covered']}/{dc['days_in_window']} days recovered)")
        print(f"  aipush.app    verified_chatgpt_user_200={ap_['verified_chatgpt_user_200']:>4} "
             f"({ap_['days_covered']}/{ap_['days_in_window']} days recovered)")
    print("\nNo verdict is issued here (\"our defect\" vs \"external shift\" is reserved).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
