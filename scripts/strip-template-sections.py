#!/usr/bin/env python3
"""strip-template-sections.py — CCH-KW-02 backfill.

Removes body H2 sections ("What to Watch", "Bottom Line", "FAQ") that the
template already renders from frontmatter, using the SAME function the writer
calls (pseo-generate.strip_template_sections). generated_at is NOT bumped: the
reader saw the section twice and will see it once; the content did not change.
FROZEN pages are in scope — this is duplicate removal, not regeneration.

    strip-template-sections.py --dry-run
    strip-template-sections.py --write --expect 428
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pg = _load("pseo_generate", "scripts/pseo-generate.py")
pu = _load("page_updates", "scripts/page-updates.py")
CONTENT = ROOT / "seo-site" / "src" / "content" / "pseo"


def plan(content: Path) -> list[tuple[Path, str, int]]:
    out = []
    for f in sorted(content.glob("*.md")):
        fm, body = pu.split_frontmatter(f.read_text(encoding="utf-8"))
        payload = yaml.safe_load(fm.strip().strip("-")) or {}
        new, n = pg.strip_template_sections(body, payload)
        if n:
            out.append((f, fm + new, n))
    return out


def run(content: Path, expect: int | None, write: bool) -> int:
    changes = plan(content)
    for f, _, n in changes:
        print(f"{f.name}: {n}")
    print(f"files changed: {len(changes)}")
    if write:
        if expect is None:
            print("REFUSED: --write requires --expect N — nothing written.")
            return 1
        tol = max(2, round(expect * 0.1))
        if abs(len(changes) - expect) > tol:
            print(f"REFUSED: expected {expect}±{tol} files, this run changes "
                  f"{len(changes)} — nothing written.")
            return 1
        for f, text, _ in changes:
            f.write_text(text, encoding="utf-8")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", type=int)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--content", type=Path, default=CONTENT)
    a = ap.parse_args()
    return run(a.content, a.expect, a.write and not a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
