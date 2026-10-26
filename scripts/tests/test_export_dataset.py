"""Gate for scripts/export-dataset.py. Expected values are literals (Y49)."""
import csv
import datetime as dt
import importlib.util
import io
import json
import shutil
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("export_dataset", SCRIPTS / "export-dataset.py")
ed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ed)

TODAY = dt.date(2026, 10, 19)
HEADER = ("issuer,card,fact_id,class,applies_to,value,unit,status,verified_at,expires,"
          "source_url,source_quote,confidence")


def _fact(fid, **kw):
    f = {"id": fid, "value": 3, "unit": "%", "applies_to": {}, "source_url": "https://x.example/a",
         "source_quote": "q", "verified_at": dt.date(2026, 10, 1), "expires": None,
         "status": "current", "class": "rate", "confidence": "issuer_help"}
    f.update(kw)
    return f


@pytest.fixture
def facts_dir(tmp_path):
    d = tmp_path / "facts"
    d.mkdir()
    rows = [
        _fact("t.ok"),
        _fact("t.disputed", status="disputed", value=30000, unit="USD/day"),
        _fact("t.expired", status="expired", value=15, unit="%"),
        _fact("t.pastdue", class_="promo", value=15, unit="%", expires=dt.date(2026, 2, 28)),
    ]
    (d / "etherfi.yaml").write_text(yaml.safe_dump({"facts": rows}), encoding="utf-8")
    return d


def test_status_and_expiry_filter(facts_dir):
    ids = [r["fact_id"] for r in ed.fact_rows(TODAY, facts_dir)]
    assert ids == ["t.ok"]


def test_no_15_percent_cashback_in_real_export():
    rows = ed.fact_rows(TODAY)
    assert rows, "real export is empty"
    assert not [r for r in rows if r["value"] == "15" and r["unit"] == "%" and r["class"] in ("cashback", "rate", "promo")]
    assert not [r for r in rows if r["status"] not in ("current", "beta")]


def test_header_and_required_fields():
    files = ed.build(TODAY)
    assert files["cards.csv"].splitlines()[0] == HEADER
    assert files["availability.csv"].splitlines()[0] == "card,jurisdiction,kind,available,verified,source_url"
    for r in csv.DictReader(io.StringIO(files["cards.csv"])):
        assert r["source_url"] and r["verified_at"], r["fact_id"]
        assert len(r["source_quote"]) <= 200
    assert len(json.loads(files["cards.json"])) == len(files["cards.csv"].splitlines()) - 1


def test_availability_allowlist_only():
    rows = ed.availability_rows()
    assert rows and all(r["available"] == "true" and r["kind"] in ("country", "us_state") for r in rows)
    assert {"card": "ether.fi Cash", "jurisdiction": "Germany"}.items() <= next(
        r for r in rows if r["jurisdiction"] == "Germany" and r["card"] == "ether.fi Cash").items()
    assert not [r for r in rows if r["jurisdiction"] in ("Belgium", "Ireland", "India")]
    assert not [r for r in rows if r["card"] == "Crypto.com Visa"]  # no `verified` => absent


def test_no_referral_urls():
    blob = "".join(ed.build(TODAY).values())
    for bad in ("@defycard", "?ref=", "ref=", "AFFILIATE_URL", "ether.fi/affiliates"):
        assert bad not in blob


def test_readme_literals():
    readme = ed.build(TODAY)["README.md"]
    assert "https://defycard.com/" in readme and "CC BY 4.0" in readme
    assert "https://defycard.com/reviews/ether-fi-cash-review/" in readme


def test_check_unchanged_then_flipped(tmp_path, monkeypatch, facts_dir):
    monkeypatch.setattr(ed, "FACTS_DIR", facts_dir)
    out = tmp_path / "out"
    assert ed.main(["--write"], out, TODAY) == 0
    assert ed.main(["--check"], out, TODAY + dt.timedelta(days=1)) == 0  # only the date moved
    data = yaml.safe_load((facts_dir / "etherfi.yaml").read_text())
    data["facts"][0]["status"] = "disputed"
    (facts_dir / "etherfi.yaml").write_text(yaml.safe_dump(data))
    assert ed.main(["--check"], out, TODAY) == 1
    assert (out / "CHANGELOG.md").read_text().startswith("- 2026-10-19: 1 facts (+1)")
