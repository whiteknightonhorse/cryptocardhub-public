"""T-0142-13 — R6 candidate table: table parser rejects anything outside
{yes, no, not stated}, and every yes/no cell must carry a URL and a quote.

Y49: the law is a LITERAL here, not re-derived from the module under test.
Two mutations are proven red: a value outside the allowed set, and a
yes/no cell with the quote stripped out. Y53's lesson (verify arrived, not
merely exists) is applied at the end: the wiring into weekly-report.py's
_streams_lines() is exercised through the real render path, not just the
helper function in isolation.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import r6_candidates as r6  # noqa: E402

_ALLOWED = {"yes", "no", "not stated"}


def _spec_load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- the real, generated report --------------------------------------------

def test_real_report_has_three_cards_seven_jurisdictions_each():
    md_path, json_path = r6.write_outputs("2026-09-24")
    rows = r6.parse_table(md_path.read_text(encoding="utf-8"))
    # LITERAL: 3 candidate cards x 7 jurisdictions = 21 rows, no more, no less.
    assert len(rows) == 21
    cards = {row["card"] for row in rows}
    assert cards == {"RedotPay", "Bybit Card", "Wirex"}
    for row in rows:
        assert row["value"] in _ALLOWED


def test_every_yes_no_row_carries_a_url_and_a_quote():
    md_path, _ = r6.write_outputs("2026-09-24")
    rows = r6.parse_table(md_path.read_text(encoding="utf-8"))
    for row in rows:
        if row["value"] in ("yes", "no"):
            assert row["url"], row
            assert row["quote"], row


def test_summary_line_names_the_date_and_card_count():
    r6.write_outputs("2026-09-24")
    line = r6.summary_line()
    assert "2026-09-24" in line
    assert "3 cards" in line
    assert "logs/partners/r6-candidates-2026-09-24.md" in line


# --- mutations: the law must go red, not just pass by construction ---------

_GOOD_ROW = ('| India | yes | https://example.com/x | "some quote" | '
             '2026-09-24 |')

_TABLE_HEADER = (
    "## Example Card\n\n"
    "| Jurisdiction | Value | URL | Quote | Fetched |\n"
    "|---|---|---|---|---|\n"
)


def test_mutation_value_outside_allowed_set_is_red():
    bad = _TABLE_HEADER + (
        '| India | maybe | https://example.com/x | "some quote" | '
        '2026-09-24 |\n')
    with pytest.raises(ValueError, match="not in"):
        r6.parse_table(bad)


def test_mutation_yes_without_quote_is_red():
    bad = _TABLE_HEADER + (
        '| India | yes | https://example.com/x |  | 2026-09-24 |\n')
    with pytest.raises(ValueError, match="no quote"):
        r6.parse_table(bad)


def test_mutation_yes_without_url_is_red():
    bad = _TABLE_HEADER + (
        '| India | yes |  | "some quote" | 2026-09-24 |\n')
    with pytest.raises(ValueError, match="no URL"):
        r6.parse_table(bad)


def test_not_stated_row_needs_neither_url_nor_quote():
    ok = _TABLE_HEADER + '| India | not stated |  |  | 2026-09-24 |\n'
    rows = r6.parse_table(ok)
    assert rows[0]["value"] == "not stated"


def test_validate_catches_a_stripped_url_in_memory():
    import copy
    mutated = copy.deepcopy(r6.CANDIDATES)
    mutated[0]["availability"]["India"]["url"] = ""
    with pytest.raises(ValueError, match="no url"):
        r6.validate(mutated)


# --- wiring: the real weekly-report.py render path must print the line -----

def test_streams_lines_prints_r6_candidates_through_the_real_module():
    r6.write_outputs("2026-09-24")
    wr = _spec_load("weekly_report", ROOT / "scripts" / "weekly-report.py")
    lines = wr._streams_lines()
    joined = "\n".join(lines)
    assert "R6 candidates" in joined
    assert "2026-09-24: 3 cards" in joined
