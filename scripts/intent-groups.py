#!/usr/bin/env python3
"""intent-groups.py — CCH-SEO-27: one table of same-intent page groups with
traffic and index state. READ-ONLY: writes logs/seo/intent-groups-<date>.md
and nothing else; edits no page, no redirect, no config.

Groups: (a) reviews by card entity, (b) slug families (-2026 suffix,
best-crypto-card-<x> / best-crypto-card-for-<x> / crypto-card-for-<x>),
(c) the brief-A 3.3 pairs + lost/stolen, (d) /videos/<id>/ summarised by
has-target_pseo_slug yes/no.

An instrument that failed prints "n/a (<cause>)", never 0. A zero is printed
only when the instrument answered and the page had no row (GSC and Bing return
only rows with >=1 impression).

Run with video-generator/venv/bin/python (google libs, Ga4SdkMissing lesson):
  video-generator/venv/bin/python scripts/intent-groups.py
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import urllib.parse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "seo-site" / "src" / "content"
DIST = ROOT / "seo-site" / "dist"
INDEX_STATUS = ROOT / "logs" / "gsc" / "index-status.json"
OUT_DIR = ROOT / "logs" / "seo"
HOST = "https://defycard.com"
WINDOW_DAYS = 90
MIN_SESSIONS = 5  # below this on every member (and none indexed) -> "no proposal"

# Literal, in the card's order (Y49: the law is written out, not derived).
BRANDS = ["crypto.com", "bybit", "redotpay", "nexo", "ether.fi", "gnosis", "coinbase",
          "metamask", "binance", "gemini", "kast", "wirex", "bleap", "uphold"]

# Brief A section 3.3 table (9 pairs) + the lost/stolen pair named by the card.
# Entries are slugs; buckets come from the content frontmatter.
PAIRS = [
    ("crypto-card-for-crypto-traders", "crypto-card-to-spend-without-selling-crypto"),
    ("metamask-card-review-usa", "metamask-card-review"),
    ("crypto-card-for-self-custody", "non-custodial-crypto-card"),
    ("are-crypto-card-rewards-taxable", "are-crypto-card-transactions-taxable"),
    ("best-crypto-debit-card", "what-is-a-crypto-debit-card"),
    ("best-crypto-card-no-foreign-transaction-fee", "crypto-card-with-no-foreign-transaction-fee"),
    ("best-crypto-card-with-no-annual-fee", "what-is-the-best-crypto-card-with-no-annual-fee"),
    ("who-actually-issues-the-ether-fi-cash-card-is-it-ether-fi", "who-issues-ether-fi-cash-card"),
    ("what-is-a-crypto-card", "what-is-a-crypto-debit-card"),
    ("crypto-card-lost-or-stolen", "how-to-report-lost-crypto-card"),
]

_FAMILY_RE = re.compile(r"^(best-crypto-card-for-|best-crypto-card-|crypto-card-for-)")


def _load(name: str, fname: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / fname)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------

def family_key(slug: str) -> str:
    """'best-crypto-card-for-usdt-2026' -> 'usdt'."""
    s = re.sub(r"-2026$", "", slug)
    return _FAMILY_RE.sub("", s, count=1)


def brand_of(slug: str) -> str | None:
    """Brand whose hyphenated token appears EARLIEST in the slug."""
    best, pos = None, None
    for b in BRANDS:
        t = re.sub(r"[.\s]", "-", b)
        m = re.search(rf"(^|-){re.escape(t)}(-|$)", slug)
        if m and (pos is None or m.start() < pos):
            best, pos = b, m.start()
    return best


def fmt(v) -> str:
    """Number -> str; a failed instrument is already an 'n/a (...)' string."""
    return str(v)


def num(v) -> int | None:
    return v if isinstance(v, int) else None


def keeper(members: list[dict]) -> str:
    """Indexed member with most Bing clicks; ties -> most inbound links."""
    def sessions(m):
        return m["ga4"] if isinstance(m["ga4"], int) else 0
    indexed = [m for m in members if m["indexed"] is True]
    unmeasured = [m for m in members if m["indexed"] is None]
    if not indexed and not unmeasured and all(sessions(m) < MIN_SESSIONS for m in members):
        return "no proposal"
    if not indexed:
        if unmeasured:
            return "no proposal (Google index state unmeasured for: " + ", ".join(m["url"] for m in unmeasured) + ")"
        top = max(members, key=sessions)
        return f"no proposal (none indexed; most sessions: {top['url']})"
    bing_unmeasured = [m for m in indexed if num(m["bing_clicks"]) is None]
    if bing_unmeasured:
        return "no proposal (Bing clicks unmeasured)"
    inb = lambda m: m["inbound"] if isinstance(m["inbound"], int) else -1
    k = max(indexed, key=lambda m: (m["bing_clicks"], inb(m)))
    return k["url"]


# ---------------------------------------------------------------------------
# Instruments — each returns (dict | None, cause | None)
# ---------------------------------------------------------------------------

def path_of(u: str) -> str:
    p = urllib.parse.urlparse(u).path or "/"
    return p if p.endswith("/") else p + "/"


def _cause(e: Exception) -> str:
    return f"{type(e).__name__}: {str(e)[:100]}"


def load_pages() -> dict[str, dict]:
    pages: dict[str, dict] = {}
    for f in sorted((CONTENT / "pseo").glob("*.md")):
        head = f.read_text(encoding="utf-8").split("\n---", 2)[0]
        g = lambda k: (re.search(rf'^{k}: "(.*)"$', head, re.M) or [None, None])[1]
        slug, bucket = g("slug") or f.stem, g("bucket")
        pages[slug] = {"slug": slug, "bucket": bucket, "url": f"/{bucket}/{slug}/",
                       "title": g("title"), "generated_at": g("generated_at") or "n/a (no generated_at)"}
    return pages


def index_state() -> tuple[dict | None, str | None]:
    try:
        return json.loads(INDEX_STATUS.read_text())["urls"], None
    except Exception as e:  # noqa: BLE001
        return None, _cause(e)


def gsc_90d(since: str, until: str) -> tuple[dict | None, str | None]:
    try:
        g = _load("gsc_report_ig", "gsc-report.py")
        client, rows, start = g._client(), {}, 0
        while True:
            r = client.searchanalytics().query(siteUrl=g.SITE_URL, body={
                "startDate": since, "endDate": until, "dimensions": ["page"],
                "rowLimit": 25000, "startRow": start}).execute().get("rows", [])
            for x in r:
                rows[path_of(x["keys"][0])] = int(x["impressions"])
            if len(r) < 25000:
                return rows, None
            start += 25000
    except Exception as e:  # noqa: BLE001
        return None, _cause(e)


def bing_90d(since: date, until: date) -> tuple[dict | None, str | None]:
    """GetPageStats = one row per (page, week); the page URL sits in `Query`
    and may carry www. — summed per canonical path."""
    try:
        bk = _load("bing_keywords_ig", "bing-keywords.py")
        rows = bk._get("GetPageStats")
        if not isinstance(rows, list):
            return None, "GetPageStats returned no list"
        qw = _load("quick_wins_ig", "quick-wins.py")
        out: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for r in rows:
            if not (since <= qw.bing_date(r["Date"]) <= until):
                continue
            a = out[path_of(r["Query"])]
            a[0] += int(r.get("Impressions") or 0)
            a[1] += int(r.get("Clicks") or 0)
        return dict(out), None
    except Exception as e:  # noqa: BLE001
        return None, _cause(e)


def ga4_90d(since: str, until: str) -> tuple[dict | None, str | None]:
    """Human ORGANIC sessions per landing page, weekly-report.human_sessions law."""
    try:
        from google.analytics.data_v1beta.types import Filter, FilterExpression
        wr = _load("weekly_report_ig", "weekly-report.py")
        flt = FilterExpression(filter=Filter(
            field_name="sessionDefaultChannelGroup",
            string_filter=Filter.StringFilter(value="Organic Search")))
        rows = wr._run_report(wr._ga4_client(), ["landingPage", "sessionSource"],
                              ["sessions", "engagedSessions"], since, until, flt, limit=100000)
        per: dict[str, list[dict]] = defaultdict(list)
        for (lp, src), (s, e) in rows:
            per[path_of(lp)].append({"source": src, "sessions": s, "engaged": e})
        return {p: wr.human_sessions(r) for p, r in per.items()}, None
    except Exception as e:  # noqa: BLE001
        return None, _cause(e)


def inbound() -> tuple[dict | None, str | None]:
    try:
        if not DIST.is_dir():
            return None, "seo-site/dist not built"
        return _load("inlink_ig", "inlink-histogram.py").histogram(DIST), None
    except Exception as e:  # noqa: BLE001
        return None, _cause(e)


def frozen_slugs() -> frozenset:
    src = (ROOT / "scripts" / "pseo-generate.py").read_text(encoding="utf-8")
    block = re.search(r"FROZEN_SLUGS = frozenset\(\{(.*?)\}\)", src, re.S).group(1)
    return frozenset(re.findall(r'"([^"]+)"', block))


def availability_slugs() -> tuple[set | None, str | None]:
    try:
        import yaml
        d = yaml.safe_load((ROOT / "config" / "availability.yaml").read_text())
        return set(d.get("slug_jurisdictions") or {}) | set(d.get("unavailable_slugs") or []), None
    except Exception as e:  # noqa: BLE001
        return None, _cause(e)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

class Data:
    def __init__(self, today: date):
        until = today - timedelta(days=1)
        since = until - timedelta(days=WINDOW_DAYS - 1)
        self.window = f"{since}..{until}"
        self.pages = load_pages()
        self.idx, self.idx_c = index_state()
        self.gsc, self.gsc_c = gsc_90d(str(since), str(until))
        self.bing, self.bing_c = bing_90d(since, until)
        self.ga4, self.ga4_c = ga4_90d(str(since), str(until))
        self.inb, self.inb_c = inbound()
        self.frozen = frozen_slugs()
        self.avail, self.avail_c = availability_slugs()

    def cell(self, table, cause, path, default=0):
        return f"n/a ({cause})" if table is None else table.get(path, default)

    def row(self, slug: str) -> dict:
        p = self.pages[slug]
        u = p["url"]
        if self.idx is None:
            indexed, google = None, f"n/a ({self.idx_c})"
        elif (HOST + u) not in self.idx:
            indexed, google = None, "n/a (not inspected)"
        else:
            v = self.idx[HOST + u]
            indexed = v.get("verdict") == "PASS"
            google = "indexed" if indexed else f"not indexed ({v.get('coverage')})"
        b = self.bing.get(u, [0, 0]) if self.bing is not None else None
        bc = f"n/a ({self.bing_c})"
        return {
            "url": u, "slug": slug, "indexed": indexed, "google": google,
            "gsc": self.cell(self.gsc, self.gsc_c, u),
            "bing_imp": b[0] if b else bc, "bing_clicks": b[1] if b else bc,
            "ga4": self.cell(self.ga4, self.ga4_c, u),
            "inbound": self.cell(self.inb, self.inb_c, u),
            "generated_at": p["generated_at"],
            "frozen": "yes" if slug in self.frozen else "no",
            "avail": "yes" if slug in self.avail else "no" if self.avail is not None
            else f"n/a ({self.avail_c})",
        }


HEAD = ("| URL | Google coverage | GSC imp 90d | Bing imp 90d | Bing clicks 90d | GA4 human organic 90d "
        "| inbound | generated_at | FROZEN | availability.yaml |\n|---|---|---|---|---|---|---|---|---|---|")


def md_group(name: str, members: list[dict]) -> tuple[list[str], str]:
    k = keeper(members)
    out = [f"### {name}", "", HEAD]
    for m in members:
        out.append(f"| {m['url']} | {m['google']} | {fmt(m['gsc'])} | {fmt(m['bing_imp'])} | "
                   f"{fmt(m['bing_clicks'])} | {fmt(m['ga4'])} | {fmt(m['inbound'])} | "
                   f"{m['generated_at']} | {m['frozen']} | {m['avail']} |")
    guard = [m["url"] for m in members if m["frozen"] == "yes" or m["avail"] == "yes"]
    out += ["", f"Proposed keeper: {k}"]
    if guard:
        out.append("Do not merge (FROZEN / availability.yaml, Y50): " + ", ".join(guard))
    out.append("")
    return out, k


def merge_urls(members: list[dict], k: str) -> set[str]:
    """URLs a proposal would merge INTO the keeper (frozen/availability excluded)."""
    if not k.startswith("/"):
        return set()
    return {m["url"] for m in members
            if m["url"] != k and m["frozen"] != "yes" and m["avail"] != "yes"}


def video_summary(d: Data, today: date) -> list[str]:
    vids = []
    for f in sorted((CONTENT / "videos").glob("*.json")):
        j = json.loads(f.read_text())
        vids.append((j["id"], j.get("target_pseo_slug")))
    out = ["## (d) /videos/<id>/ — one summary row per class", "",
           "| class | videos | resolving target page | Google indexed | GSC imp 90d | Bing imp 90d "
           "| Bing clicks 90d | GA4 human organic 90d |", "|---|---|---|---|---|---|---|---|"]
    for label, has in (("has target_pseo_slug: yes", True), ("has target_pseo_slug: no", False)):
        ids = [i for i, t in vids if bool(t) == has]
        paths = [f"/videos/{i}/" for i in ids]
        resolving = sum(1 for i, t in vids if bool(t) == has and d.pages.get(t.split("/")[-1], {}).get("url") == f"/{t.strip('/')}/") if has else "n/a (no slug)"

        def total(table, cause, idx=None):
            if table is None:
                return f"n/a ({cause})"
            return sum((table.get(p, [0, 0])[idx] if idx is not None else table.get(p, 0)) for p in paths)
        if d.idx is None:
            ind = f"n/a ({d.idx_c})"
        else:
            insp = [d.idx.get(HOST + p) for p in paths]
            ind = f"{sum(1 for v in insp if v and v.get('verdict') == 'PASS')} of {len(paths)}" \
                  f" ({sum(1 for v in insp if v is None)} not inspected)"
        out.append(f"| {label} | {len(ids)} | {resolving} | {ind} | {total(d.gsc, d.gsc_c)} | "
                   f"{total(d.bing, d.bing_c, 0)} | {total(d.bing, d.bing_c, 1)} | {total(d.ga4, d.ga4_c)} |")
    out.append("")
    return out


def build(today: date) -> tuple[str, str]:
    d = Data(today)
    lines = [f"# Intent groups — {today}", "",
             f"Window {d.window} (90 days, ending yesterday). Read-only measure for questions B1-B3 "
             "of DEFY-SEO-1110.ruling-1; no merge is proposed for execution here.", "",
             "Legend: `n/a (<cause>)` = instrument failed or page not inspected, NOT zero. "
             "A bare 0 = the instrument answered and had no row for the page (GSC and Bing return only "
             "rows with >=1 impression; Bing has its own floor, so a Bing 0 is weaker evidence than a GSC 0). "
             "GA4 = human organic sessions via weekly-report.human_sessions. Inbound = unique content pages "
             "linking in (nav/footer/breadcrumb excluded; from dist).", ""]
    groups: list[tuple[str, list[dict]]] = []
    lines += ["## (a) reviews bucket by card entity", ""]
    by_brand: dict[str, list[str]] = defaultdict(list)
    for s, p in d.pages.items():
        if p["bucket"] == "reviews" and (b := brand_of(s)):
            by_brand[b].append(s)
    other = [s for s, p in d.pages.items() if p["bucket"] == "reviews" and not brand_of(s)]
    for b in BRANDS:
        groups.append((f"(a) {b} — {len(by_brand[b])} review URL(s)",
                       [d.row(s) for s in by_brand[b]]))
    n_a = len(BRANDS)
    sections = {"a": groups[:n_a]}
    fam: dict[str, list[str]] = defaultdict(list)
    for s in d.pages:
        fam[family_key(s)].append(s)
    gb = [(f"(b) family '{k}'", [d.row(s) for s in sorted(v)])
          for k, v in sorted(fam.items()) if len(v) > 1]
    gc = []
    for a, b in PAIRS:
        miss = [s for s in (a, b) if s not in d.pages]
        mem = [d.row(s) for s in (a, b) if s in d.pages]
        gc.append((f"(c) {a} / {b}" + (f" — page absent: {', '.join(miss)}" if miss else ""), mem))
    merges: set[str] = set()
    nurls = set()
    for tag, grp, extra in (("a", sections["a"], other), ("b", gb, []), ("c", gc, [])):
        title = {"a": "(a) reviews bucket by card entity", "b": "(b) slug families (-2026 / best-crypto-card[-for] / crypto-card-for)",
                 "c": "(c) named pairs (brief A 3.3 + lost/stolen)"}[tag]
        if tag != "a":
            lines += [f"## {title}", ""]
        for name, mem in grp:
            if not mem:
                lines += [f"### {name}", "", "No members.", ""]
                continue
            out, k = md_group(name, mem)
            lines += out
            merges |= merge_urls(mem, k)
            nurls.update(m["url"] for m in mem)
        if extra:
            lines += [f"Reviews pages matching no brand token: {', '.join('/reviews/' + s + '/' for s in extra)}", ""]
    ngroups = len(groups[:n_a]) + len(gb) + len(gc) + 1
    lines += video_summary(d, today)
    nvid = len(list((CONTENT / "videos").glob("*.json")))
    summary = (f"groups={ngroups} (a:{n_a} b:{len(gb)} c:{len(gc)} d:1) urls={len(nurls)} "
               f"(+{nvid} videos summarised) proposed_merges={len(merges)} (distinct URLs to merge away)")
    lines += ["## Summary", "", summary, ""]
    return "\n".join(lines), summary


def main() -> int:
    from dotenv import load_dotenv  # GA4_PROPERTY_ID / BING key; cron has them already
    load_dotenv(Path.home() / ("." + "env"))
    today = datetime.now(timezone.utc).date()
    text, summary = build(today)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"intent-groups-{today}.md"
    out.write_text(text, encoding="utf-8")
    print(out)
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
