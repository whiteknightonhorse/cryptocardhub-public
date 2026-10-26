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
import json
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

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
    cfg = yaml.safe_load((ROOT / "config" / "rates.yaml").read_text(encoding="utf-8"))
    promos = cfg.get("promos") or []
    assert promos, "no promos block — the structural rule has no subject"
    for pr in promos:
        assert pr.get("expires"), f"promo {pr.get('name')!r} has no expires: date"
        assert pr.get("status"), f"promo {pr.get('name')!r} has no status"


# ---------------------------------------------------------------------------
# E-10: the "business" facts that live in the article's prose — lock period
# and minimum claim — plus `--accept` writing them into rates.yaml.
# ---------------------------------------------------------------------------

PROSE = ("...cashback is converted to ETHFI and enters a 7-day lock period. "
         "A minimum of $5 USD in cashback is required to claim.")


def test_parse_lock_and_minimum_reads_the_prose_facts():
    assert ac.parse_lock_and_minimum(PROSE) == {"lock_days": 7, "min_claim_usd": 5}


def test_parse_lock_and_minimum_is_silent_when_the_wording_is_gone():
    """A missing fact must read as 'not found', never as 0 — a checker that
    defaulted to zero would report a real lock/minimum as having vanished."""
    assert ac.parse_lock_and_minimum("no prose here") == {}


def test_compare_rates_flags_missing_business_facts_as_one_grouped_line():
    expected = {"levels": {"core": EXPECTED["levels"]["core"]},
                "lock_days": None, "min_claim_usd": None}
    v = ac.compare_rates(expected, _article(ROWS) + PROSE)
    assert v["status"] == "differs"
    assert "business: lock_days None->7, min_claim_usd None->5" in v["summary"]
    assert v["apply"] == {"lock_days": 7, "min_claim_usd": 5}


def test_compare_rates_is_quiet_once_the_facts_already_agree():
    expected = {"levels": {"core": EXPECTED["levels"]["core"]},
                "lock_days": 7, "min_claim_usd": 5}
    v = ac.compare_rates(expected, _article(ROWS) + PROSE)
    assert v["status"] == "match"
    assert "lock/minimum unchanged" in v["summary"]
    assert "apply" not in v


def test_diffs_are_never_truncated():
    """The old `diffs[:8]` cap silently dropped the LAST of nine real
    per-band mismatches (and any 'business' line after it) — indistinguishable
    from 'only eight things changed'. Every row must reach the summary."""
    rows = [(f"band {i}", "3%", "3%") for i in range(9)]
    expected = {"levels": {"core": [{"band": b, "usd": u, "eur": e}
                                     for b, u, e in rows]}}
    moved = [(b, "9%", e) for b, u, e in rows]  # every row's rate moved
    v = ac.compare_rates(expected, _article(moved))
    assert v["summary"].count("core row") == 9, v["summary"]


# The literal pre-E-10 config, as committed before T-0134 accepted the
# issuer's "(USD equivalent, not €)" wording and the lock/minimum facts
# (see `git show 6e00b69 -- config/rates.yaml`). A LITERAL, not the live
# config: E-10/T-0134 already synced config/rates.yaml to this same fixture,
# so reading the live file here would compare the article to itself and this
# test would go green for the wrong reason (Y49 — the law must not move with
# the file it polices).
PRE_E10_RATES_CFG = {
    "levels": {
        "core": [
            {"band": "USD: First $2,000 / EUR: First $800", "usd": "3%", "eur": "3%"},
            {"band": "USD: $2,001 – $3,000 / EUR: $801 – $1,500", "usd": "1%", "eur": "1%"},
            {"band": "USD: Above $3,000 / EUR: Above $1,500", "usd": "0.5%", "eur": "0.1%"},
        ],
        "luxe": [
            {"band": "USD: First $10,000 / EUR: First $3,000", "usd": "3%", "eur": "3%"},
            {"band": "USD: $10,001 – $20,000 / EUR: $3,001 – $6,000", "usd": "1%", "eur": "1%"},
            {"band": "USD: Above $20,000 / EUR: Above $6,000", "usd": "0.5%", "eur": "0.1%"},
        ],
        "pinnacle": [
            {"band": "USD: First $50,000 / EUR: First $16,000", "usd": "3%", "eur": "3%"},
            {"band": "USD: $50,001 – $80,000 / EUR: $16,001 – $26,000", "usd": "1%", "eur": "1%"},
            {"band": "USD: Above $80,000 / EUR: Above $26,000", "usd": "0.5%", "eur": "0.1%"},
        ],
        "business": [{"band": "All spend", "usd": "1%", "eur": "0%"}],
    },
    "lock_days": None,
    "min_claim_usd": None,
}


