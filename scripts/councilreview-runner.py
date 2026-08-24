#!/usr/bin/env python3
"""
councilreview-runner.py — autonomous /councilreview loop.

For each round (max N):
  1. Compute git diff main...HEAD
  2. Spawn `claude --print` with .claude/commands/councilreview.md as system prompt
  3. Parse output for verdict line
  4. If APPROVE / APPROVE WITH CONDITIONS — exit 0
  5. If REQUEST CHANGES / BLOCK — print findings, exit non-zero (caller decides next step)

Used by Phase 10 of the production-launch plan.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COUNCIL_PROMPT = ROOT / ".claude" / "commands" / "councilreview.md"
DEFAULT_MODEL = "claude-sonnet-5"  # higher quality for review than Haiku


def git_diff(base: str = "HEAD~1", head: str = "HEAD") -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "diff", f"{base}...{head}"],
            capture_output=True, text=True, timeout=30,
        )
        return out.stdout
    except Exception as e:
        return f"# git diff failed: {e}\n"


def run_council(model: str = DEFAULT_MODEL, timeout_s: int = 300) -> dict:
    if not COUNCIL_PROMPT.exists():
        raise SystemExit(f"missing {COUNCIL_PROMPT}")
    system_prompt = COUNCIL_PROMPT.read_text(encoding="utf-8")
    diff = git_diff("HEAD~3", "HEAD")
    if not diff.strip():
        diff = "# (no diff vs HEAD~3)\n"

    user_msg = (
        "Run the council review on the following git diff. "
        "Follow the §3 finding format and the §4 council summary format exactly. "
        "Return your full report in markdown.\n\n"
        "```diff\n"
        + diff[:50000]
        + "\n```\n"
    )

    # Both prompts go via tempfile (system) + stdin (user) to avoid argv-length
    # hangs we hit in initial Claude-CLI testing.
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as sp_file:
        sp_file.write(system_prompt)
        sp_path = sp_file.name
    try:
        cmd = [
            "/usr/bin/claude",
            "--print",
            "--model", model,
            "--append-system-prompt-file", sp_path,
            "--disable-slash-commands",
            "--max-turns", "1",
            "--output-format", "json",
            "--exclude-dynamic-system-prompt-sections",
        ]
        proc = subprocess.run(
            cmd,
            input=user_msg,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        if proc.returncode != 0:
            raise SystemExit(
                f"claude CLI exit {proc.returncode}: stderr={proc.stderr[:500]!r}"
            )
        cli_data = json.loads(proc.stdout)
        if cli_data.get("is_error"):
            raise SystemExit(f"claude is_error: {cli_data.get('result', '')[:300]}")
        return {
            "report": cli_data.get("result", ""),
            "usage": cli_data.get("usage", {}),
            "cost_usd": cli_data.get("total_cost_usd", 0.0),
        }
    finally:
        try:
            import os as _os
            _os.unlink(sp_path)
        except OSError:
            pass


VERDICT_RE = re.compile(
    r"^\s*Decision:\s*(APPROVE|APPROVE WITH CONDITIONS|REQUEST CHANGES|BLOCK)\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def parse_verdict(report: str) -> str:
    m = VERDICT_RE.search(report)
    if m:
        return m.group(1).upper()
    # Fallback: look for last APPROVE/BLOCK token
    for word in ["BLOCK", "REQUEST CHANGES", "APPROVE WITH CONDITIONS", "APPROVE"]:
        if word.lower() in report.lower():
            return word
    return "UNKNOWN"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--timeout-s", type=int, default=300)
    ap.add_argument("--out", type=Path, default=ROOT / "logs" / "councilreview.md")
    args = ap.parse_args(argv)

    try:
        result = run_council(args.model, args.timeout_s)
    except SystemExit:
        raise
    except Exception as e:
        print(f"[council] error: {e}", file=sys.stderr)
        return 2

    verdict = parse_verdict(result["report"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(result["report"], encoding="utf-8")
    print(f"[council] verdict: {verdict}")
    print(f"[council] report saved to {args.out}")
    print(f"[council] cost (display): ${result['cost_usd']:.4f}")

    # Exit codes:
    #   0 = APPROVE / APPROVE WITH CONDITIONS — safe to ship
    #   1 = REQUEST CHANGES — fix and re-run
    #   2 = BLOCK — must fix, do not ship
    #   3 = UNKNOWN parse — surface to operator
    if verdict in {"APPROVE", "APPROVE WITH CONDITIONS"}:
        return 0
    if verdict == "REQUEST CHANGES":
        return 1
    if verdict == "BLOCK":
        return 2
    return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
