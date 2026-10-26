#!/usr/bin/env python3
"""G37 backfill: rank every pSEO page by AI-tell density (--report, zero model
calls, same functions as `humanize_gate.py --corpus`), then rewrite the worst
ones at a fixed pace (--write).

    humanize-backfill.py --report [--corpus DIR] [--out FILE]
    humanize-backfill.py --write --expect N [--cap 10] [--dry-run]

THE LAW (FLEET.md corpus-editor law): --expect is mandatory; the number of
pages this run would attempt must be within max(2, 10% of N) of it, else it
refuses BEFORE any model call or write. DAILY_CAP is a literal; --cap may only
lower it. Skipped always: FROZEN_SLUGS (measured converters), the hand-written
claude-sonnet-5 pages, any slug already in the ledger, score <= THRESHOLD.
--dry-run prints the plan and makes zero model calls. title, h1, description
and supporting_keywords are never touched (humanize_gate.payload_fields does
not include them; only payload_fields keys are copied back).
"""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import os
import json
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import humanize_gate as hg  # noqa: E402
import money_gate  # noqa: E402
import telegram_lib  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "logs" / "humanize" / "backfill-report.json"
SONNET_STAMP = "claude-sonnet-5"
LEDGER = ROOT / "logs" / "humanize" / "backfill.jsonl"
DAILY_CAP = 10
CALL = None   # tests inject a stub; None = pg.call_claude_cli
_BACK_KEYS = ("intro_paragraph", "long_context_paragraph", "snapshot_bullets",
              "what_to_watch", "bottom_line", "faq")


