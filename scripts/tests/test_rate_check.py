"""G36 — the rate checker, and the three outcomes it is allowed to have.

An expired promo was published as a current property of the card on 307 pages
because nothing compared our numbers to the issuer's. The country list had such
a comparison; the rates did not. This is the rates half, and it inherits the
law that the availability check already obeys:

    tables match      -> bump `verified`. The date then means a check happened.
    tables differ     -> alert, change NOTHING. A machine may notice that a
                         financial figure has moved; it may not decide the new
                         value.
    unreachable /
    unparseable       -> alert, change NOTHING, do NOT bump. "I could not read
                         the page" recorded as "nothing changed" is the failure
                         the visible `verified` date exists to prevent.

The third outcome is the one worth a test. The first two announce themselves.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ac = _load("availability_check", "scripts/availability-check.py")

# One level's table as Intercom ships it: three header cells, then triples.
def _article(rows, level="core"):
    cells = ['{"type":"heading","text":"%s"}' % level]
    for text in ("\\u003cb\\u003eMonthly Spend\\u003c/b\\u003e",
                 "\\u003cb\\u003eStandard Cashback Rate\\u003c/b\\u003e",
                 "\\u003cb\\u003eEUR Cashback Rate\\u003c/b\\u003e"):
        cells.append('{"type":"paragraph","text":"%s","class":"no-margin"}' % text)
    for band, usd, eur in rows:
        for text in (band, usd, eur):
            cells.append('{"type":"paragraph","text":"%s","class":"no-margin"}' % text)
    return "[" + ",".join(cells) + "]"


ROWS = [("USD: First $2,000 / EUR: First $800", "3%", "3%"),
        ("USD: Above $3,000 / EUR: Above $1,500", "0.5%", "0.1%")]
EXPECTED = {"levels": {"core": [
    {"band": "USD: First $2,000 / EUR: First $800", "usd": "3%", "eur": "3%"},
    {"band": "USD: Above $3,000 / EUR: Above $1,500", "usd": "0.5%", "eur": "0.1%"},
]}}


def test_a_matching_table_is_a_match():
    v = ac.compare_rates(EXPECTED, _article(ROWS))
    assert v["status"] == "match", v


def test_a_changed_rate_is_reported_not_absorbed():
    """The value of the whole instrument: it must SEE a moved figure. A checker
    that only ever says 'match' has no discriminating power and its green is
    worth nothing."""
    moved = [(ROWS[0][0], "2%", "3%"), ROWS[1]]
    v = ac.compare_rates(EXPECTED, _article(moved))
    assert v["status"] == "differs"
    assert "ours" in v["summary"] and "issuer" in v["summary"]
    assert "2%" in v["summary"] and "3%" in v["summary"]


def test_a_changed_threshold_is_reported_too():
    """A band boundary moving from $2,000 to $1,500 is a rate cut in disguise.
    Bands are compared verbatim precisely so that this cannot pass."""
    moved = [("USD: First $1,500 / EUR: First $800", "3%", "3%"), ROWS[1]]
    v = ac.compare_rates(EXPECTED, _article(moved))
    assert v["status"] == "differs"
    assert "1,500" in v["summary"]


def test_a_new_level_on_the_issuers_page_is_reported():
    html = _article(ROWS) + _article([("All spend", "1%", "0%")], level="business")
    v = ac.compare_rates(EXPECTED, html)
    assert v["status"] == "differs"
    assert "business" in v["summary"]


def test_an_article_that_changed_shape_is_unparseable_not_a_match():
    """The dangerous failure: the page is reachable, the parse yields nothing,
    and an empty comparison reads as 'identical'."""
    v = ac.compare_rates(EXPECTED, '[{"type":"heading","text":"core"}]')
    assert v["status"] == "unparseable"


def test_a_table_that_lost_its_header_is_not_read_as_rows():
    """Without the header check, the first data row would be eaten as headers
    and every subsequent triple would be shifted by one cell — producing a
    confident, wrong comparison instead of a refusal."""
    bad = _article(ROWS).replace("Monthly Spend", "Something Else")
    v = ac.compare_rates(EXPECTED, bad)
    assert v["status"] == "unparseable"


def test_the_config_carries_an_expiry_for_every_promo():
    """A promo row with no `expires:` cannot go stale — it has nowhere to fall
    out of date into. That is exactly how 'up to 15 % on food' survived four
    months past its own end, on 307 pages."""
    import yaml
    cfg = yaml.safe_load((ROOT / "config" / "rates.yaml").read_text(encoding="utf-8"))
    promos = cfg.get("promos") or []
    assert promos, "no promos block — the structural rule has no subject"
    for pr in promos:
        assert pr.get("expires"), f"promo {pr.get('name')!r} has no expires: date"
        assert pr.get("status"), f"promo {pr.get('name')!r} has no status"
