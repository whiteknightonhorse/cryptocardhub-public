"""CCH-SEO-26 — quick-win lists. Expected values are LITERALS worked out by hand
from the live capture scripts/tests/fixtures/quick-wins/ (Y49): nothing here
calls the function under test to compute its own expectation.

Windows for today=2026-11-10: current 2026-10-13..11-09 (Bing weekly rows
10-16, 10-23, 10-30, 11-06), previous 2026-09-15..10-12 (09-18, 09-25, 10-02, 10-09).

By hand from the captured GetQueryStats rows (imp/clicks/avg-position per week):
  "coinbase card"  cur 6/0/9 + 1/0/10 + 3/1/8 + 3/1/7 = 13 imp, 2 clk,
                   pos (54+10+24+21)/13 = 8.4 ; prev 2 + 6 + 15 = 23 imp
                   -> striking (8.4, 13>=10) and falling (13 <= 0.6*23 = 13.8)
  "binance card"   cur 21+5+12+13 = 51 imp, 0 clk, pos 7.0 ; prev 11+16+13 = 40
                   -> low-CTR and no-clicks; NOT striking (7.0 < 8.0); NOT falling
  "best cryptocurrency debit cards"
                   cur 10/0/8 + 4/1/10 = 14 imp, 1 clk, pos (80+40)/14 = 8.6 ; prev 9
                   -> striking only
"""
from __future__ import annotations

import importlib.util
import json
import os
import urllib.parse
from datetime import date
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures" / "quick-wins"


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, HERE.parent / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


os.environ.setdefault("GA4_PROPERTY_ID", "0")  # weekly-report reads it at import
qw = _load("quick_wins", "quick-wins.py")
wr = _load("weekly_report_qw", "weekly-report.py")
bk = _load("bing_keywords_t", "bing-keywords.py")

TODAY = date(2026, 11, 10)
PICK = {"coinbase card", "binance card", "best cryptocurrency debit cards"}


def _bing_slice():
    rows = json.loads((FIX / "bing-GetQueryStats-2026-11-10.json").read_text())["d"]
    return [r for r in rows if r["Query"].lower().strip() in PICK]


def _lists():
    cur, prev = qw.windows(TODAY)
    return qw.build_lists(qw.bing_aggregate(_bing_slice(), cur, prev, bk.normalize))


def _q(lists, name):
    return {r["query"]: r for r in lists[name]}


def test_windows_are_28_full_days_ending_yesterday():
    cur, prev = qw.windows(TODAY)
    assert (cur, prev) == ((date(2026, 10, 13), date(2026, 11, 9)),
                           (date(2026, 9, 15), date(2026, 10, 12)))


def test_captured_shape_is_what_the_parser_reads():
    rows = json.loads((FIX / "bing-GetQueryStats-2026-11-10.json").read_text())["d"]
    assert {"Query", "Date", "Impressions", "Clicks", "AvgImpressionPosition"} <= rows[0].keys()
    assert qw.bing_date(rows[0]["Date"]) == date(2026, 8, 21)


def test_per_query_sum_before_thresholds():
    c = _q(_lists(), "no_clicks")["binance card"]
    assert (c["impressions"], c["clicks"], c["position"]) == (51, 0, 7.0)


def test_striking_distance_literal():
    s = _q(_lists(), "striking_distance")
    assert set(s) == {"coinbase card", "best cryptocurrency debit cards"}
    assert (s["coinbase card"]["impressions"], s["coinbase card"]["clicks"],
            s["coinbase card"]["position"]) == (13, 2, 8.4)
    assert s["best cryptocurrency debit cards"]["position"] == 8.6


def test_low_ctr_and_no_clicks_and_falling_literals():
    lists = _lists()
    assert set(_q(lists, "low_ctr")) == {"binance card"}
    assert set(_q(lists, "no_clicks")) == {"binance card"}
    fall = _q(lists, "falling")
    assert set(fall) == {"coinbase card"} and fall["coinbase card"]["prev_impressions"] == 23


def test_noise_queries_are_dropped():
    cur, prev = qw.windows(TODAY)
    row = {"Query": "site:defycard.com x", "Date": "/Date(1792108800000)/",
           "Impressions": 99, "Clicks": 0, "AvgImpressionPosition": 9}
    row2 = dict(row, Query="what is defycard")
    assert qw.bing_aggregate([row, row2], cur, prev, bk.normalize) == []


