"""E-12 — GSC as the second demand instrument.

THE FIGURES ARE LITERALS HERE (Y49). Nothing below computes the expectation
by calling the function under test — they are copied by hand from
FABLE_DEFYCARD_PART2.md line 34, GSC's own live 28-day answer for
`sc-domain:defycard.com`, measured 2026-09-14: 211 impressions, 1 click,
average position 40.6, 89 of 873 pages with >=1 impression; inspecting the 8
most agent-fetched URLs that week found 7 "Submitted and indexed" and 1
"Discovered - currently not indexed".
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gsc = _load("gsc_report", "scripts/gsc-report.py")

FIXTURE = {
    "status": "ok",
    "impressions": 211,
    "clicks": 1,
    "position": 40.6,
    "pages": 89,
    "indexing": {"inspected": 8, "not_indexed": 1},
}


def test_render_line_matches_the_accepted_format():
    assert gsc.render_line(FIXTURE) == (
        "google: 211 imp / 1 clicks / pos 40.6 / 89 pages / not-indexed 1/8")


def test_render_line_is_action_required_when_the_instrument_is_dead():
    dead = {"status": "instrument_dead", "why": "HttpError 403"}
    assert gsc.render_line(dead) == "ACTION REQUIRED: google: HttpError 403"


def test_render_line_says_n_a_not_zero_when_indexing_was_never_measured():
    """A skipped inspection (empty top_fetched_urls, or every call failing)
    must read as n/a, not as a false '0/0' claiming 100% indexed."""
    no_indexing = {k: v for k, v in FIXTURE.items() if k != "indexing"}
    assert "not-indexed n/a" in gsc.render_line(no_indexing)


def test_render_line_says_n_a_when_every_inspection_failed():
    """ruling-1: count_not_indexed([]) returns {"inspected": 0, "not_indexed":
    0} — a NON-EMPTY dict, so a naive `if idx:` presence check is true and
    prints the false '0/0' ("100% indexed") the live 403-on-every-call run
    produced. `inspected` must be checked, not just key presence."""
    zero_indexing = {**FIXTURE, "indexing": {"inspected": 0, "not_indexed": 0}}
    assert "not-indexed n/a" in gsc.render_line(zero_indexing)


def test_count_not_indexed_reads_the_literal_live_answer():
    states = ["PASS"] * 7 + ["NEUTRAL"]
    assert gsc.count_not_indexed(states) == {"inspected": 8, "not_indexed": 1}


def test_count_not_indexed_is_zero_when_everything_is_indexed():
    assert gsc.count_not_indexed(["PASS"] * 5) == {
        "inspected": 5, "not_indexed": 0}


def test_count_not_indexed_is_verdict_not_substring():
    """"URL is unknown to Google" has no "not indexed" substring but verdict
    NEUTRAL: not indexed. Only PASS counts as indexed."""
    assert gsc.count_not_indexed(["PASS", "NEUTRAL", "NEUTRAL", ""]) == {
        "inspected": 4, "not_indexed": 3}


def test_inspect_indexing_sends_a_full_url_not_a_bare_path():
    """ruling-1's live finding: URL Inspection 403s on a bare path
    ("not part of this property") — it only accepts a full URL. This pins
    the literal full URL in the inspect() request body, the exact thing the
    live run caught that the mocked-response fixture above could not."""
    seen: dict = {}

    class FakeInspect:
        def execute(self):
            return {"inspectionResult": {"indexStatusResult": {
                "coverageState": "Submitted and indexed", "verdict": "PASS"}}}

    class FakeIndex:
        def inspect(self, body):
            seen["body"] = body
            return FakeInspect()

    class FakeInspection:
        def index(self):
            return FakeIndex()

    class FakeClient:
        def urlInspection(self):
            return FakeInspection()

    gsc.inspect_indexing(FakeClient(), ["/how-to/how-to-refer-friend-ether-fi/"])
    assert seen["body"] == {
        "inspectionUrl": "https://defycard.com/how-to/how-to-refer-friend-ether-fi/",
        "siteUrl": "sc-domain:defycard.com",
    }


# ---- CCH-SEO-01: --inspect-all (expected values are literals, Y49) ----

class _Fake:
    def __init__(self, table):
        self.t = table
        self.u = None

    def urlInspection(self): return self
    def index(self): return self

    def inspect(self, body):
        self.u = body["inspectionUrl"]
        return self

    def execute(self):
        v = self.t[self.u]
        if isinstance(v, Exception):
            raise v
        cov, ver = v
        return {"inspectionResult": {"indexStatusResult": {"coverageState": cov, "verdict": ver}}}


def test_inspect_all_summary_by_bucket_records_errors():
    H = "https://defycard.com/"
    table = {
        H + "faq/a/": ("Submitted and indexed", "PASS"),
        H + "faq/b/": ("Discovered - currently not indexed", "NEUTRAL"),
        H + "how-to/a/": ("Crawled - currently not indexed", "NEUTRAL"),
        H + "how-to/b/": ("URL is unknown to Google", "NEUTRAL"),
        H + "videos/x/": ("Submitted and indexed", "PASS"),
        H + "videos/y/": RuntimeError("boom"),
    }
    res = gsc.inspect_all(_Fake(table), list(table))
    assert res[H + "videos/y/"] == {"coverage": "instrument_error", "why": "RuntimeError: boom"}
    assert gsc.summarize(res) == {
        "faq": {"indexed": 1, "not_indexed": 1, "error": 0},
        "how-to": {"indexed": 0, "not_indexed": 2, "error": 0},
        "videos": {"indexed": 1, "not_indexed": 0, "error": 1},
    }


def test_inspect_all_429_sleeps_once_then_records_error():
    class E429(Exception):
        resp = type("R", (), {"status": 429})()
    sleeps = []
    res = gsc.inspect_all(_Fake({"https://defycard.com/a/b/": E429("q")}),
                          ["https://defycard.com/a/b/"], sleep=sleeps.append)
    assert sleeps == [60]
    assert res["https://defycard.com/a/b/"]["coverage"] == "instrument_error"


def test_inspect_all_never_exceeds_the_cap():
    assert gsc.INSPECT_ALL_MAX == 1800
    urls = [f"https://defycard.com/faq/{i}/" for i in range(1801)]
    table = {u: ("Submitted and indexed", "PASS") for u in urls}
    assert len(gsc.inspect_all(_Fake(table), urls)) == 1800
