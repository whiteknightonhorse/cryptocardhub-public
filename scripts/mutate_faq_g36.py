#!/usr/bin/env python3
"""Mutation proof for CCH-FAQ-G36-0929: re-break each edit, expect red, restore."""
import subprocess, sys
from pathlib import Path
R = Path(__file__).resolve().parent
PG, FC = R / "pseo-generate.py", R / "faq-children.py"
T = "scripts/tests/test_faq_g36_allowlist.py"
MUTS = [
 ("edit1: re-add 0.3 % bullet", PG,
  '- A cashback or commission claim with no number',
  '- Verified and safe to state: referral commission **0.1 %-0.3 %** by tier; **12-month** window.\n- A cashback or commission claim with no number'),
 ("edit2: G36 retry ignores writer_sha", FC,
  'and refusal.get("writer_sha") != writer_sha)', 'and True)'),
 ("edit2b: CLI select() without writer_sha", FC,
  'cohort = select(a.cohort, _writer_sha())', 'cohort = select(a.cohort)'),
 ("edit3: breaker never trips", PG,
  'if a["g36_streak"] >= FAQ_G36_BREAKER:', 'if False:'),
 ("edit5: drop $40 premise", FC,
  'r"\\$40"', 'r"NEVERMATCHES40"'),
]
bad = 0
for name, f, old, new in MUTS:
    src = f.read_text(); assert src.count(old) == 1, name
    f.write_text(src.replace(old, new))
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", T, "-q", "-x"], cwd=R.parent,
                           capture_output=True, text=True)
    finally:
        f.write_text(src)
    red = r.returncode != 0
    print(f"{'RED (good)' if red else 'GREEN (FAKE GATE)'}  {name}  "
          f"{[l for l in r.stdout.splitlines() if l.startswith('FAILED')][:1]}")
    bad += not red
sys.exit(bad)
