#!/usr/bin/env python3
"""aeo-aggregate.py — AEO-2 instrument: how often assistants FETCH us.

WHY THIS FILE EXISTS
--------------------
GA4 counts assistant traffic only after a human clicks through — ~0.4 sessions
a day. The answer-time agents (ChatGPT-User, Claude-User, Perplexity-User)
fetch a page *while the assistant is composing its answer*, two orders of
magnitude more often. That is citability, measured before any click, and nginx
is the only place it exists.

nginx logs rotate after ~15 days. A trend read off raw logs is therefore an
artefact of the retention window, not of the site (this exact mistake produced
a fake "+4.6x July->August" in the R2 round). So this script does two things:

  1. aggregates one ISO week of logs into logs/aeo/<YYYY-Www>.json
  2. appends that week to logs/aeo/history.jsonl, which outlives rotation

Run it weekly (cron) BEFORE weekly-report.py. The report reads aggregates
only — it never parses logs itself, so a missing week reads as Missing rather
than as a quiet zero.

THE COUNTING LAW (mirrored as literals in scripts/tests/test_aeo_report.py):
  a request counts as an AI article fetch iff
     UA names an answer-time agent   AND
     status == 200                   AND
     path is `/` or `/<bucket>/<slug>/` with bucket in ARTICLE_BUCKETS

Both extra conditions are load-bearing. Top-404 traffic on this host arrives
under forged `OAI-SearchBot` / `PerplexityBot` user agents asking for /.env,
/.aws/credentials and /id_rsa — vulnerability scanners wearing an AI costume.
Dropping either filter re-admits them and inflates the only number this whole
programme is judged on.

CLAIMED VS VERIFIED (added after the 654->99 investigation, 2026-09-01)
------------------------------------------------------------------------
The three conditions above only prove the UA STRING says "ChatGPT-User" etc.
They do nothing to prove the request came from OpenAI/Anthropic/Perplexity's
own infrastructure — a vulnerability scanner can type any string into a
User-Agent header for free. A weekly total that mixed real answer-time agents
with scanners spoofing that same header (resolving to generic Google
Cloud / AWS ranges, not the vendor's own published ranges) is how a row read
654 one week — a single counter standing for two populations at once.

So every count this file produces now exists in TWO separate fields that are
NEVER added together into one number:
  * `ai_fetches`, `by_agent`, `by_bucket`, `by_page` (unprefixed)
      = CLAIMED: the UA string matched. Unchanged behaviour, unchanged name —
        this is exactly what the file always measured, now named honestly.
  * `verified_ai_fetches`, `verified_by_agent`, `verified_by_bucket`,
    `verified_by_page` = VERIFIED: claimed AND the source IP is inside that
        agent's own published range file (checked with the stdlib `ipaddress`
        module, cached locally so a network hiccup does not stall the cron):
          ChatGPT-User    https://openai.com/chatgpt-user.json
          Claude-User     https://claude.com/crawling/bots.json
          Perplexity-User https://www.perplexity.com/perplexity-user.json
A verdict, a target list or a report headline must read `verified_*`.
`ai_fetches` etc. stay for comparison/transparency only — printing them as
"the" number is the exact mistake this file existed to prevent once already.

Usage:
    aeo-aggregate.py                      # the last COMPLETE ISO week
    aeo-aggregate.py --week 2026-W32
    aeo-aggregate.py --since 2026-08-04 --until 2026-08-10
    aeo-aggregate.py --print              # don't write, just show
"""
from __future__ import annotations

import argparse
import gzip
import ipaddress
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
AEO_DIR = ROOT / "logs" / "aeo"
HISTORY = AEO_DIR / "history.jsonl"
NGINX_GLOB = "/var/log/nginx/access.log*"

# --- the law, part 1: who is an answer-time agent ---------------------------
# Substring match, case-insensitive. These three fetch a page at the moment a
# user's question is being answered. Crawlers that build an index (GPTBot,
# ClaudeBot, PerplexityBot, OAI-SearchBot) are deliberately NOT here: they say
# nothing about whether we were used in an answer.
AI_ANSWER_AGENTS = ("ChatGPT-User", "Claude-User", "Perplexity-User")

