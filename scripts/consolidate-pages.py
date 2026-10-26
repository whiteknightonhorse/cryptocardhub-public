#!/usr/bin/env python3
"""CCH-SEO-09: consolidate near-duplicate pages — keep the stronger, 301 the weaker.

Input is the operator's config/consolidations.tsv (loser<TAB>keeper<TAB>decision-date, no header).
No model touches any page. Everything is validated BEFORE the first write.

  consolidate-pages.py --expect N [--dry-run] [--tsv PATH]
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TSV = ROOT / "config" / "consolidations.tsv"
SITE = ROOT / "seo-site"
CONTENT = SITE / "src" / "content" / "pseo"
DIST = SITE / "dist"
REDIRECTS = SITE / "redirects.map"
# src/content/pseo is gitignored, so `git rm` is no undo: keep a copy of every deleted page.
BACKUP = ROOT / "logs" / "pseo" / "consolidated"

_PATH = re.compile(r"^/([a-z0-9-]+)/([a-z0-9-]+)/$")


def _pg():
    spec = importlib.util.spec_from_file_location("pseo_generate", ROOT / "scripts" / "pseo-generate.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("pseo_generate", m)
    spec.loader.exec_module(m)
    return m


def _slug(path: str) -> str:
    return _PATH.match(path).group(2)


def _redirect_rows() -> dict[str, str]:
    out: dict[str, str] = {}
    if REDIRECTS.exists():
        for line in REDIRECTS.read_text(encoding="utf-8").splitlines():
            p = line.split()
            if len(p) == 2 and not line.lstrip().startswith("#"):
                out[p[0]] = p[1]
    return out


def read_rows(tsv: Path) -> list[tuple[str, str, str]]:
    rows = []
    for n, line in enumerate(tsv.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) != 3 or not all(c.strip() for c in cols):
            raise SystemExit(f"refuse: {tsv.name} line {n}: need 3 tab-separated columns")
        rows.append(tuple(c.strip() for c in cols))
    return rows


def validate(rows, expect: int, assert_not_frozen) -> None:
    if len(rows) != expect:
        raise SystemExit(f"refuse: {len(rows)} TSV row(s) but --expect {expect}")
    losers = [r[0] for r in rows]
    red = _redirect_rows()
    targets = set(red.values())
    for loser, keeper, _ in rows:
        for p in (loser, keeper):
            if not _PATH.match(p):
                raise SystemExit(f"refuse: {p!r} is not a /bucket/slug/ path")
            if not (DIST / p.strip("/") / "index.html").is_file():
                raise SystemExit(f"refuse: {p} is not built in dist")
        if loser == keeper:
            raise SystemExit(f"refuse: loser == keeper ({loser})")
        if keeper in losers:
            raise SystemExit(f"refuse: keeper {keeper} is a loser in another row")
        if losers.count(loser) > 1:
            raise SystemExit(f"refuse: {loser} listed twice")
        if loser in targets:
            raise SystemExit(f"refuse: loser {loser} is a redirects.map target (301 -> 301 chain)")
        if keeper in red:
            raise SystemExit(f"refuse: keeper {keeper} is itself a redirects.map source")
        if loser in red:
            raise SystemExit(f"refuse: {loser} is already a redirects.map source")
        if not (CONTENT / f"{_slug(loser)}.md").is_file():
            raise SystemExit(f"refuse: no source file for {loser}")
        assert_not_frozen(_slug(loser))


def apply(rows) -> None:
    pg = _pg()
    BACKUP.mkdir(parents=True, exist_ok=True)
    with REDIRECTS.open("a", encoding="utf-8") as fh:
        fh.write("\n# CCH-SEO-09 — near-duplicate consolidation: weaker page 301s to the stronger.\n")
        for loser, keeper, _ in rows:
            fh.write(f"{loser}\t{keeper}\n")
    by_slug = {}
    for loser, keeper, _ in rows:
        src = CONTENT / f"{_slug(loser)}.md"
        shutil.copy2(src, BACKUP / src.name)
        src.unlink()
        by_slug[_slug(loser)] = keeper

    def mark(led):
        for c in led.get("cohorts", []):
            for it in c.get("items", []):
                stem = Path((it.get("published") or {}).get("path", "")).stem
                for s in {it.get("slug"), stem} & set(by_slug):
                    it["consolidated_into"] = by_slug[s]

    pg._faq_ledger_txn(mark)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", type=int, required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tsv", type=Path, default=None)
    a = ap.parse_args(argv)
    rows = read_rows(a.tsv or TSV)
    validate(rows, a.expect, _pg().assert_not_frozen)
    for loser, keeper, day in rows:
        print(f"{'PLAN' if a.dry_run else 'APPLY'}: {loser} -> {keeper} ({day})")
    if not a.dry_run:
        apply(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
