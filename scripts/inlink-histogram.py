#!/usr/bin/env python3
"""Read-only: inbound <a href> per /<bucket>/<slug>/ in seo-site/dist, counted
from OTHER content pages only (header/footer/breadcrumb nav and bucket indexes
excluded). Usage: inlink-histogram.py [dist] [--json]"""
import json, statistics, sys
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

BUCKETS = {"hub", "compare", "reviews", "geo", "tools", "how-to", "for", "learn", "help", "news", "faq"}


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.hrefs = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("header", "footer") or (tag == "nav" and a.get("aria-label") == "Breadcrumb"):
            self.stack.append(tag)
        elif tag == "a" and not self.stack and a.get("href"):
            self.hrefs.append(a["href"])

    def handle_endtag(self, tag):
        if tag in ("header", "footer", "nav") and self.stack and self.stack[-1] == tag:
            self.stack.pop()


def pages(dist: Path):
    for p in sorted(dist.glob("*/*/index.html")):
        if p.parent.parent.name in BUCKETS:
            yield f"/{p.parent.parent.name}/{p.parent.name}/", p


def histogram(dist: Path):
    ps = dict(pages(dist))
    inbound = {u: set() for u in ps}
    for src, f in ps.items():
        l = Links()
        l.feed(f.read_text(errors="ignore"))
        for h in l.hrefs:
            u = urlparse(h)
            if u.netloc not in ("", "defycard.com"):
                continue
            t = u.path if u.path.endswith("/") else u.path + "/"
            if t in inbound and t != src:
                inbound[t].add(src)
    return {u: len(s) for u, s in inbound.items()}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dist = Path(args[0]) if args else Path(__file__).resolve().parent.parent / "seo-site" / "dist"
    h = histogram(dist)
    v = sorted(h.values())
    stats = {"pages": len(v), "min": v[0], "median": statistics.median(v), "max": v[-1],
             "below3": sum(1 for x in v if x < 3)}
    if "--json" in sys.argv:
        print(json.dumps({"stats": stats, "counts": h}))
    else:
        print(stats)
        for u, n in sorted(h.items(), key=lambda kv: (kv[1], kv[0]))[:20]:
            print(f"{n:4d} {u}")
