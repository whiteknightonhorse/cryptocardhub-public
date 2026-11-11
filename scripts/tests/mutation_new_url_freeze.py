"""Mutation proof for CCH-SEO-30: run `python scripts/tests/mutation_new_url_freeze.py`.
Each mutation must turn the NAMED test red; the file is restored afterwards."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / "video-generator/venv/bin/python"
MUTS = [
    ("faq-children.py", "    if flags.new_urls_frozen():  # DEFY-SEO-1110 B8: even with force=True\n",
     "    if False:  # DEFY-SEO-1110 B8: even with force=True\n", "test_open_next_cohort_refused_even_forced"),
    ("news-ingest.py", "    if new_urls_frozen():  # DEFY-SEO-1110 B8: before any fetch / model call\n",
     "    if False:  # DEFY-SEO-1110 B8: before any fetch / model call\n", "test_news_ingest_skips_before_any_fetch"),
    ("pseo-generate.py", "    if new_urls_frozen() and not target.exists():\n",
     "    if False and not target.exists():\n", "test_write_page_new_path_raises_existing_path_writes"),
    ("weekly-report.py", "        L.append(f\"  FREEZE: new URLs frozen since",
     "        pass; _unused = (f\"  FREEZE: new URLs frozen since", "test_weekly_report_before_review_has_line_and_no_action"),
]
bad = 0
for fname, old, new, test in MUTS:
    p = ROOT / "scripts" / fname
    src = p.read_text(encoding="utf-8")
    assert src.count(old) == 1, (fname, old)
    p.write_text(src.replace(old, new), encoding="utf-8")
    try:
        r = subprocess.run([str(PY), "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider",
                            str(ROOT / f"scripts/tests/test_new_url_freeze.py::{test}")],
                           capture_output=True, text=True, cwd=ROOT)
    finally:
        p.write_text(src, encoding="utf-8")
    red = r.returncode != 0
    print(f"{'RED ' if red else 'GREEN(BAD)'} {fname}: {test}")
    bad += not red
sys.exit(bad)
