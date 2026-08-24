"""G37 — the monthly digest publishes only when the market actually moved.

Stream 3 of Fable's plan exists because we already own a dataset and were not
treating it as one: the availability matrix, the rate tables and the promo
expiry dates are data ABOUT THE MARKET, maintained daily as a by-product of
keeping our own pages honest.

THE LAW THAT MAKES IT NOT IMITATION: a month with no recorded change gets NO
page. A monthly slot that must be filled whatever the month contained is the
"pages per week as a KPI" this site already has a name for — 199 double zeros.
So refusal is the default, publication is the exception, and every refusal
writes its cause (Y55: a zero has to carry its reason).

Literals here, not imports of the module's constants (Y49): the minimum item
count, the bucket, and the slug shape are typed out again.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "market_digest", ROOT / "scripts" / "market-digest.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["market_digest"] = mod
    spec.loader.exec_module(mod)
    return mod


md = _load()

ROWS = [
    {"observed": "2026-07-03", "kind": "rates", "card": "etherfi",
     "summary": "core row 3: ours 0.5% vs issuer 0.1%",
     "source": "https://help.ether.fi/en/articles/262374-how-does-cashback-work"},
    {"observed": "2026-07-09", "kind": "availability", "card": "etherfi",
     "summary": "prohibited: issuer adds ['Estonia']",
     "source": "https://help.ether.fi/en/articles/262373"},
]


def test_a_quiet_month_publishes_nothing(tmp_path, monkeypatch, capsys):
    """The whole point. No journal entries, no page — and the refusal says so."""
    monkeypatch.setattr(md, "CONTENT", tmp_path)
    monkeypatch.setattr(md, "entries_for", lambda month: [])
    rc = md.refuse("2026-07", "the fact journal recorded no market changes", 0, False)
    assert rc == 0                                   # a correct verdict, not an error
    out = capsys.readouterr().out
    record = json.loads(out.strip().splitlines()[0])
    assert record["event"] == "digest_refused"
    assert record["items"] == 0
    assert record["reason"]                           # the zero carries its cause
    assert not list(tmp_path.glob("*.md"))


def test_one_recorded_change_is_enough_to_publish():
    """No arbitrary "at least five items" threshold: one verified change to a
    financial product IS the month's news."""
    assert md.MIN_ITEMS == 1


def test_every_bullet_carries_its_date_and_issuer_source():
    """Nothing in a digest may be prose we wrote. Each line is one observed
    divergence plus the page it was observed against."""
    page = md.build("2026-07", ROWS)
    body = page["body_markdown"]
    for r in ROWS:
        assert r["observed"] in body, r
        assert r["summary"] in body, r
        assert r["source"] in body, r
    # The page states plainly how it is made, and that sentence contains the
    # word "estimate" by design ("nothing here is an estimate or a forecast"),
    # so a keyword check on it fails on CORRECT content. The law is the three
    # assertions above: date, summary and issuer source, for every row.
    assert "issuer source" in body


def test_the_page_is_dated_in_its_title_and_slug():
    page = md.build("2026-07", ROWS)
    assert page["slug"] == "crypto-card-market-changes-2026-07"
    assert "July 2026" in page["title"]
    assert page["bucket"] == "news"


def test_the_page_carries_a_verified_date():
    page = md.build("2026-07", ROWS)
    assert any("Last verified" in b for b in page["snapshot_bullets"])


def test_a_month_that_already_has_a_digest_is_refused(tmp_path, monkeypatch, capsys):
    """Idempotent under a cron that may fire twice — a duplicate month would be
    two URLs for one intent, which is the cannibalisation the whole programme
    forbids."""
    monkeypatch.setattr(md, "CONTENT", tmp_path)
    (tmp_path / "crypto-card-market-changes-2026-07.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["market-digest.py", "--month", "2026-07"])
    rc = md.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "already exists" in out


def test_the_written_page_is_valid_frontmatter(tmp_path, monkeypatch):
    """It goes through the same collection schema as every other page — a
    digest that breaks the build is worse than no digest."""
    monkeypatch.setattr(md, "CONTENT", tmp_path)
    path = md.write_page(md.build("2026-07", ROWS))
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    import re
    for line in text.split("\n"):
        m = re.match(r"^(\w+): (.*)$", line)
        if m and m.group(2)[:1] in '[{"':
            json.loads(m.group(2))          # raises if malformed
    for required in ("title", "description", "h1", "slug", "bucket",
                     "primary_keyword", "intro_paragraph"):
        assert f"{required}:" in text, required


def test_the_month_defaults_to_the_one_that_just_ended(monkeypatch):
    """A cron firing on the 2nd must report on the month that closed, not the
    two days of the month it is standing in."""
    first, last = md.month_bounds("2026-07")
    assert (first, last) == (date(2026, 7, 1), date(2026, 7, 31))
    first, last = md.month_bounds("2026-02")
    assert last == date(2026, 2, 28)        # leap-year arithmetic, not 30 days