def test_gsc_entries_use_query_page_keys_and_fall_into_lists():
    cap = json.loads((FIX / "gsc-searchanalytics-2026-11-10.json").read_text())
    entries = qw.gsc_entries(cap["current"].get("rows", []), cap["previous"].get("rows", []))
    assert all(e["engine"] == "gsc" and e["page"].startswith("https://defycard.com") for e in entries)
    # hand-built row pair: 100 imp -> 10 imp is a fall, 0 clicks, pos 12.0 is striking only at >=10 imp
    e = qw.gsc_entries([{"keys": ["q a", "https://defycard.com/p/"], "clicks": 0,
                         "impressions": 10, "position": 12.0}],
                       [{"keys": ["q a", "https://defycard.com/p/"], "clicks": 0,
                         "impressions": 100, "position": 12.0}])
    lists = qw.build_lists(e)
    assert [len(lists[k]) for k in qw.LISTS] == [1, 0, 1, 0]


def test_index_state_reads_google_verdict():
    st = {"urls": {"https://defycard.com/a/": {"verdict": "PASS", "coverage": "x"},
                   "https://defycard.com/b/": {"verdict": "NEUTRAL", "coverage": "Crawled - currently not indexed"}}}
    assert qw.index_state("https://www.defycard.com/a/", st) == "indexed"
    assert qw.index_state("/b/", st) == "not indexed (Crawled - currently not indexed)"
    assert qw.index_state("/c/", st) == "not inspected"


def test_link_count_empty_capture_prints_na_never_zero(monkeypatch):
    cap = json.loads((FIX / "bing-GetLinkCounts-2026-11-10.json").read_text())["d"]
    monkeypatch.setattr(qw, "_load", lambda *a: type("B", (), {"_get": staticmethod(lambda *a, **k: cap)}))
    out = qw.bing_inbound_links()
    assert out.startswith("n/a (") and "0 from" not in out


# --- report text at the transmit boundary -----------------------------------

HEADINGS = ["QUICK WIN 1 — striking distance", "QUICK WIN 2 — low CTR",
            "QUICK WIN 3 — falling", "QUICK WIN 4 — impressions without clicks"]


def _sent_text(report, monkeypatch):
    bodies = []
    monkeypatch.setattr(wr.urllib.request, "urlopen",
                        lambda url, data=None, timeout=None: bodies.append(data) or type(
                            "R", (), {"read": lambda s: b""})())
    monkeypatch.setenv("TG_BOT_TOKEN", "t")
    monkeypatch.setenv("TG_CHAT_ID", "c")
    wr.send(report, wr.render(report))
    return "\n".join(urllib.parse.unquote_plus(b.decode()) for b in bodies)


def _report(qwd):
    return {"since": "2026-11-03", "until": "2026-11-10", "human_sessions": 1, "raw_sessions": 1,
            "bot_direct": 0, "by_source": [], "clicks_by_source": {}, "clicks_by_partner": {},
            "utm_content": {}, "videos_7d": 0,
            "median_views_7d": wr.Missing("median_views_7d", "x"),
            "retention_7d": wr.Missing("retention_7d", "x"), "quick_wins": qwd}


def _full():
    lists = _lists()
    for rows in lists.values():
        for r in rows:
            r["index"] = "indexed"
    return {"date": "2026-11-10", "engines": {"bing": {"status": "ok"}, "gsc": {"status": "ok"}},
            "bing_inbound_links": "n/a (cause)", "lists": lists}


def test_sent_report_contains_four_headings_and_rows(monkeypatch):
    text = _sent_text(_report(_full()), monkeypatch)
    for h in HEADINGS:
        assert h in text
    assert "bing | coinbase card | n/a | 13 imp (was 23) / 2 clk / pos 8.4 | indexed" in text
    assert "bing inbound links: n/a (cause)" in text


def test_empty_list_says_none_this_week(monkeypatch):
    d = _full()
    d["lists"]["low_ctr"] = []
    text = _sent_text(_report(d), monkeypatch)
    assert "QUICK WIN 2 — low CTR (imp >= 50, pos <= 10, CTR < 1%)\n    none this week" in text


def test_failed_engine_heads_the_report(monkeypatch, tmp_path):
    (tmp_path / "logs" / "seo").mkdir(parents=True)
    d = _full()
    d["date"] = date.today().isoformat()
    d["engines"]["bing"] = {"status": "failed", "why": "BingMeasurementFailed: ThrottleHost"}
    (tmp_path / "logs" / "seo" / "quick-wins-x.json").write_text(json.dumps(d))
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    report = _report(None)
    report.update(wr._collect_quick_wins())
    text = _sent_text(report, monkeypatch)
    assert "ACTION REQUIRED" in text
    assert "quick_wins.bing: BingMeasurementFailed: ThrottleHost" in text