# --- the law, part 2: what an article URL looks like ------------------------
ARTICLE_BUCKETS = ("hub", "compare", "reviews", "how-to", "for", "tools",
                   "learn", "help", "geo", "news", "faq")

# --- the law, part 3: what makes a claim VERIFIED ----------------------------
# Each vendor publishes its own crawler/fetcher IP ranges as a small JSON file
# (same {"prefixes": [{"ipv4Prefix": "a.b.c.d/nn"}, ...]} shape for all three,
# confirmed by fetching them directly on 2026-09-01). An IP outside the range
# means the UA string is unconfirmed — still counted as CLAIMED, never VERIFIED.
BOT_RANGES_URL = {
    "ChatGPT-User": "https://openai.com/chatgpt-user.json",
    "Claude-User": "https://claude.com/crawling/bots.json",
    "Perplexity-User": "https://www.perplexity.com/perplexity-user.json",
}
RANGES_DIR = AEO_DIR / "bot-ranges"
RANGES_MAX_AGE_DAYS = 7  # vendors rotate ranges; re-fetch weekly, don't hammer them daily

_LINE_RE = re.compile(
    r'^(\S+) \S+ \S+ \[([^\]]+)\] "(?:GET|HEAD) ([^ "]+)[^"]*" (\d{3}) \S+ "[^"]*" "([^"]*)"'
)
_ARTICLE_RE = re.compile(
    r"^/(" + "|".join(re.escape(b) for b in ARTICLE_BUCKETS) + r")/[a-z0-9][a-z0-9-]*/$"
)


def is_article_path(path: str) -> bool:
    """`/` or `/<bucket>/<slug>/`. Query strings are stripped by the caller."""
    return path == "/" or bool(_ARTICLE_RE.match(path))


def agent_of(ua: str) -> str | None:
    low = ua.lower()
    for a in AI_ANSWER_AGENTS:
        if a.lower() in low:
            return a
    return None


def parse_line(line: str, is_content_path=is_article_path) -> dict[str, Any] | None:
    """-> {ts, path, status, ua, agent, ip} or None if not an AI article fetch.

    Returning None for *any* of the three conditions is the point: the gate
    proves each one separately. `ip` is the CLAIM's subject, not yet judged —
    verification happens later, once per unique IP, in `aggregate()`.

    `is_content_path` defaults to defycard's own `is_article_path` but is
    injectable: `/var/log/nginx/access.log` is confirmed (2026-09-01) to be
    ONE shared file across every site on this box, undistinguished by Host —
    apibase.pro's `/mcp` and defycard's `/reviews/...` land in the same log.
    The control script for a neighbor site reuses this exact UA+status law
    with that site's own known content paths instead.
    """
    m = _LINE_RE.match(line)
    if not m:
        return None
    ip, ts_raw, path, status, ua = m.groups()
    agent = agent_of(ua)
    if agent is None:
        return None
    if status != "200":
        return None
    path = path.split("?", 1)[0]
    if not is_content_path(path):
        return None
    try:
        ts = datetime.strptime(ts_raw.split()[0], "%d/%b/%Y:%H:%M:%S").date()
    except ValueError:
        return None
    return {"date": ts.isoformat(), "path": path, "status": 200,
            "ua": ua, "agent": agent, "ip": ip}


# ---------------------------------------------------------------------------
# Verification: is the claimed IP actually inside the vendor's own range?
# ---------------------------------------------------------------------------

def _http_get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "defycard-aeo-verify/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def _cache_path(agent: str) -> Path:
    return RANGES_DIR / f"{agent.lower().replace('-', '_')}.json"


