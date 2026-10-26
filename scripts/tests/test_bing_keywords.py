"""bing-keywords.py — Fable 2026-09-14 (FABLE_DEFYCARD_ANSWER.md, A4 step 1).

THE BUG THIS PINS: `GetQueryStats` returns one row per (query, week), not one
row per query. On 2026-09-07 the live CANDIDATES.md reported "3 candidates"
that were the SAME query in three weekly rows (82/56/48 impressions). The law:
collect() must group by normalize(query) and SUM before any threshold is
applied — a mutation that removes the aggregation must turn this file red.
"""
from __future__ import annotations

import importlib.util
import io
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bk = _load("bing_keywords", "scripts/bing-keywords.py")


# --- normalize / jaccard -----------------------------------------------------

def test_normalize_collapses_punctuation_and_case():
    assert bk.normalize("Crypto Rebates vs Cashback: Which Is Better?") == \
        bk.normalize("crypto rebates vs cashback which is better")


def test_jaccard_identical_sets_is_one():
    assert bk.jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_disjoint_sets_is_zero():
    assert bk.jaccard({"a"}, {"b"}) == 0.0


def test_nearest_duplicate_flags_a_near_rewrite():
    have = {"coinbase-card-review", "binance-card-review"}
    # 3 of 4 tokens shared with coinbase-card-review
    assert bk.nearest_duplicate("coinbase-card", have) == "coinbase-card-review"


def test_nearest_duplicate_is_none_for_a_genuinely_distinct_slug():
    have = {"coinbase-card-review", "binance-card-review"}
    assert bk.nearest_duplicate("gnosis-pay-fees-explained", have) is None


# --- collect(): the query x week aggregation bug, fixed ---------------------

def test_collect_sums_query_x_week_rows_instead_of_treating_them_as_candidates(monkeypatch):
    """The exact live defect (2026-09-07): one query, three weekly rows,
    82 + 56 + 48 = 186. Must become ONE candidate with impressions=186,
    weeks_seen=3 — never three."""
    rows = [
        {"Query": "binance card usage for everyday purchases", "Impressions": 82, "Clicks": 2, "Date": "2026-08-24"},
        {"Query": "binance card usage for everyday purchases", "Impressions": 56, "Clicks": 1, "Date": "2026-08-31"},
        {"Query": "binance card usage for everyday purchases", "Impressions": 48, "Clicks": 1, "Date": "2026-09-07"},
    ]
    monkeypatch.setattr(bk, "_get", lambda method, **p: rows if method == "GetQueryStats" else [])
    monkeypatch.setattr(bk, "existing_slugs", lambda: set())
    monkeypatch.setattr(bk, "query_page_stats", lambda q: [])   # nothing of ours ranks
    data = bk.collect()
    assert data["queries_returned"] == 1          # ONE query, not three rows
    all_cands = data["new_candidates"] + data["update_candidates"]
    assert len(all_cands) == 1
    assert all_cands[0]["impressions"] == 186
    assert all_cands[0]["weeks_seen"] == 3


def test_collect_routes_an_already_ranked_query_to_update_not_new(monkeypatch):
    """A2.3: if Bing already shows one of OUR pages for this query, it is a
    task to strengthen that page, never a new-page candidate — printing a new
    page here is exactly how 5 of 6 promotions on 2026-09-01 became
    near-duplicates of pages that already ranked."""
    rows = [
        {"Query": "binance card usage for everyday purchases", "Impressions": 82, "Clicks": 2, "Date": "2026-08-24"},
        {"Query": "binance card usage for everyday purchases", "Impressions": 133, "Clicks": 2, "Date": "2026-08-31"},
    ]
    monkeypatch.setattr(bk, "_get", lambda method, **p: rows if method == "GetQueryStats" else [])
    monkeypatch.setattr(bk, "existing_slugs", lambda: set())
    monkeypatch.setattr(bk, "query_page_stats",
                        lambda q: [{"path": "/reviews/binance-card-review/", "impressions": 171,
                                    "clicks": 0, "position": 6.0}])
    data = bk.collect()
    assert data["new_candidates"] == []
    assert len(data["update_candidates"]) == 1
    assert data["update_candidates"][0]["ranked_by"][0]["path"] == "/reviews/binance-card-review/"


