"""keyword-positions.py (CCH-KW-05): a measurement that failed is "unmeasured", never 0."""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
spec = importlib.util.spec_from_file_location("keyword_positions", ROOT / "scripts/keyword-positions.py")
kp = importlib.util.module_from_spec(spec)
sys.modules["keyword_positions"] = kp
spec.loader.exec_module(kp)


class Throttled(Exception):
    pass


class FakeGsc:
    def __init__(self, rows=None, boom=False):
        self.rows, self.boom = rows or [], boom

    def searchanalytics(self):
        return self

    def query(self, siteUrl, body):
        assert body["dimensions"] == ["query", "page"]
        assert body["dimensionFilterGroups"][0]["filters"][0]["operator"] == "equals"
        return self

    def execute(self):
        if self.boom:
            raise RuntimeError("quota")
        return {"rows": self.rows}


PAGE = "/reviews/coinbase-card-review/"
ROW = [{"keyword": "coinbase debit card", "path": PAGE, "role": "secondary"}]


def _bing(rows):
    return lambda q: rows


def test_snapshot_shape():
    gsc = FakeGsc([{"keys": ["coinbase debit card", "https://defycard.com" + PAGE],
                    "clicks": 0, "impressions": 3, "position": 8.04}])
    snap = kp.snapshot(ROW, _bing([{"path": PAGE, "position": 9.0, "impressions": 1, "clicks": 0}]),
                       Throttled, gsc, "sc-domain:x", "https://defycard.com")
    r = snap["rows"][0]
    assert r["bing"] == {"position": 9.0, "impressions": 1, "clicks": 0}
    assert r["gsc"] == {"clicks": 0, "impressions": 3, "position": 8.0}


def test_throttle_is_unmeasured_never_zero():
    def boom(q):
        raise Throttled()
    snap = kp.snapshot(ROW, boom, Throttled, FakeGsc(boom=True), "s", "https://defycard.com")
    assert snap["rows"][0]["bing"] == "unmeasured"
    assert snap["rows"][0]["gsc"] == "unmeasured"


def test_no_row_and_no_client_are_unmeasured():
    snap = kp.snapshot(ROW, _bing([]), Throttled, None, "s", "h")
    assert snap["rows"][0]["bing"] == snap["rows"][0]["gsc"] == "unmeasured"


def test_compare_renders_table():
    a = {"date": "2026-10-19", "rows": [{**ROW[0], "bing": "unmeasured", "gsc": "unmeasured"}]}
    b = {"date": "2026-11-16", "rows": [{**ROW[0], "bing": {"position": 6.0}, "gsc": {"impressions": 4}}]}
    out = kp.compare(a, b)
    assert "| coinbase debit card | /reviews/coinbase-card-review/ | unmeasured→6.0 | unmeasured→4 |" in out


def test_third_keyword_on_one_path_is_refused(tmp_path):
    f = tmp_path / "p.tsv"
    f.write_text("a\t/x/\tprimary\nb\t/x/\tsecondary\nc\t/x/\tcontained\n")
    with pytest.raises(SystemExit):
        kp.load_placements(f)


def test_real_placements_table_is_within_law():
    rows = kp.load_placements()
    assert len(rows) == 10