def fetch_bot_ranges(agent: str, *, fetcher=_http_get_json,
                     now: float | None = None) -> tuple[list, dict[str, Any]]:
    """-> (list of ip_network, meta). Empty list + source='unavailable' fails CLOSED
    (an agent with no usable range list verifies nothing, it never verifies
    everything) so a fetch outage can only ever undercount `verified`, never
    silently inflate it back to `claimed`.

    Cached under logs/aeo/bot-ranges/<agent>.json so a weekly cron does not
    hit the vendor on every run, and a network hiccup falls back to the last
    good copy instead of zeroing verification out for the week.
    """
    import time as _time
    now = _time.time() if now is None else now
    url = BOT_RANGES_URL.get(agent)
    cache = _cache_path(agent)
    cached = None
    if cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = None
    stale = not cache.exists() or (now - cache.stat().st_mtime) > RANGES_MAX_AGE_DAYS * 86400
    data, source = cached, "cache"
    if url and stale:
        try:
            data = fetcher(url)
            RANGES_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data), encoding="utf-8")
            source = "live"
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            data, source = cached, ("cache_stale" if cached else "unavailable")
    elif not url:
        source = "no_published_range_for_this_agent"
    networks = []
    for p in (data or {}).get("prefixes", []):
        raw = p.get("ipv4Prefix") or p.get("ipv6Prefix")
        if not raw:
            continue
        try:
            networks.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            continue
    if not networks:
        source = "unavailable"
    return networks, {"source": source, "prefix_count": len(networks)}


def load_all_bot_ranges(*, fetcher=_http_get_json) -> tuple[dict[str, list], dict[str, Any]]:
    ranges: dict[str, list] = {}
    meta: dict[str, Any] = {}
    for agent in AI_ANSWER_AGENTS:
        ranges[agent], meta[agent] = fetch_bot_ranges(agent, fetcher=fetcher)
    return ranges, meta


def verify_ip(ip: str, agent: str, ranges: dict[str, list]) -> bool:
    """Is `ip` inside the range list published BY THE VENDOR NAMED `agent`?

    Fails closed on a malformed IP or an agent with no usable range list —
    see `fetch_bot_ranges`'s docstring for why that direction is the safe one.
    """
    nets = ranges.get(agent) or []
    if not nets:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in n for n in nets)


