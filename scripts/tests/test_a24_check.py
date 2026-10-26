"""E-14 — a24-check.py: A2.4 hypothesis check, read-only, no verdict.

Y49's rule, applied: every expected number below is worked out by hand and
written as a literal, never re-derived by calling the function under test —
otherwise a mutation to that function would move both sides of the
assertion and the gate would stay green through the exact bug it exists to
catch.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


a24 = _load("a24_check", "scripts/a24-check.py")

CANDIDATES_MD = """# Keyword candidates — measured demand, not yet bank rows

- fetched: `2026-09-14T06:03:53.757509+00:00`
- queries returned by Bing (unique, summed across weekly rows): **2**
- new-page candidates: 0
- update candidates (a page of ours already ranks): 2

## New-page candidates

_No candidate cleared the filters. That is an answer, not a failure:_

## Update candidates

| query | impressions | clicks | ranked page | page impressions | page clicks | avg position |
|---|---|---|---|---|---|---|
| uphold debit card | 8 | 2 | `/reviews/uphold-card-review/` | 4 | 0 | 4 |
| uphold credit card | 8 | 0 | `/reviews/uphold-card-review/` | 4 | 0 | 7 |
"""


# --- _bing_week(): both real Bing date shapes -------------------------------

def test_bing_week_parses_plain_iso_date():
    # 2026-08-24 is a Monday, ISO week 35 (worked out by hand).
    assert a24._bing_week("2026-08-24") == "2026-W35"


def test_bing_week_parses_wcf_epoch_form():
    # /Date(1786924800000)/ is the WCF epoch form (bing-keywords.py's
    # GetQueryPageStats docstring confirms this shape occurs live).
    # 1786924800000 ms = 2026-08-17 00:00:00 UTC = Monday, ISO week 34 —
    # verified by hand with `datetime.fromtimestamp(...).isocalendar()`,
    # not by calling the function under test. (ruling-1: the previous
    # literal here, 1787270400000, is 2026-08-21 — a Friday, not the
    # Monday the comment claimed; it happened to land in the same ISO
    # week so the assertion passed for the wrong reason.)
    assert a24._bing_week("/Date(1786924800000)/") == "2026-W34"


def test_bing_week_returns_none_on_garbage():
    assert a24._bing_week("not-a-date") is None
    assert a24._bing_week("") is None


# --- ranked_pages(): reuses promote-candidates.py's parser, doesn't re-type it

def test_ranked_pages_groups_queries_by_page(tmp_path):
    p = tmp_path / "CANDIDATES.md"
    p.write_text(CANDIDATES_MD, encoding="utf-8")
    out = a24.ranked_pages(p)
    assert out == {"/reviews/uphold-card-review/": ["uphold debit card", "uphold credit card"]}


# --- correlate(): the N >= MIN_PAGES gate, literal boundary on both sides ---

def test_correlate_below_floor_is_underpowered_string():
    pairs = [(float(i), float(i)) for i in range(7)]  # N=7, perfectly correlated data
    out = a24.correlate(pairs)
    assert out == {"n": 7, "r": "underpowered"}


def test_correlate_at_floor_computes_a_real_number():
    # N=8, perfect positive correlation by construction -> r must be 1.0,
    # not the "underpowered" string a mutation weakening the N>=8 gate to
    # N>=3 would produce for the N=7 case above.
    pairs = [(float(i), float(i) * 2) for i in range(8)]
    out = a24.correlate(pairs)
    assert out == {"n": 8, "r": 1.0}


def test_correlate_constant_series_is_not_a_lying_number():
    pairs = [(1.0, 5.0)] * 8  # every x identical, every y identical
    out = a24.correlate(pairs)
    assert out["n"] == 8
    assert out["r"] is None


# --- bing_weekly(): missing key -> instrument_dead, never a zero -----------

def test_bing_weekly_instrument_dead_without_key(monkeypatch):
    monkeypatch.delenv("BING_WEBMASTER_API_KEY", raising=False)
    by_query, status = a24.bing_weekly({"uphold debit card"})
    assert by_query == {}
    assert status["status"] == "instrument_dead"


def test_bing_weekly_aggregates_only_tracked_queries_by_week(monkeypatch):
    monkeypatch.setenv("BING_WEBMASTER_API_KEY", "dummy")
    rows = [
        {"Query": "uphold debit card", "Impressions": 5, "Date": "2026-08-24"},
        {"Query": "uphold debit card", "Impressions": 3, "Date": "2026-08-31"},
        {"Query": "some untracked query", "Impressions": 999, "Date": "2026-08-24"},
    ]
    monkeypatch.setattr(a24.bk, "_get", lambda method, **p: rows)
    by_query, status = a24.bing_weekly({"uphold debit card"})
    assert status == {"status": "ok"}
    assert by_query == {"uphold debit card": {"2026-W35": 5, "2026-W36": 3}}


# --- verified_weekly(): a missing week aggregate is `partial`, not a zero --

FAKE_HISTORY = {
    "2026-W34": {"verified_by_page": {"/reviews/uphold-card-review/": 4}},
    "2026-W35": {"verified_by_page": {"/reviews/uphold-card-review/": 7}},
    "2026-W36": {"verified_by_page": {}},
    # 2026-W37 deliberately absent
}


def test_verified_weekly_reads_history_and_flags_missing_week(monkeypatch):
    monkeypatch.setattr(a24.aeo, "load_week", lambda w: FAKE_HISTORY.get(w))
    out, status = a24.verified_weekly({"/reviews/uphold-card-review/"})
    assert out["/reviews/uphold-card-review/"] == {
        "2026-W34": 4, "2026-W35": 7, "2026-W36": 0}
    assert status == {"status": "partial", "missing_weeks": ["2026-W37"]}


# --- ga4_weekly(): isolated failure never crashes the whole run -----------

def test_ga4_weekly_instrument_dead_when_load_fails(monkeypatch):
    def boom(name, relpath):
        raise KeyError("GA4_PROPERTY_ID")
    monkeypatch.setattr(a24, "_load", boom)
    out, status = a24.ga4_weekly({"/reviews/uphold-card-review/"})
    assert out == {}
    assert status["status"] == "instrument_dead"


class _FakeWR:
    """One `_run_report` call PER WEEK (dims=["landingPage"] only, since/until
    = that week's Monday/Sunday) — the shape ruling-1 required in place of
    the unverified `isoYear`/`isoWeek` dimension pair (see `ga4_weekly`'s
    docstring). Week bounds worked out by hand with
    `date.fromisocalendar(2026, w, 1/7)`, not re-derived from the code under
    test: W34 = 08-17..08-23, W35 = 08-24..08-30, W36 = 08-31..09-06,
    W37 = 09-07..09-13.

    ruling-2: the W35 row deliberately has NO trailing slash — the confirmed
    LIVE shape (logs/aeo/cron-report.log: "/learn/how-do-crypto-debit-cards-work",
    "/reviews/gnosis-pay-card-review", neither carries one) — while the W36
    row keeps a trailing slash + query string, so both real shapes are
    exercised against the CANDIDATES.md path "/reviews/uphold-card-review/",
    which always has one."""

    ROWS_BY_SINCE = {
        "2026-08-24": [  # W35
            (["/reviews/uphold-card-review"], ["3"]),  # no trailing slash — live shape
            (["/some/other/page"], ["9"]),  # not in our path set
        ],
        "2026-08-31": [  # W36
            (["/reviews/uphold-card-review/?utm_source=chatgpt.com"], ["2"]),
        ],
    }

    @staticmethod
    def _ga4_client():
        return object()

    @staticmethod
    def _run_report(client, dims, mets, since, until, dim_filter=None, limit=200):
        assert dims == ["landingPage"]
        assert mets == ["sessions"]
        assert dim_filter == "CHATGPT_FILTER"
        return _FakeWR.ROWS_BY_SINCE.get(since, [])


def test_ga4_weekly_buckets_by_week_strips_query_string_and_trailing_slash(monkeypatch):
    def fake_load(name, relpath):
        assert relpath == "scripts/weekly-report.py"
        return _FakeWR()
    monkeypatch.setattr(a24, "_load", fake_load)
    monkeypatch.setattr(a24, "_chatgpt_filter", lambda: "CHATGPT_FILTER")
    out, status = a24.ga4_weekly({"/reviews/uphold-card-review/"})
    assert status == {"status": "ok"}
    # W34/W37 genuinely had 0 sessions (a week that answered, not a failure) —
    # present as literal 0, not absent. W35's 3 only lands here if the
    # no-trailing-slash live shape is normalized against the ranked path's
    # trailing slash (ruling-2); W36's 2 proves the query-string strip still
    # works on top of that.
    assert out == {"/reviews/uphold-card-review/": {
        "2026-W34": 0, "2026-W35": 3, "2026-W36": 2, "2026-W37": 0}}


def test_ga4_weekly_normalizes_trailing_slash_against_candidates_path(monkeypatch):
    """ruling-2 on E-14: live GA4 `landingPage` values have NO trailing slash
    (logs/aeo/cron-report.log: "/learn/how-do-crypto-debit-cards-work",
    "/reviews/gnosis-pay-card-review") while CANDIDATES.md's ranked paths DO
    ("/reviews/uphold-card-review/"). A strict `path in out` comparison
    silently drops every real GA4 row and reports a fake `status: ok` with a
    guessed zero for every page. This pins the fix directly, isolated from
    the query-string-stripping behavior tested above."""
    class WR:
        _ga4_client = staticmethod(lambda: object())

        @staticmethod
        def _run_report(client, dims, mets, since, until, dim_filter=None, limit=200):
            if since == "2026-08-24":  # W35
                return [(["/reviews/uphold-card-review"], ["5"])]  # no trailing slash
            return []
    monkeypatch.setattr(a24, "_load", lambda name, relpath: WR())
    monkeypatch.setattr(a24, "_chatgpt_filter", lambda: "CHATGPT_FILTER")
    out, status = a24.ga4_weekly({"/reviews/uphold-card-review/"})
    assert status == {"status": "ok"}
    assert out["/reviews/uphold-card-review/"]["2026-W35"] == 5


def test_ga4_weekly_partial_when_some_weeks_fail(monkeypatch):
    def flaky_run_report(client, dims, mets, since, until, dim_filter=None, limit=200):
        if since in ("2026-08-17", "2026-09-07"):  # W34, W37
            raise RuntimeError("500")
        return _FakeWR.ROWS_BY_SINCE.get(since, [])
    monkeypatch.setattr(a24, "_load",
                        lambda name, relpath: type("WR", (), {
                            "_ga4_client": staticmethod(lambda: object()),
                            "_run_report": staticmethod(flaky_run_report)})())
    monkeypatch.setattr(a24, "_chatgpt_filter", lambda: "CHATGPT_FILTER")
    out, status = a24.ga4_weekly({"/reviews/uphold-card-review/"})
    assert status == {"status": "partial", "missing_weeks": ["2026-W34", "2026-W37"]}
    # the two weeks that DID answer are still real numbers, not dropped
    assert out["/reviews/uphold-card-review/"] == {"2026-W35": 3, "2026-W36": 2}


def test_ga4_weekly_instrument_dead_when_every_week_fails(monkeypatch):
    def always_fails(client, dims, mets, since, until, dim_filter=None, limit=200):
        raise RuntimeError("503")
    monkeypatch.setattr(a24, "_load",
                        lambda name, relpath: type("WR", (), {
                            "_ga4_client": staticmethod(lambda: object()),
                            "_run_report": staticmethod(always_fails)})())
    out, status = a24.ga4_weekly({"/reviews/uphold-card-review/"})
    assert out == {}
    assert status["status"] == "instrument_dead"


# --- build_report(): end-to-end wiring, one JSON's worth of numbers --------

def test_build_report_wires_all_three_instruments(tmp_path, monkeypatch):
    p = tmp_path / "CANDIDATES.md"
    p.write_text(CANDIDATES_MD, encoding="utf-8")
    monkeypatch.setenv("BING_WEBMASTER_API_KEY", "dummy")
    bing_rows = [
        {"Query": "uphold debit card", "Impressions": 10, "Date": "2026-08-24"},
        {"Query": "uphold credit card", "Impressions": 6, "Date": "2026-08-24"},
    ]
    monkeypatch.setattr(a24.bk, "_get", lambda method, **kw: bing_rows)
    monkeypatch.setattr(a24.aeo, "load_week", lambda w: FAKE_HISTORY.get(w))

    def dead_ga4(name, relpath):
        raise RuntimeError("no GA4 in this test")
    monkeypatch.setattr(a24, "_load", dead_ga4)

    report = a24.build_report(p)
    assert report["ranked_pages_n"] == 1
    assert report["instruments"]["bing_get_query_stats"] == {"status": "ok"}
    assert report["instruments"]["ga4_landing_chatgpt"]["status"] == "instrument_dead"
    w35 = report["by_week"]["2026-W35"]
    page = w35["pages"][0]
    assert page["bing_impressions"] == 16          # 10 + 6, summed at page level
    assert page["verified_by_page"] == 7
    assert page["ga4_chatgpt_sessions"] is None    # GA4 leg is dead, never a guessed 0
    # only 1 ranked page in this fixture: correlation is underpowered by construction
    assert w35["correlation_bing_vs_verified"]["r"] == "underpowered"
    assert w35["correlation_bing_vs_verified"]["n"] == 1


# --- main(): writes exactly one file, nothing else -------------------------

def test_main_writes_exactly_one_json_file(tmp_path, monkeypatch):
    p = tmp_path / "CANDIDATES.md"
    p.write_text(CANDIDATES_MD, encoding="utf-8")
    monkeypatch.setattr(a24.pc, "CANDIDATES", p)
    monkeypatch.setattr(a24, "OUT_DIR", tmp_path / "aeo")
    monkeypatch.delenv("BING_WEBMASTER_API_KEY", raising=False)
    monkeypatch.setattr(a24.aeo, "load_week", lambda w: None)
    monkeypatch.setattr(a24, "_load", lambda name, relpath: (_ for _ in ()).throw(RuntimeError("dead")))

    before = set((tmp_path / "aeo").glob("**/*")) if (tmp_path / "aeo").exists() else set()
    rc = a24.main()
    # T-0142-04: bing + ga4 are dead in this fixture (see instruments asserted
    # below) — a dead instrument must fail loudly, rc=1, never a quiet 0.
    assert rc == 1
    after = set((tmp_path / "aeo").glob("**/*"))
    new_files = after - before
    assert len(new_files) == 1
    out_path = new_files.pop()
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["instruments"]["bing_get_query_stats"]["status"] == "instrument_dead"
    assert data["instruments"]["aeo_verified_by_page"]["status"] == "partial"
    assert data["instruments"]["ga4_landing_chatgpt"]["status"] == "instrument_dead"
    # ruling-1 M2: pins that main() re-reads `pc.CANDIDATES` at call time
    # (`build_report(pc.CANDIDATES)`) instead of `build_report()`'s own
    # frozen default. The fixture above has exactly 1 ranked page; a
    # mutation dropping the explicit argument would silently fall back to
    # build_report()'s default, frozen at import time to the REAL
    # production CANDIDATES.md — a different page count — and this is the
    # only assertion in this test that would catch it.
    assert data["ranked_pages_n"] == 1


def test_main_rc_zero_when_all_three_instruments_are_ok(tmp_path, monkeypatch):
    """T-0142-04's own acceptance literal: rc=1 only when an instrument is
    dead — a run where all three read `ok` must exit 0. Mirrors the fixture
    already used for `test_build_report_wires_all_three_instruments` /
    `test_ga4_weekly_buckets_by_week_strips_query_string_and_trailing_slash`,
    wired through `main()` instead of the individual legs."""
    p = tmp_path / "CANDIDATES.md"
    p.write_text(CANDIDATES_MD, encoding="utf-8")
    monkeypatch.setattr(a24.pc, "CANDIDATES", p)
    monkeypatch.setattr(a24, "OUT_DIR", tmp_path / "aeo")
    monkeypatch.setenv("BING_WEBMASTER_API_KEY", "dummy")
    bing_rows = [
        {"Query": "uphold debit card", "Impressions": 10, "Date": "2026-08-24"},
        {"Query": "uphold credit card", "Impressions": 6, "Date": "2026-08-24"},
    ]
    monkeypatch.setattr(a24.bk, "_get", lambda method, **kw: bing_rows)
    monkeypatch.setattr(a24.aeo, "load_week", lambda w: FAKE_HISTORY.get(w))
    monkeypatch.setattr(a24, "_load", lambda name, relpath: _FakeWR())
    monkeypatch.setattr(a24, "_chatgpt_filter", lambda: "CHATGPT_FILTER")

    rc = a24.main()
    assert rc == 0
    out_path = tmp_path / "aeo" / f"a24-check-{a24.date.today().isoformat()}.json"
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["instruments"] == {
        "bing_get_query_stats": {"status": "ok"},
        "aeo_verified_by_page": {"status": "partial", "missing_weeks": ["2026-W37"]},
        "ga4_landing_chatgpt": {"status": "ok"},
    }


def test_main_rc_one_when_an_instrument_is_dead(tmp_path, monkeypatch):
    """Y49 literal-mutation pair for the rc=1 law: same fixture as the `ok`
    test above but with the Bing key absent, so `bing_get_query_stats`
    reports `instrument_dead` — rc must flip to 1. A mutation dropping the
    `any(... == "instrument_dead" ...)` check from `main()` (i.e. `return 0`
    unconditionally) makes exactly this assertion fail; verified by hand."""
    p = tmp_path / "CANDIDATES.md"
    p.write_text(CANDIDATES_MD, encoding="utf-8")
    monkeypatch.setattr(a24.pc, "CANDIDATES", p)
    monkeypatch.setattr(a24, "OUT_DIR", tmp_path / "aeo")
    monkeypatch.delenv("BING_WEBMASTER_API_KEY", raising=False)
    monkeypatch.setattr(a24.aeo, "load_week", lambda w: FAKE_HISTORY.get(w))
    monkeypatch.setattr(a24, "_load", lambda name, relpath: _FakeWR())
    monkeypatch.setattr(a24, "_chatgpt_filter", lambda: "CHATGPT_FILTER")

    rc = a24.main()
    assert rc == 1


# --- acceptance literal: 0 model calls ------------------------------------

def test_zero_model_calls():
    """E-14's own acceptance line: '0 вызовов модели'. A literal source scan
    for the two shapes a model call would actually take here — importing the
    SDK or constructing its client — not a mock-call-count: there is no
    Anthropic/Claude client to construct in the first place, and this pins
    that it stays that way. (Prose mentioning "Anthropic" in a docstring is
    fine; `import anthropic` or `Anthropic(` is not.)"""
    src = (ROOT / "scripts" / "a24-check.py").read_text(encoding="utf-8")
    assert "import anthropic" not in src
    assert "Anthropic(" not in src
