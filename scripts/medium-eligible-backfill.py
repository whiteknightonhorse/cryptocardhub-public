#!/usr/bin/env python3
"""medium-eligible-backfill.py — one-off G37b pass over the existing corpus (CCH-MEDIUM-01).

Runs style-lint.py over every seo-site/src/content/pseo/*.md and sets ONE frontmatter line,
`medium_eligible: true|false`. Nothing else in any file changes (no rewrite, no re-emit).
Usage: medium-eligible-backfill.py [--write]   (default: dry run, prints counts per bucket)
Edit ceiling: refuses to write more files than exist in the corpus.
"""
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import humanize_gate as hg  # noqa: E402

CORPUS = hg.CORPUS


def verdict(path: Path) -> tuple[str, bool]:
    m = re.match(r"---\n(.*?)\n---\n(.*)", path.read_text(encoding="utf-8"), re.S)
    fm = yaml.safe_load(m.group(1)) or {}
    fm["body_markdown"] = m.group(2)
    return fm.get("bucket", "?"), hg.style_lint(fm)["green"]


def set_field(text: str, green: bool) -> str:
    line = f"medium_eligible: {'true' if green else 'false'}"
    if re.search(r"^medium_eligible:.*$", text, re.M):
        return re.sub(r"^medium_eligible:.*$", line, text, count=1, flags=re.M)
    head, sep, rest = text.partition("\n---\n")   # first closing delimiter only
    return head + "\n" + line + sep + rest


def main(write: bool) -> int:
    files = sorted(CORPUS.glob("*.md"))
    counts: Counter = Counter()
    changed = 0
    for p in files:
        bucket, green = verdict(p)
        counts[(bucket, green)] += 1
        new = set_field(p.read_text(encoding="utf-8"), green)
        if new != p.read_text(encoding="utf-8"):
            changed += 1
            if write:
                if changed > len(files):
                    raise SystemExit("edit ceiling exceeded")
                p.write_text(new, encoding="utf-8")
    for b in sorted({b for b, _ in counts}):
        print(f"{b:10s} GREEN {counts[(b, True)]:4d}  RED {counts[(b, False)]:4d}")
    print(f"total GREEN {sum(v for (b, g), v in counts.items() if g)} RED "
          f"{sum(v for (b, g), v in counts.items() if not g)}; files changed: {changed}"
          f"{'' if write else ' (dry run)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main("--write" in sys.argv))