def test_collect_refuses_a_single_week_query_even_above_the_impression_floor(monkeypatch):
    rows = [{"Query": "best crypto card fresh phrase people typed once", "Impressions": 50, "Clicks": 0, "Date": "2026-09-07"}]
    monkeypatch.setattr(bk, "_get", lambda method, **p: rows if method == "GetQueryStats" else [])
    monkeypatch.setattr(bk, "existing_slugs", lambda: set())
    monkeypatch.setattr(bk, "query_page_stats", lambda q: [])
    data = bk.collect()
    assert data["new_candidates"] == []
    assert data["update_candidates"] == []
    assert data["single_week"] == 1


def test_collect_refuses_below_floor_even_across_two_weeks(monkeypatch):
    rows = [
        {"Query": "crypto card low signal query", "Impressions": 2, "Clicks": 0, "Date": "2026-08-31"},
        {"Query": "crypto card low signal query", "Impressions": 3, "Clicks": 0, "Date": "2026-09-07"},
    ]
    monkeypatch.setattr(bk, "_get", lambda method, **p: rows if method == "GetQueryStats" else [])
    monkeypatch.setattr(bk, "existing_slugs", lambda: set())
    monkeypatch.setattr(bk, "query_page_stats", lambda q: [])
    data = bk.collect()
    assert data["new_candidates"] == []
    assert data["below_floor"] == 1


def test_collect_refuses_a_near_duplicate_slug_even_with_strong_measured_demand(monkeypatch):
    rows = [
        {"Query": "coinbase card fees", "Impressions": 40, "Clicks": 1, "Date": "2026-08-31"},
        {"Query": "coinbase card fees", "Impressions": 40, "Clicks": 1, "Date": "2026-09-07"},
    ]
    monkeypatch.setattr(bk, "_get", lambda method, **p: rows if method == "GetQueryStats" else [])
    monkeypatch.setattr(bk, "existing_slugs", lambda: {"coinbase-card-review"})
    monkeypatch.setattr(bk, "query_page_stats", lambda q: [])
    data = bk.collect()
    assert data["new_candidates"] == []
    assert data["near_duplicate"] == 1


def test_collect_a_query_that_clears_every_filter_becomes_a_new_candidate(monkeypatch):
    rows = [
        {"Query": "crypto rewards digital gift cards subscription services compliance",
         "Impressions": 20, "Clicks": 0, "Date": "2026-08-31"},
        {"Query": "crypto rewards digital gift cards subscription services compliance",
         "Impressions": 12, "Clicks": 0, "Date": "2026-09-07"},
    ]
    monkeypatch.setattr(bk, "_get", lambda method, **p: rows if method == "GetQueryStats" else [])
    monkeypatch.setattr(bk, "existing_slugs", lambda: set())
    monkeypatch.setattr(bk, "query_page_stats", lambda q: [])
    data = bk.collect()
    assert len(data["new_candidates"]) == 1
    assert data["new_candidates"][0]["impressions"] == 32
    assert data["new_candidates"][0]["weeks_seen"] == 2


# --- query_page_stats(): the real Bing response shape, not a guess ---------

def test_query_page_stats_parses_the_real_bing_response_shape(monkeypatch):
    """THE BUG THIS PINS (2026-09-14 first live run): GetQueryPageStats puts
    the ranking PAGE'S URL in the response's `Query` field — confirmed by a
    live call. The old code read `Page`/`Url`, which are always absent, so
    every result came back empty and the Update channel this step exists to
    build stayed permanently empty even when the HTTP call succeeded. This is
    the EXACT shape a live call returns, not a convenient fixture."""
    live_shape = [{
        "__type": "QueryStats:#Microsoft.Bing.Webmaster.Api",
        "AvgClickPosition": -1, "AvgImpressionPosition": 5, "Clicks": 1,
        "Date": "/Date(1787270400000)/", "Impressions": 51,
        "Query": "https://www.defycard.com/reviews/binance-card-review/",
    }]
    monkeypatch.setattr(bk, "_get", lambda method, **p: live_shape)
    monkeypatch.setattr(bk.time, "sleep", lambda s: None)
    out = bk.query_page_stats("binance card usage for everyday purchases")
    assert len(out) == 1
    assert out[0]["path"] == "/reviews/binance-card-review/"
    assert out[0]["impressions"] == 51
    assert out[0]["clicks"] == 1


# --- _get(): ThrottleHost retried, never silently swallowed -----------------