def aggregate(lines: Iterable[str], since: date, until: date,
             ranges: dict[str, list] | None = None,
             ranges_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fold log lines into one week's figures. `until` is inclusive.

    `ranges` (agent -> list[ip_network]) drives the VERIFIED split; pass None
    to skip verification entirely (existing callers/tests that only care about
    CLAIMED behaviour keep working unchanged — `verified_*` keys are simply
    absent, never a lying zero). `main()` supplies real ranges from
    `load_all_bot_ranges()`; tests inject a fixed dict so no test hits network.
    """
    by_agent: dict[str, int] = {a: 0 for a in AI_ANSWER_AGENTS}
    by_bucket: dict[str, int] = {}
    by_page: dict[str, int] = {}
    v_by_agent: dict[str, int] = {a: 0 for a in AI_ANSWER_AGENTS}
    v_by_bucket: dict[str, int] = {}
    v_by_page: dict[str, int] = {}
    pages: set[str] = set()
    v_pages: set[str] = set()
    days: set[str] = set()
    fetches = 0
    v_fetches = 0
    for line in lines:
        rec = parse_line(line)
        if rec is None:
            continue
        d = date.fromisoformat(rec["date"])
        if not (since <= d <= until):
            continue
        fetches += 1
        by_agent[rec["agent"]] += 1
        pages.add(rec["path"])
        by_page[rec["path"]] = by_page.get(rec["path"], 0) + 1
        days.add(rec["date"])
        bucket = rec["path"].split("/")[1] if rec["path"] != "/" else "(home)"
        by_bucket[bucket] = by_bucket.get(bucket, 0) + 1
        if ranges is not None and verify_ip(rec["ip"], rec["agent"], ranges):
            v_fetches += 1
            v_by_agent[rec["agent"]] += 1
            v_pages.add(rec["path"])
            v_by_page[rec["path"]] = v_by_page.get(rec["path"], 0) + 1
            v_by_bucket[bucket] = v_by_bucket.get(bucket, 0) + 1
    span = (until - since).days + 1
    out = {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "days_covered": len(days),
        # CLAIMED — UA string match only. See module docstring: never report
        # this as "the" number, it is exactly the field that mixed real
        # agents with spoofed scanners in the 654 row.
        "ai_fetches": fetches,
        "ai_fetches_per_day": round(fetches / span, 1),
        "ai_pages_fetched": len(pages),
        "by_agent": by_agent,
        "by_bucket": dict(sorted(by_bucket.items(), key=lambda kv: -kv[1])),
        # per-page counts: the ranking W1 works down and the complement W2
        # works through. `ai_pages_fetched` is len(by_page) by construction.
        "by_page": dict(sorted(by_page.items(), key=lambda kv: (-kv[1], kv[0]))),
    }
    if ranges is not None:
        out.update({
            # VERIFIED — claimed AND the source IP is in the vendor's own
            # published range file. This is the field a verdict may use.
            "verified_ai_fetches": v_fetches,
            "verified_ai_fetches_per_day": round(v_fetches / span, 1),
            "verified_ai_pages_fetched": len(v_pages),
            "verified_by_agent": v_by_agent,
            "verified_by_bucket": dict(sorted(v_by_bucket.items(), key=lambda kv: -kv[1])),
            "verified_by_page": dict(sorted(v_by_page.items(), key=lambda kv: (-kv[1], kv[0]))),
            "verification": ranges_meta or {},
        })
    return out


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def _read_logs() -> Iterable[str]:
    """Every rotated nginx access log, oldest first.

    The logs are root:adm 0640 and this process is neither. `sudo -n cat` is
    the fallback so a plain user cron still works; the clean fix is to add the
    cron user to `adm` (or run this from a root cron), after which the direct
    read succeeds and sudo is never reached.
    """
    import glob as _glob
    for path in sorted(_glob.glob(NGINX_GLOB)):
        opener = gzip.open if path.endswith(".gz") else open
        try:
            with opener(path, "rt", encoding="utf-8", errors="replace") as f:
                yield from f
        except PermissionError:
            cmd = ["sudo", "-n", "zcat" if path.endswith(".gz") else "cat", path]
            p = subprocess.run(cmd, capture_output=True, text=True)
            if p.returncode != 0:
                print(f"[aeo] cannot read {path}: {p.stderr.strip()[:120]}",
                      file=sys.stderr)
                continue
            yield from p.stdout.splitlines()


def iso_week_bounds(week: str) -> tuple[date, date]:
    """'2026-W32' -> (Monday, Sunday)."""
    y, w = week.split("-W")
    mon = date.fromisocalendar(int(y), int(w), 1)
    return mon, mon + timedelta(days=6)


def last_complete_week(today: date) -> str:
    """The ISO week that ended before `today`'s week started."""
    mon_this = today - timedelta(days=today.weekday())
    prev = mon_this - timedelta(days=1)
    y, w, _ = prev.isocalendar()
    return f"{y}-W{w:02d}"


def write_week(week: str, agg: dict[str, Any]) -> Path:
    """Persist the week twice: a per-week file and one history line.

    history.jsonl is the record that survives the 15-day log rotation — the
    per-week json is a convenience. Re-running a week REPLACES its history
    line rather than appending a duplicate.
    """
    AEO_DIR.mkdir(parents=True, exist_ok=True)
    out = AEO_DIR / f"{week}.json"
    out.write_text(json.dumps(agg, indent=2) + "\n", encoding="utf-8")

    rows = []
    if HISTORY.exists():
        for line in HISTORY.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("week") != week:
                rows.append(row)
    rows.append({"week": week, **agg})
    rows.sort(key=lambda r: r.get("week", ""))
    HISTORY.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return out


def load_week(week: str) -> dict[str, Any] | None:
    """Read one week back out of history.jsonl (rotation-proof)."""
    if not HISTORY.exists():
        return None
    for line in HISTORY.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("week") == week:
            return row
    return None


# ---------------------------------------------------------------------------
# Targets: what the week's numbers say should be worked on
#
# Fable's ruling of 2026-08-13 (cch_r1/fable_a_recombine.log) forbids widening
# the site by recombining bank keys — key x partner, key x scenario and key
# pairs are doorway pages by construction, and the geo axis stays shut until a
# demand row exists. The conveyor therefore runs on three MEASURED lists, all
# of which are just projections of the week aggregate this file already builds:
#
#   W1 hot        — top pages by agent fetches: verify figures, deepen
#   W2 cold       — pages agents never fetched: diagnose, upgrade-thin
#   W3 demand_404 — article-shaped paths agents ASKED FOR and we do not have.
#                   This is a Y55 demand row (number + window + instrument) and
#                   currently the only lawful reason to print a NEW page.
#
# The 404 law repeats the 200 law's caution and adds one clause: the path must
# be article-shaped. That clause is not a nicety, it is the whole defence.
# Measured over one retention window on this host: 87 785 404s, of which 1 288
# carry an ANSWER-TIME user agent — `ChatGPT-User` itself, not the index
# crawlers — and they ask for /api/.env, /.git/config, /firebase-adminsdk.json,
# /.claude.json. The user agent is a header; a scanner types whatever it likes
# into it, so filtering by agent name protects nothing here. Requiring
# /<bucket>/<slug>/ took those 1 288 down to ONE real row
# (/reviews/brighty-card-review/, 1 hit, below threshold) without a blacklist
# that would need feeding. The cost of getting this wrong is a page written for
# a vulnerability scanner and a keyword bank taught by one.
# ---------------------------------------------------------------------------

MIN_404_FETCHES = 3          # below this a path is noise, not demand
HOT_N = 20                   # W1 batch size

CONTENT_DIR = ROOT / "seo-site" / "src" / "content" / "pseo"


def parse_404(line: str) -> dict[str, Any] | None:
    """-> {date, path} for an answer-time agent's 404 on an article-shaped path.

    Same shape as parse_line, opposite status. Kept separate rather than
    parameterised: the 200 law is what the whole AEO programme is judged on and
    it must stay readable as one thing.
    """
    m = _LINE_RE.match(line)
    if not m:
        return None
    _ip, ts_raw, path, status, ua = m.groups()
    if status != "404":
        return None
    if agent_of(ua) is None:
        return None
    path = path.split("?", 1)[0]
    if not path.endswith("/"):
        path += "/"
    if not _ARTICLE_RE.match(path):     # NOT is_article_path: "/" is never a 404
        return None
    try:
        ts = datetime.strptime(ts_raw.split()[0], "%d/%b/%Y:%H:%M:%S").date()
    except ValueError:
        return None
    return {"date": ts.isoformat(), "path": path}


def mine_404s(lines: Iterable[str], since: date, until: date) -> dict[str, int]:
    """Article-shaped paths agents asked for and got nothing, path -> hits."""
    out: dict[str, int] = {}
    for line in lines:
        rec = parse_404(line)
        if rec is None:
            continue
        d = date.fromisoformat(rec["date"])
        if not (since <= d <= until):
            continue
        out[rec["path"]] = out.get(rec["path"], 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def article_paths(content_dir: Path | None = None) -> set[str]:
    """`/bucket/slug/` for every published pSEO page on disk."""
    d = content_dir or CONTENT_DIR
    out: set[str] = set()
    for f in sorted(d.glob("*.md")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines()[:40]:
            if line.startswith("bucket:"):
                bucket = line.split(":", 1)[1].strip().strip('"\'')
                if bucket:
                    out.add(f"/{bucket}/{f.stem}/")
                break
    return out


def build_targets(agg: dict[str, Any], demand: dict[str, int],
                  existing: set[str]) -> dict[str, Any]:
    """Project one week's aggregate onto the three work lists.

    `hot` and `cold` partition the published pages: every path on disk is in
    exactly one of them. A page fetched by agents but absent from disk (a stale
    URL still being served from an old build) lands in neither and is reported
    separately rather than silently folded into `hot`.

    Prefers `verified_by_page` when the aggregate carries one (editorial time
    should chase pages real agents fetched, not ones a scanner inflated) —
    checked by KEY presence, not truthiness, so a week that is genuinely all-
    unverified (`{}`) is not silently rewritten back into the claimed figures.
    Callers that never verify (older history rows, existing tests) fall back
    to plain `by_page` unchanged.
    """
    by_page = agg["verified_by_page"] if "verified_by_page" in agg else (agg.get("by_page") or {})
    hot = [{"path": p, "fetches": n} for p, n in by_page.items() if p in existing]
    fetched = {p for p in by_page if p in existing}
    cold = sorted(existing - fetched)
    orphan = sorted(p for p in by_page if p not in existing and p != "/")
    return {
        "week": agg.get("week", ""),
        "since": agg.get("since", ""),
        "until": agg.get("until", ""),
        "pages_total": len(existing),
        "hot_total": len(hot),
        "cold_total": len(cold),
        "hot": hot[:HOT_N],
        "cold": cold,
        "demand_404": [{"path": p, "hits": n} for p, n in demand.items()
                       if n >= MIN_404_FETCHES],
        "demand_404_below_threshold": sum(1 for n in demand.values()
                                          if n < MIN_404_FETCHES),
        "orphan_fetched": orphan,
    }


def targets_path(week: str) -> Path:
    return AEO_DIR / f"targets-{week}.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", help="ISO week, e.g. 2026-W32 (default: last complete)")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--print", dest="print_only", action="store_true")
    ap.add_argument("--targets", action="store_true",
                    help="also write logs/aeo/targets-<week>.json (W1/W2/W3 lists)")
    a = ap.parse_args()

    if a.since and a.until:
        since, until = date.fromisoformat(a.since), date.fromisoformat(a.until)
        y, w, _ = since.isocalendar()
        week = a.week or f"{y}-W{w:02d}"
    else:
        week = a.week or last_complete_week(date.today())
        since, until = iso_week_bounds(week)

    ranges, ranges_meta = load_all_bot_ranges()
    agg = aggregate(_read_logs(), since, until, ranges=ranges, ranges_meta=ranges_meta)
    shown = {k: v for k, v in agg.items() if k not in ("by_page", "verified_by_page")}
    print(json.dumps({"week": week, **shown}, indent=2))
    if agg["ai_fetches"] == 0:
        print("[aeo] WARNING: 0 fetches — logs unreadable or window outside "
              "retention. NOT written (a zero here would read as 'nobody cited "
              "us' and that is a measurement failure, not a fact).",
              file=sys.stderr)
        return 1
    print(f"[aeo] claimed={agg['ai_fetches']} verified={agg.get('verified_ai_fetches', 'n/a')} "
          f"ranges=" + ", ".join(f"{k}:{v['source']}({v['prefix_count']})"
                                 for k, v in ranges_meta.items()), file=sys.stderr)
    if not a.print_only:
        print(f"[aeo] wrote {write_week(week, agg)} + history line", file=sys.stderr)

    if a.targets:
        # A second pass over the logs: 404s are a different law, and the
        # generator that reads this file must not have to re-parse nginx.
        demand = mine_404s(_read_logs(), since, until)
        tg = build_targets({"week": week, **agg}, demand, article_paths())
        if not a.print_only:
            targets_path(week).write_text(json.dumps(tg, indent=2) + "\n",
                                          encoding="utf-8")
            print(f"[aeo] wrote {targets_path(week)}", file=sys.stderr)
        print(f"[aeo] W1 hot={tg['hot_total']} (top {len(tg['hot'])}) "
              f"W2 cold={tg['cold_total']}/{tg['pages_total']} "
              f"W3 demand_404={len(tg['demand_404'])} "
              f"(+{tg['demand_404_below_threshold']} below {MIN_404_FETCHES}) "
              f"orphan={len(tg['orphan_fetched'])}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
