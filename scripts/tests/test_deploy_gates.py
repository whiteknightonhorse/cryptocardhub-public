"""E-03 — the deploy gates exist, in order, and BEFORE the site changes hands.

Two separate laws, because the first one alone was already satisfied on
2026-09-14 and the money bug still had a clear path to production:

  LAW 1  deploy.sh runs four pytest gates: G20 -> G34 -> G12 -> G37.
  LAW 2  all four run BEFORE `mv -Tf "$WEB_NEW" "$WEB"`.

LAW 2 is the one that was broken. The 2b/2c gates sat *after* the atomic
renames — their own comment said "a red gate here means no rsync" while the
rsync had already happened two steps earlier, and the `exit` left the bad copy
serving with no rollback. A gate whose failure cannot change what is served is
a log line wearing a gate's name.

THE LAWS ARE LITERALS HERE (Y49/Y54): the expected filenames and their order
are typed out below, not derived from the script under test. A test that reads
its own expectation out of deploy.sh moves with every mutation and can never
go red.
"""
from __future__ import annotations

import re
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "scripts" / "deploy.sh"

# LAW 1 — the four gate test files, in the order deploy.sh must run them.
EXPECTED_GATES = [
    "tests/test_internal_links.py",   # 2b — G20 dead internal links
    "tests/test_rendered_output.py",  # 2c — G34 raw markup reaching the reader
    "tests/test_partner_links.py",    # 2d — G12 a card the reader cannot be issued
    "tests/test_url_retirement.py",   # 2e — G37 a remembered URL that would become a bare 404
]

# LAW 2 — the line that hands the new copy to nginx. Every gate precedes it.
HANDOVER = 'mv -Tf "$WEB_NEW" "$WEB"'


def _lines() -> list[str]:
    return DEPLOY.read_text(encoding="utf-8").splitlines()


def _gate_line_numbers() -> list[tuple[int, str]]:
    """(line number, gate file) for every `pytest <file>` invocation, in file order."""
    found = []
    for i, line in enumerate(_lines()):
        if "-m pytest" not in line:
            continue
        m = re.search(r"tests/test_[a-z_]+\.py", line)
        if m:
            found.append((i, m.group(0)))
    return found


def test_deploy_runs_the_four_gates_in_order():
    """LAW 1 — mutation: delete the 2d block and this goes red on the list."""
    assert [g for _, g in _gate_line_numbers()] == EXPECTED_GATES


def test_every_gate_runs_before_the_site_changes_hands():
    """LAW 2 — mutation: move any gate below the rename and this goes red."""
    lines = _lines()
    handover = [i for i, line in enumerate(lines) if HANDOVER in line]
    assert handover, f"{HANDOVER!r} not found — deploy.sh no longer hands over this way"
    first_handover = min(handover)
    for lineno, gate in _gate_line_numbers():
        assert lineno < first_handover, (
            f"{gate} runs at line {lineno + 1}, after the live root is replaced at "
            f"line {first_handover + 1} — its failure cannot stop the deploy"
        )


def test_each_gate_failure_aborts_the_script():
    """A gate that logs and continues is not a gate: each must exit non-zero."""
    text = DEPLOY.read_text(encoding="utf-8")
    for gate in EXPECTED_GATES:
        assert gate in text, f"{gate} is not invoked by deploy.sh at all"
        block = text.split(gate, 1)[1].split("\n\n", 1)[0]
        assert re.search(r"^\s*exit [1-9]", block, re.M), (
            f"{gate} is invoked but its failure branch does not exit"
        )


def test_smoke_has_the_four_new_sections():
    """CCH-SEO-12: sections 9-12 exist in verify-deploy.sh, in order."""
    text = (DEPLOY.parent / "verify-deploy.sh").read_text(encoding="utf-8")
    headers = [
        "=== 9. www -> apex 301 ===",
        "=== 10. Retirement sample (redirects.map) ===",
        "=== 11. One sitemap (sitemap.xml == sitemap-index.xml) ===",
        "=== 12. Visible breadcrumb on a sampled article ===",
    ]
    pos = [text.find(h) for h in headers]
    assert all(p >= 0 for p in pos), f"missing section header(s): {pos}"
    assert pos == sorted(pos), "sections 9-12 are out of order"