def _http_error(body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", 400, "Bad Request", {}, io.BytesIO(body))


def test_get_retries_on_throttlehost_then_succeeds(monkeypatch):
    calls = {"n": 0}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"d": [{"ok": true}]}'

    def fake_urlopen(url, timeout=60):
        calls["n"] += 1
        if calls["n"] < 3:
            # a fresh HTTPError each time — its BytesIO body is one-shot, like a real response
            raise _http_error(b'{"ErrorCode":5,"Message":"ERROR!!! ThrottleHost"}')
        return FakeResp()

    monkeypatch.setenv("BING_WEBMASTER_API_KEY", "dummy")
    monkeypatch.setattr(bk.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(bk.time, "sleep", lambda s: None)
    result = bk._get("GetQueryPageStats", query="uphold card")
    assert result == [{"ok": True}]
    assert calls["n"] == 3


def test_get_raises_measurement_failed_after_exhausting_retries_on_throttlehost(monkeypatch):
    """THE BUG THIS PINS (2026-09-14 first live run): an uncaught HTTPError
    (ThrottleHost, ErrorCode 5) from a single query killed the entire weekly
    run — CANDIDATES.md and the funnel log were never written at all. A
    measurement failure must become a typed, catchable exception, never a
    crash of the whole batch."""
    def always_throttled(url, timeout=60):
        raise _http_error(b'{"ErrorCode":5,"Message":"ERROR!!! ThrottleHost"}')

    monkeypatch.setenv("BING_WEBMASTER_API_KEY", "dummy")
    monkeypatch.setattr(bk.urllib.request, "urlopen", always_throttled)
    monkeypatch.setattr(bk.time, "sleep", lambda s: None)
    with pytest.raises(bk.BingMeasurementFailed):
        bk._get("GetQueryPageStats", query="uphold card")


def test_get_does_not_retry_a_non_throttle_error(monkeypatch):
    other = _http_error(b'{"ErrorCode":1,"Message":"bad apikey"}')
    calls = {"n": 0}

    def fail_once(url, timeout=60):
        calls["n"] += 1
        raise other

    monkeypatch.setenv("BING_WEBMASTER_API_KEY", "dummy")
    monkeypatch.setattr(bk.urllib.request, "urlopen", fail_once)
    monkeypatch.setattr(bk.time, "sleep", lambda s: None)
    with pytest.raises(bk.BingMeasurementFailed):
        bk._get("GetQueryPageStats", query="uphold card")
    assert calls["n"] == 1   # no retry budget spent on a non-throttle error


# --- collect(): a measurement failure is neither ranked nor unranked -------

def test_collect_never_classifies_a_measurement_failure_as_new_or_update(monkeypatch):
    """THE BUG THIS PINS (A2.3, one layer down): if query_page_stats() cannot
    be measured, the query must NOT fall through to new_candidates or
    near_duplicate as if Bing had confirmed no page of ours ranks — that is
    indistinguishable from the exact defect this step was built to close."""
    rows = [
        {"Query": "uphold card", "Impressions": 40, "Clicks": 1, "Date": "2026-08-31"},
        {"Query": "uphold card", "Impressions": 40, "Clicks": 1, "Date": "2026-09-07"},
    ]

    def fail(q):
        raise bk.BingMeasurementFailed("ThrottleHost")

    monkeypatch.setattr(bk, "_get", lambda method, **p: rows if method == "GetQueryStats" else [])
    monkeypatch.setattr(bk, "existing_slugs", lambda: set())
    monkeypatch.setattr(bk, "query_page_stats", fail)
    data = bk.collect()
    assert data["new_candidates"] == []
    assert data["update_candidates"] == []
    assert data["measurement_failed"] == 1


# --- render(): two blocks, New-page rows keep the pinned 5-column shape -----

def test_render_writes_two_separate_headed_blocks():
    data = {
        "fetched_at": "2026-09-14T05:40:00+00:00", "queries_returned": 2,
        "covered": 0, "off_intent": 0, "near_duplicate": 0, "below_floor": 0,
        "single_week": 0, "measurement_failed": 0, "window_end": "2026-09-14",
        "new_candidates": [{"query": "a new query", "impressions": 15, "clicks": 0, "slug": "a-new-query", "weeks_seen": 2}],
        "update_candidates": [{"query": "an old query", "impressions": 50, "clicks": 1, "slug": "an-old-query",
                               "weeks_seen": 2, "ranked_by": [{"path": "/reviews/x/", "impressions": 40, "clicks": 0, "position": 5.0}]}],
    }
    out = bk.render(data)
    assert "## New-page candidates" in out
    assert "## Update candidates" in out
    new_idx = out.index("## New-page candidates")
    upd_idx = out.index("## Update candidates")
    assert new_idx < out.index("a new query") < upd_idx
    assert upd_idx < out.index("an old query")
    # the New-page row still has the pinned 5-column shape (backtick slug column)
    assert "`a-new-query`" in out
