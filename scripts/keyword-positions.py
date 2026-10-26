#!/usr/bin/env python3
"""keyword-positions.py — where our pages stand for the keywords we placed (CCH-KW-05).

    keyword-positions.py --snapshot before     -> logs/seo/keyword-positions-before-<date>.json
    keyword-positions.py --compare a.json b.json

Law: a measurement that could not be made is "unmeasured", never 0. A Bing
ThrottleHost after retries, a keyword with no row, a dead GSC client — all
"unmeasured". Google positions at this scale are noise; Bing is the instrument,
GSC is written for the record.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLACEMENTS = ROOT / "config" / "keyword-placements.tsv"
OUT_DIR = ROOT / "logs" / "seo"
MAX_PER_PATH = 2   # the TSV is the law: <=2 target keywords per page
UNMEASURED = "unmeasured"


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_placements(path: Path = PLACEMENTS) -> list[dict]:
    rows = []
    per_path: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        kw, page, role = (line.split("\t") + [""])[:3]
        per_path[page] = per_path.get(page, 0) + 1
        if per_path[page] > MAX_PER_PATH:
            raise SystemExit(f"refused: more than {MAX_PER_PATH} target keywords on {page}")
        rows.append({"keyword": kw, "path": page, "role": role})
    return rows


def measure_bing(keyword: str, path: str, stats_fn, failed_exc) -> dict | str:
    try:
        rows = stats_fn(keyword)
    except failed_exc:
        return UNMEASURED
    for r in rows:
        if r["path"] == path:
            return {k: r.get(k) for k in ("position", "impressions", "clicks")}
    return UNMEASURED   # no row = Bing shows no such page for the query; not proof of 0


def measure_gsc(keyword: str, path: str, client, site_url: str, host: str,
                today: date | None = None) -> dict | str:
    today = today or date.today()
    try:
        r = client.searchanalytics().query(siteUrl=site_url, body={
            "startDate": (today - timedelta(days=28)).isoformat(),
            "endDate": today.isoformat(),
            "dimensions": ["query", "page"],
            "dimensionFilterGroups": [{"filters": [
                {"dimension": "query", "operator": "equals", "expression": keyword}]}],
            "rowLimit": 25}).execute()
    except Exception:
        return UNMEASURED
    want = (host.rstrip("/") + path).rstrip("/")
    for row in r.get("rows") or []:
        if row["keys"][1].rstrip("/") == want:
            return {"clicks": row.get("clicks"), "impressions": row.get("impressions"),
                    "position": round(row.get("position", 0), 1)}
    return UNMEASURED


def snapshot(rows: list[dict], stats_fn, failed_exc, gsc_client, site_url: str, host: str) -> dict:
    out = []
    for r in rows:
        out.append({**r,
                    "bing": measure_bing(r["keyword"], r["path"], stats_fn, failed_exc),
                    "gsc": (measure_gsc(r["keyword"], r["path"], gsc_client, site_url, host)
                            if gsc_client else UNMEASURED)})
    return {"date": date.today().isoformat(), "rows": out}


def _cell(v, key) -> str:
    return str(v[key]) if isinstance(v, dict) else UNMEASURED


def compare(a: dict, b: dict) -> str:
    bmap = {(r["keyword"], r["path"]): r for r in b["rows"]}
    lines = [f"| keyword | page | bing pos {a['date']}→{b['date']} | gsc impr {a['date']}→{b['date']} |",
             "|---|---|---|---|"]
    for r in a["rows"]:
        n = bmap.get((r["keyword"], r["path"]), {})
        lines.append(f"| {r['keyword']} | {r['path']} | "
                     f"{_cell(r['bing'], 'position')}→{_cell(n.get('bing'), 'position')} | "
                     f"{_cell(r['gsc'], 'impressions')}→{_cell(n.get('gsc'), 'impressions')} |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--snapshot", metavar="TAG")
    g.add_argument("--compare", nargs=2, metavar=("A", "B"))
    a = ap.parse_args()
    if a.compare:
        print(compare(*(json.loads(Path(p).read_text()) for p in a.compare)))
        return 0
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    bk = _load("bing_keywords", "scripts/bing-keywords.py")
    gsc = _load("gsc_report", "scripts/gsc-report.py")
    try:
        client = gsc._client()
    except Exception as e:
        print(f"gsc client unavailable: {type(e).__name__}", file=sys.stderr)
        client = None
    snap = snapshot(load_placements(), bk.query_page_stats, bk.BingMeasurementFailed,
                    client, gsc.SITE_URL, gsc.SITE_HOST)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"keyword-positions-{a.snapshot}-{snap['date']}.json"
    out.write_text(json.dumps(snap, indent=1) + "\n", encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
