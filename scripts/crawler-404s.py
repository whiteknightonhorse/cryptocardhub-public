#!/usr/bin/env python3
"""Every URL a VERIFIED Googlebot/bingbot asked for and got 404 (CCH-SEO-02).

Reuses aeo-aggregate.py for the log reader, line regex and range cache; adds the
two search-crawler range sources (same {"prefixes": [...]} JSON shape).
Read-only; writes only logs/gsc/crawler-404s.json. Feeds CCH-SEO-03.
Run with video-generator/venv/bin/python (weekly, raw logs rotate in ~15 d).
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


aeo = _load("aeo_aggregate_c404", "scripts/aeo-aggregate.py")

CRAWLERS = ("Googlebot", "bingbot")
aeo.BOT_RANGES_URL.update({
    "Googlebot": "https://developers.google.com/static/search/apis/ipranges/googlebot.json",
    "bingbot": "https://www.bing.com/toolbox/bingbot.json",
})
OUT = ROOT / "logs" / "gsc" / "crawler-404s.json"
HOST = "defycard.com"
_VIDEO_RE = re.compile(r"^/videos/([^/]+)/$")
_HOST_RE = re.compile(r"\bhost=(\S+)\s*$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _crawler_of(ua: str) -> str | None:
    low = ua.lower()
    return next((c for c in CRAWLERS if c.lower() in low), None)


def _shape_ok(path: str) -> bool:
    if aeo._ARTICLE_RE.match(path) or _VIDEO_RE.match(path):
        return True
    return path.endswith("/") and len([s for s in path.split("/") if s]) <= 3


def mine(lines, ranges) -> dict:
    paths: dict = {}
    unverified = 0
    dates: list[str] = []
    for line in lines:
        m = aeo._LINE_RE.match(line)
        if not m:
            continue
        ip, ts_raw, path, status, ua = m.groups()
        if status != "404":
            continue
        crawler = _crawler_of(ua)
        if crawler is None:
            continue
        h = _HOST_RE.search(line)
        if h and h.group(1) != HOST:
            continue
        path = path.split("?", 1)[0]
        if not _shape_ok(path):
            continue
        if not aeo.verify_ip(ip, crawler, ranges):
            unverified += 1
            continue
        try:
            d = datetime.strptime(ts_raw.split()[0], "%d/%b/%Y:%H:%M:%S").date().isoformat()
        except ValueError:
            continue
        dates.append(d)
        row = paths.setdefault(path, {"hits": 0, "last_seen": d, "agents": {"Googlebot": 0, "bingbot": 0}})
        row["hits"] += 1
        row["agents"][crawler] += 1
        row["last_seen"] = max(row["last_seen"], d)
    window = [min(dates), max(dates)] if dates else [None, None]
    return {"window": window, "paths": paths, "claimed_unverified": unverified}


def served_paths(root: Path = ROOT / "seo-site") -> set[str]:
    """Pages that exist: built dist dirs (if any) + content sources (dist may be partial)."""
    out: set[str] = set()
    dist = root / "dist"
    if dist.is_dir():
        for p in dist.rglob("index.html"):
            out.add("/" + p.parent.relative_to(dist).as_posix() + "/")
    for md in (root / "src" / "content" / "pseo").glob("*.md"):
        head = md.read_text(encoding="utf-8", errors="replace")[:1500]
        b = re.search(r'^bucket:\s*"?([\w-]+)', head, re.M)
        s = re.search(r'^slug:\s*"?([\w-]+)', head, re.M)
        if b and s:
            out.add(f"/{b.group(1)}/{s.group(1)}/")
    for j in (root / "src" / "content" / "videos").glob("*.json"):
        out.add(f"/videos/{j.stem}/")
    out.discard("//")
    return out


def _slug_tokens(path: str) -> set[str]:
    return set(_TOKEN_RE.findall(path.strip("/").split("/")[-1].lower()))


def propose(path: str, served: set[str], videos_dir: Path | None = None) -> str:
    vm = _VIDEO_RE.match(path)
    if vm:
        vd = videos_dir or ROOT / "seo-site" / "src" / "content" / "videos"
        if not (vd / f"{vm.group(1)}.json").exists():
            return "410"
    want, bucket = _slug_tokens(path), path.strip("/").split("/")[0]
    best, key = "-", (0.0, 0)
    for cand in sorted(served):
        if cand == path:
            continue
        have = _slug_tokens(cand)
        j = len(want & have) / len(want | have) if want | have else 0.0
        k = (j, cand.strip("/").split("/")[0] == bucket)
        if j > 0 and k > key:
            best, key = cand, k
    return best


def main() -> int:
    ranges = {c: aeo.fetch_bot_ranges(c)[0] for c in CRAWLERS}
    res = mine(aeo._read_logs(), ranges)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    served = served_paths()
    print(f"window {res['window']}  unique 404 paths {len(res['paths'])}  "
          f"claimed_unverified {res['claimed_unverified']}")
    print("hits | last_seen | G/B | path -> proposed")
    for p, r in sorted(res["paths"].items(), key=lambda kv: (-kv[1]["hits"], kv[0])):
        a = r["agents"]
        print(f"{r['hits']:>4} | {r['last_seen']} | {a['Googlebot']}/{a['bingbot']} | {p} -> {propose(p, served)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
