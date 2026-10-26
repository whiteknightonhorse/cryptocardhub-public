"""G38 — the cohort is judged by the FAMILY, and the tap is the verdict rate.

A child page answers a question its parent already answers. If a retriever
starts citing the child, the parent's own fetches can fall — and a child
measured ALONE would report success while the family stood still or shrank.
So: fetches(parent) + fetches(children) against the parent's own baseline from
before the cohort opened.

Both thresholds are literals here, not imports (Y49): 30 % of children fetched,
and the family at or above baseline.

The tests that matter most are the REFUSALS. A verdict machine that always
produces a verdict is the same failure as a checker that always says "match":
it has no discriminating power, and its green means nothing.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "faq_verdict", ROOT / "scripts" / "faq-verdict.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["faq_verdict"] = mod
    spec.loader.exec_module(mod)
    return mod


fv = _load()

OPENED = date(2026, 8, 3)
PARENT = "/faq/who-can-get-ether-fi-cash/"
KIDS = [f"kid-{i}" for i in range(10)]
COHORT = {"opened": OPENED.isoformat(),
          "items": [{"slug": k, "parent": PARENT, "parent_fetches": 100, "published": {"on": "2026-08-05", "path": "x"}}
                    for k in KIDS]}


def _weeks(monkeypatch, before_parent, after_parent, after_children_each,
           children_fetched):
    """A history with one complete VERIFIED week each side of the cohort."""
    kids = {f"/faq/{k}/": (after_children_each if i < children_fetched else 0)
            for i, k in enumerate(KIDS)}
    rows = [
        {"week": "2026-W30", "since": "2026-07-20", "until": "2026-07-26",
         "days_covered": 7, "verified_by_page": {PARENT: before_parent}},
        {"week": "2026-W33", "since": "2026-08-10", "until": "2026-08-16",
         "days_covered": 7, "verified_by_page": {PARENT: after_parent, **kids}},
    ]
    monkeypatch.setattr(fv, "weeks", lambda: rows)


def test_a_growing_family_passes(monkeypatch):
    _weeks(monkeypatch, before_parent=70, after_parent=60,
           after_children_each=5, children_fetched=5)
    v = fv.judge(COHORT, OPENED + timedelta(days=15), force=False)
    assert v["status"] == "pass", v["summary"]
    assert v["additive"] and v["reach_met"]


def test_a_child_that_only_cannibalises_its_parent_fails(monkeypatch):
    """The case the family rule exists for: children get fetched, the parent
    falls by more, and a child-only metric would have called this a win."""
    _weeks(monkeypatch, before_parent=70, after_parent=20,
           after_children_each=2, children_fetched=8)
    v = fv.judge(COHORT, OPENED + timedelta(days=15), force=False)
    assert v["status"] == "fail"
    assert v["additive"] is False
    assert v["reach_met"] is True          # reach alone must not carry a pass


def test_reach_below_thirty_percent_fails_even_if_the_family_grew(monkeypatch):
    """Two children carrying the whole cohort is not a cohort that worked."""
    _weeks(monkeypatch, before_parent=50, after_parent=50,
           after_children_each=40, children_fetched=2)
    v = fv.judge(COHORT, OPENED + timedelta(days=15), force=False)
    assert v["status"] == "fail"
    assert v["additive"] is True
    assert v["reach_met"] is False
    assert v["children_share"] == 0.2


def test_the_threshold_is_thirty_percent():
    assert fv.MIN_CHILDREN_FETCHED == 0.30
    assert (fv.WINDOW_14, fv.WINDOW_28) == (14, 28)


# --- the refusals ----------------------------------------------------------

def test_no_verdict_before_the_window_closes(monkeypatch):
    _weeks(monkeypatch, 70, 90, 5, 9)
    v = fv.judge(COHORT, OPENED + timedelta(days=3), force=False)
    assert v["status"] == "too_early"


def test_no_baseline_means_unmeasurable_not_failed(monkeypatch):
    """Weeks recorded before `by_page` existed carry no per-page figures.
    Treating that as a baseline of zero would manufacture a pass for any
    cohort at all."""
    monkeypatch.setattr(fv, "weeks", lambda: [
        {"week": "2026-W30", "since": "2026-07-20", "until": "2026-07-26",
         "days_covered": 7},                                   # no verified_by_page
        {"week": "2026-W33", "since": "2026-08-10", "until": "2026-08-16",
         "days_covered": 7, "verified_by_page": {PARENT: 90}},
    ])
    v = fv.judge(COHORT, OPENED + timedelta(days=15), force=False)
    assert v["status"] == "unmeasurable"
    assert "baseline" in v["summary"]


def test_unmeasurable_carries_a_window_so_main_can_write_it_to_the_ledger(monkeypatch):
    """A retraction is only complete once the re-run verdict actually lands in
    the ledger (main()'s `verdict_{v['window']}` key needs this present)."""
    monkeypatch.setattr(fv, "weeks", lambda: [])
    v = fv.judge(COHORT, OPENED + timedelta(days=15), force=False)
    assert v["status"] == "unmeasurable"
    assert v["window"] in (fv.WINDOW_14, fv.WINDOW_28)


def test_a_claimed_only_baseline_is_also_unmeasurable_not_a_free_pass(monkeypatch):
    """The 2026-08-31 defect, reproduced as a test: a week that has `by_page`
    (claimed) but no `verified_by_page` must NOT feed the baseline — that is
    exactly the unverified-scanner-inflated number the retraction exists for."""
    monkeypatch.setattr(fv, "weeks", lambda: [
        {"week": "2026-W30", "since": "2026-07-20", "until": "2026-07-26",
         "days_covered": 7, "by_page": {PARENT: 9000}},        # claimed only
        {"week": "2026-W33", "since": "2026-08-10", "until": "2026-08-16",
         "days_covered": 7, "verified_by_page": {PARENT: 90}},
    ])
    v = fv.judge(COHORT, OPENED + timedelta(days=15), force=False)
    assert v["status"] == "unmeasurable"


def test_no_week_after_the_cohort_means_no_data_yet(monkeypatch):
    monkeypatch.setattr(fv, "weeks", lambda: [
        {"week": "2026-W30", "since": "2026-07-20", "until": "2026-07-26",
         "days_covered": 7, "verified_by_page": {PARENT: 70}},
    ])
    v = fv.judge(COHORT, OPENED + timedelta(days=15), force=False)
    assert v["status"] == "no_data_yet"


def test_rates_are_per_day_not_per_week(monkeypatch):
    """The windows are 14 and 28 days while the aggregates are weekly, so a
    per-week comparison would compare unlike spans the moment a week is
    partial."""
    _weeks(monkeypatch, before_parent=70, after_parent=70,
           after_children_each=0, children_fetched=0)
    v = fv.judge(COHORT, OPENED + timedelta(days=15), force=False)
    assert v["baseline_parent_per_day"] == 10.0     # 70 over 7 days
    assert v["now_parent_per_day"] == 10.0


# --- verified vs claimed (2026-09-01, the 654->99 fix) ----------------------

def test_judge_prefers_verified_by_page_over_inflated_claimed(monkeypatch):
    """A row carrying both must use `verified_by_page` — this is the exact
    defect that produced the 2026-08-31 `fail` verdict on dirty data: a
    scanner inflating `by_page` for the parent must not count."""
    rows = [
        {"week": "2026-W30", "since": "2026-07-20", "until": "2026-07-26",
         "days_covered": 7, "by_page": {PARENT: 70}, "verified_by_page": {PARENT: 70}},
        {"week": "2026-W33", "since": "2026-08-10", "until": "2026-08-16",
         "days_covered": 7,
         "by_page": {PARENT: 9000},                      # scanner-inflated claim
         "verified_by_page": {PARENT: 60, **{f"/faq/{k}/": 5 for k in KIDS[:5]}}},
    ]
    monkeypatch.setattr(fv, "weeks", lambda: rows)
    v = fv.judge(COHORT, OPENED + timedelta(days=15), force=False)
    assert v["now_parent_per_day"] == round(60 / 7, 2)   # verified, not 9000/7
    assert v["additive"] is True                              # 60+25 >= 70 baseline


def test_daily_rate_never_falls_back_to_claimed_by_page():
    """A verdict is a conviction (Y75). A week that only has claimed `by_page`
    — pre-verification data, or raw logs since rotated out of reach and
    unrecoverable — contributes NO signal here, unlike the softer fallback
    `aeo-aggregate.py build_targets` uses for its editorial priority list."""
    rows = [{"since": "x", "until": "y", "days_covered": 7, "by_page": {PARENT: 9000}}]
    rate, days = fv.daily_rate(rows, {PARENT})
    assert (rate, days) == (0.0, 0)


def test_daily_rate_does_not_fall_back_when_verified_is_an_empty_dict():
    """An empty `verified_by_page` means every claim that week failed
    verification — real signal, not a reason to trust the inflated claim."""
    rows = [{"since": "x", "until": "y", "days_covered": 7,
             "by_page": {PARENT: 9000}, "verified_by_page": {}}]
    rate, days = fv.daily_rate(rows, {PARENT})
    assert (rate, days) == (0.0, 7)


# --- the tap ---------------------------------------------------------------

def test_the_tap_stays_shut_until_the_cohort_is_read(tmp_path, monkeypatch):
    """Fable: the limit is not how many pages we may print, it is how many
    hypotheses we can judge."""
    led = tmp_path / "faq-cohorts.json"
    led.write_text(json.dumps({"cohorts": [
        {"opened": "2026-08-13", "verdict_due_14": "2026-08-27", "items": []}]}),
        encoding="utf-8")
    monkeypatch.setattr(fv, "LEDGER", led)
    ok, why = fv.tap_is_open()
    assert not ok
    assert "no +14 reading" in why


def test_the_tap_opens_once_a_verdict_exists(tmp_path, monkeypatch):
    led = tmp_path / "faq-cohorts.json"
    led.write_text(json.dumps({"cohorts": [
        {"opened": "2026-08-13", "verdict_14": {"status": "pass"}, "items": []}]}),
        encoding="utf-8")
    monkeypatch.setattr(fv, "LEDGER", led)
    ok, _ = fv.tap_is_open()
    assert ok


def test_the_first_cohort_is_never_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(fv, "LEDGER", tmp_path / "missing.json")
    ok, _ = fv.tap_is_open()
    assert ok


def test_a_voided_cohort_opens_the_tap(tmp_path, monkeypatch):
    led = tmp_path / "faq-cohorts.json"
    led.write_text(json.dumps({"cohorts": [
        {"opened": "2026-09-28", "verdict_due_14": "2026-10-12", "items": [],
         "voided": {"on": "2026-09-29", "reason": "x"}}]}), encoding="utf-8")
    monkeypatch.setattr(fv, "LEDGER", led)
    ok, _ = fv.tap_is_open()
    assert ok


def test_main_writes_no_verdict_for_a_voided_cohort(tmp_path, monkeypatch):
    led = tmp_path / "faq-cohorts.json"
    body = json.dumps({"cohorts": [
        {"opened": "2026-07-01", "verdict_due_14": "2026-07-15", "items": [],
         "voided": {"on": "2026-09-29", "reason": "x"}}]}, indent=1) + "\n"
    led.write_text(body, encoding="utf-8")
    monkeypatch.setattr(fv, "LEDGER", led)
    monkeypatch.setattr(sys, "argv", ["faq-verdict.py", "--write", "--force"])
    assert fv.main() == 0
    assert led.read_text(encoding="utf-8") == body       # byte-identical: nothing written