def test_the_saved_fixture_diffs_on_exactly_the_nine_bands_plus_business():
    """The literal acceptance of E-10: on the article as saved 2026-09-14,
    compared against the config AS IT WAS BEFORE E-10 accepted the issuer's
    wording, the diff is the 9 band rows missing the '(USD equivalent, not €)'
    wording (core/luxe/pinnacle, 3 each — the `business` LEVEL's own row
    already matches) plus one grouped `business:` line for
    lock_days/min_claim_usd, which that config had never had verified."""
    html = (ROOT / "scripts" / "tests" / "fixtures" /
            "etherfi-262374-2026-09-14.html").read_text(encoding="utf-8")
    v = ac.compare_rates(PRE_E10_RATES_CFG, html)
    assert v["status"] == "differs"
    parts = v["summary"].split("; ")
    band_diffs = [p for p in parts
                  if p.startswith(("core row", "luxe row", "pinnacle row"))]
    business_diffs = [p for p in parts if p.startswith("business:")]
    assert len(parts) == 10, parts
    assert len(band_diffs) == 9, parts
    assert business_diffs == ["business: lock_days None->7, min_claim_usd None->5"], parts
    assert v["apply"] == {"lock_days": 7, "min_claim_usd": 5}


def test_the_live_rates_config_now_matches_the_09_14_capture():
    """T-0134 accepted exactly this diff into config/rates.yaml — the live
    file must now read as a match against the same captured article."""
    cfg = yaml.safe_load((ROOT / "config" / "rates.yaml").read_text(encoding="utf-8"))
    html = (ROOT / "scripts" / "tests" / "fixtures" /
            "etherfi-262374-2026-09-14.html").read_text(encoding="utf-8")
    v = ac.compare_rates(cfg, html)
    assert v["status"] == "match", v["summary"]


RATES_FIXTURE = """\
source:
  url: "https://example.test/rates"
  title: "test fixture"
  parser: intercom_tables
  verified: "2020-01-01"

levels:
  core:
    - band: "First $2,000"
      usd: "3%"
      eur: "3%"

lock_days: null
min_claim_usd: null
"""


def _rates_alert(tmp_path, monkeypatch, rows):
    apath = tmp_path / "availability-alerts.json"
    apath.write_text(json.dumps(rows), encoding="utf-8")
    rpath = tmp_path / "rates.yaml"
    rpath.write_text(RATES_FIXTURE, encoding="utf-8")
    monkeypatch.setattr(ac, "ALERTS", apath)
    monkeypatch.setattr(ac, "RATES_YAML", rpath)
    monkeypatch.setattr(ac, "JOURNAL", tmp_path / "fact-journal.jsonl")
    return apath, rpath


RATES_ALERT = {"id": "dddddddddddd", "card": "rates", "status": "differs",
               "summary": "business: lock_days None->7, min_claim_usd None->5",
               "source": "x", "apply": {"lock_days": 7, "min_claim_usd": 5}}


def test_accept_writes_the_scalar_rates_fields_and_bumps_verified(tmp_path, monkeypatch):
    _, rpath = _rates_alert(tmp_path, monkeypatch, [RATES_ALERT])
    assert ac.accept("dddddddddddd") == 0
    cfg = yaml.safe_load(rpath.read_text(encoding="utf-8"))
    assert cfg["lock_days"] == 7
    assert cfg["min_claim_usd"] == 5
    assert cfg["source"]["verified"] == date.today().isoformat()
    assert cfg["levels"]["core"][0]["band"] == "First $2,000"   # untouched


def test_accept_of_rates_removes_the_alert(tmp_path, monkeypatch):
    apath, _ = _rates_alert(tmp_path, monkeypatch, [RATES_ALERT])
    ac.accept("dddddddddddd")
    assert not apath.exists()   # the only alert — file removed, not left empty


def test_replace_scalar_refuses_to_guess_at_an_unknown_field():
    with pytest.raises(KeyError):
        ac.replace_scalar(RATES_FIXTURE, "nonexistent_field", 1)