def _page_updates():
    spec = importlib.util.spec_from_file_location("page_updates_for_backfill", ROOT / "scripts" / "page-updates.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def build_report(corpus: Path, now: str) -> dict:
    split = _page_updates().split_frontmatter
    frozen = hg._load_pg().FROZEN_SLUGS
    rows = []
    for p in sorted(corpus.glob("*.md")):
        fm_block, body = split(p.read_text(encoding="utf-8"))
        fm = yaml.safe_load(fm_block.strip()[3:-3]) or {}
        fm["body_markdown"] = body
        text = "\n\n".join(hg.payload_fields(fm))
        flags = hg.lint(text)
        words = hg._count_words(text)
        rows.append({
            "slug": p.stem, "bucket": fm.get("bucket"),
            "score": round(len(flags) * 1000 / words, 2) if words else 0.0,
            "words": words,
            "flags_by_kind": dict(Counter(f.kind for f in flags)),
            "frozen": p.stem in frozen,
            "model_stamp": fm.get("model"), "generated_at": fm.get("generated_at"),
        })
    rows.sort(key=lambda r: -r["score"])
    header = {
        "pages": len(rows),
        "above_threshold": sum(r["score"] > hg.THRESHOLD for r in rows),
        "median_score": round(statistics.median(r["score"] for r in rows), 2) if rows else 0.0,
        "generated_at": now, "threshold": hg.THRESHOLD,
    }
    return {"header": header, "pages": rows}


def _ledger_rows() -> list[dict]:
    if not LEDGER.exists():
        return []
    return [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]


def _ledger_append(row: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER.with_suffix(".jsonl.lock"), "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _rewrite(path: Path, now: datetime, call):
    """-> (new_text | None, ledger fields). Never writes."""
    pu, pg = _page_updates(), hg._load_pg()
    original = path.read_text(encoding="utf-8")
    fm_block, body = pu.split_frontmatter(original)
    d = yaml.safe_load(fm_block.strip()[3:-3]) or {}
    payload = dict(d)
    payload["body_markdown"] = body
    before = hg.score("\n\n".join(hg.payload_fields(payload)))
    res = hg.humanize(payload, call=call)
    row = {"score_before": round(before, 2), "score_after": round(before, 2),
           "accepted": res.accepted, "rejected": res.rejected,
           "refused_reason": res.refused_reason, "cost_usd": res.cost_usd}
    if res.refused_reason:
        return None, row
    if not res.rewritten:
        row["refused_reason"] = "not_rewritten"
        return None, row
    row["score_after"] = round(hg.score("\n\n".join(hg.payload_fields(res.payload))), 2)
    for k in _BACK_KEYS:
        if k in d:
            d[k] = res.payload[k]
    if "generated_at" in d:
        d["generated_at"] = str(d["generated_at"])   # unquoted YAML timestamps: bump needs the quoted form
    new_body = res.payload["body_markdown"]
    new_text = pg.emit_frontmatter(d) + "\n" + new_body
    new_text = pu.bump_generated_at(new_text, now)   # a real content edit earns its date
    unbacked = money_gate.unbacked(new_text, parent_text=original)
    if unbacked:
        row["refused_reason"] = f"G36:unbacked:{','.join(unbacked)[:120]}"
        return None, row
    return new_text, row


def write_batch(corpus: Path, expect: int, cap: int, dry_run: bool, call=None) -> int:
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    cap = min(DAILY_CAP, cap)
    rows = _ledger_rows()
    seen = {r["slug"] for r in rows}
    left = cap - sum(r["ts"].startswith(today) for r in rows)
    cands = [r["slug"] for r in build_report(corpus, now.isoformat())["pages"]
             if not r["frozen"] and r["model_stamp"] != SONNET_STAMP
             and r["slug"] not in seen and r["score"] > hg.THRESHOLD]
    plan = cands[:max(0, left)]
    tol = max(2, round(expect * 0.1))
    if abs(len(plan) - expect) > tol:
        print(f"REFUSED: expected {expect}±{tol} pages, this run would attempt {len(plan)} "
              f"(cap left {left}, candidates {len(cands)}). Nothing called, nothing written.", file=sys.stderr)
        return 3
    if dry_run:
        print(f"dry-run: would attempt {len(plan)} (cap left {left}, candidates {len(cands)}):")
        for s in plan:
            print("  ", s)
        return 0
    written = refused = 0
    cost = 0.0
    for slug in plan:
        path = corpus / f"{slug}.md"
        new_text, row = _rewrite(path, now, call)
        if new_text is not None:
            tmp = path.with_suffix(".md.tmp")
            tmp.write_text(new_text, encoding="utf-8")
            os.replace(tmp, path)
            written += 1
        else:
            refused += 1
        cost += row["cost_usd"]
        _ledger_append({"ts": datetime.now(timezone.utc).isoformat(), "slug": slug, **row})
        print(json.dumps({"slug": slug, **row}))
    summary = f"humanize-backfill: written={written} refused={refused} cost=${cost:.4f}"
    sent = telegram_lib.alert(summary)
    print(f"{summary} telegram_sent={sent}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--expect", type=int)
    ap.add_argument("--cap", type=int, default=DAILY_CAP)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--corpus", type=Path, default=hg.CORPUS)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    if args.write:
        if args.expect is None:
            ap.error("--write requires --expect N")
        return write_batch(args.corpus, args.expect, args.cap, args.dry_run, CALL)
    if not args.report:
        ap.error("pass --report")
    rep = build_report(args.corpus, datetime.now(timezone.utc).isoformat())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    h, rows = rep["header"], rep["pages"]
    print(f"pages={h['pages']} above_threshold={h['above_threshold']} (> {h['threshold']}) median={h['median_score']}")
    print("top 20:")
    for r in rows[:20]:
        tag = " [FROZEN]" if r["frozen"] else (" [SONNET]" if r["model_stamp"] == SONNET_STAMP else "")
        print(f"  {r['score']:7.2f}  {r['slug']}{tag}")
    fz = [r for r in rows if r["frozen"]]
    sn = [r for r in rows if r["model_stamp"] == SONNET_STAMP]
    print(f"FROZEN ({len(fz)}), above threshold {sum(r['score'] > hg.THRESHOLD for r in fz)}:")
    for r in fz:
        print(f"  {r['score']:7.2f}  {r['slug']}")
    print(f"model_stamp={SONNET_STAMP} ({len(sn)}):")
    for r in sn:
        print(f"  {r['score']:7.2f}  {r['slug']}")
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
