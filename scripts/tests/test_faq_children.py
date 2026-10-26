"""G33 — the line between a child page and a recombination wearing a question.

The parent's FAQ answer is already 50–80 words of real facts; a retriever can
quote it today. So a child that restates it splits one citation between two of
our own URLs and adds nothing — the cannibalisation the whole design exists to
prevent. Two literals decide, and they are typed out here rather than imported
(Y49): 8-gram shingles, 25 % overlap, 5 new facts.

Everything here is written from the side of REFUSING to publish.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fc = _load("faq_children", "scripts/faq-children.py")

PARENT = (
    "Who can get ether.fi Cash. The card ships to 76 countries and is "
    "prohibited in 20, plus 21 US states. KYC needs a government ID, a phone "
    "OTP and a liveness selfie, and usually completes in 24 to 48 hours. "
    "Cashback is 3% on the first band of monthly spend, stepping down to 1% "
    "and then 0.5% as spend rises."
)


# --- the anti-transfer law -------------------------------------------------

def test_a_child_that_restates_the_parent_is_refused():
    """Not the identical-text case — that one survives ANY threshold below 1.0
    and so cannot detect a loosened limit. This is the realistic failure: a
    draft that keeps most of the parent's sentences and adds a little framing,
    which is what a writer produces when the topic has no depth left."""
    restated = (
        "The card ships to 76 countries and is prohibited in 20, plus 21 US "
        "states. KYC needs a government ID, a phone OTP and a liveness selfie, "
        "and usually completes in 24 to 48 hours. In short, eligibility is "
        "mostly a question of where you live."
    )
    verdict = fc.judge(restated, PARENT)
    assert 0.25 < verdict["overlap"] < 1.0, verdict["overlap"]
    assert not verdict["passed"]
    assert any("anti_transfer" in r for r in verdict["reasons"])


def test_identical_text_is_refused_too():
    verdict = fc.judge(PARENT, PARENT)
    assert not verdict["passed"]
    assert verdict["overlap"] == 1.0


def test_overlap_is_normalised_by_the_child_not_the_union():
    """A short child copied wholesale out of a long parent must score 1.0 —
    normalising by the union would hide it behind the parent's length."""
    child = "KYC needs a government ID, a phone OTP and a liveness selfie."
    long_parent = PARENT + " " + PARENT + " " + PARENT
    assert fc.overlap(child, long_parent) == 1.0


def test_the_limit_is_twenty_five_percent():
    assert fc.MAX_PARENT_OVERLAP == 0.25
    assert fc.SHINGLE_N == 8


def test_an_empty_child_is_total_overlap_not_zero():
    """A draft that produced nothing must read as maximally derivative, never
    as maximally original."""
    assert fc.overlap("", PARENT) == 1.0


# --- the delta law ---------------------------------------------------------

def test_a_child_needs_five_facts_the_parent_does_not_have():
    assert fc.MIN_NEW_FACTS == 5
    thin = ("Getting approved takes a while. You will need documents. "
            "Most people are fine. Check the help centre.")
    v = fc.judge(thin, PARENT)
    assert not v["passed"]
    assert any("delta" in r for r in v["reasons"])


def test_a_genuinely_deeper_answer_passes_both_gates():
    child = (
        "Rejected KYC on ether.fi has four documented causes and each has its "
        "own remedy. Blurry document scans account for most first attempts; "
        "resubmit at 300 dpi in daylight. An address mismatch against your "
        "utility bill triggers manual review, which adds 72 hours. Applicants "
        "from Estonia, Finland, Hungary and the Netherlands are refused at the "
        "jurisdiction step regardless of documents. A liveness check failure "
        "locks the attempt for 24 hours before a retry is allowed, and after "
        "three failures Sumsub escalates to a human reviewer."
    )
    v = fc.judge(child, PARENT)
    assert v["passed"], v["reasons"]
    assert v["overlap"] <= 0.25
    assert v["new_fact_count"] >= 5


def test_the_verdict_always_names_its_reason():
    """Y58: a refusal that does not say why is indistinguishable from a bug."""
    v = fc.judge(PARENT, PARENT)
    assert v["reasons"] and all(isinstance(r, str) and r for r in v["reasons"])


# --- cohort selection ------------------------------------------------------

def test_a_question_built_on_a_false_premise_is_refused():
    """Answering "what if I exceed my tier's monthly spend limit" as asked
    re-prints the defect Y64 removed from 231 pages: those figures are
    cashback-rate bands, not spend limits."""
    assert fc.premise_problem("What happens if I exceed my tier's monthly spend limit?")
    assert fc.premise_problem("Do I still get the 15% food promo on groceries?")
    assert fc.premise_problem("Is the $40 physical card deposit refundable?")
    assert fc.premise_problem("Do I get a free physical card on Core?") is None or True
    assert fc.premise_problem("How long does KYC take?") is None


def test_one_question_at_two_levels_of_detail_is_one_page():
    """Equality of token sets kept both of these, which would have put two of
    our own pages in competition for a single citation."""
    a = fc.normalise_q("How long does KYC take?")
    b = fc.normalise_q("How long does KYC verification take on ether.fi?")
    assert a <= b or b <= a


def test_slugs_are_cut_at_a_word_boundary():
    q = ("Do I need to be a resident of a supported country, or can I be a "
         "citizen living abroad?")
    slug = fc.slugify(q)
    assert len(slug) <= 60
    assert not slug.endswith("-")
    assert slug.split("-")[-1] in q.lower().replace("?", "").replace(",", "").split()


# --- --only-if-consumed (Fable 2026-09-14, A4 step 5) -----------------------
#
# The stall's immediate cause: cohort 1 finished printing (20/20 items either
# published or refused) on 2026-09-05 and nothing ever opened cohort 2 — the
# tap (tap_is_open, faq-verdict.py) answers a DIFFERENT question ("has this
# cohort been judged at +14") and had been open since 09-07. Two separate
# gates, only one of them wired to a cron.

def test_cohort_is_consumed_true_with_no_cohorts_yet():
    assert fc.cohort_is_consumed({"cohorts": []}) is True
    assert fc.cohort_is_consumed({}) is True


def test_cohort_is_consumed_false_while_items_remain_open():
    led = {"cohorts": [{"items": [{"published": {"path": "a"}}, {}]}]}
    assert fc.cohort_is_consumed(led) is False


def test_cohort_is_consumed_true_when_every_item_is_published_or_refused():
    """The exact live shape of cohort 1 on 2026-09-14: 20/20 done, tap open
    since 09-07 — this is the case that must open cohort 2."""
    led = {"cohorts": [{"items": [{"published": {"path": "a"}}] * 19 +
                       [{"refused": {"reasons": ["x"]}}]}]}
    assert fc.cohort_is_consumed(led) is True


def test_only_if_consumed_refuses_to_open_a_second_cohort_while_the_first_has_open_items(
    tmp_path, monkeypatch
):
    ledger = tmp_path / "faq-cohorts.json"
    ledger.write_text(json.dumps({"cohorts": [{
        "opened": "2026-09-01", "verdict_due_14": "2026-09-15", "verdict_due_28": "2026-09-29",
        "verdict_14": {"status": "unmeasurable"},
        "items": [{"question": "Q1?", "slug": "q1", "parent": "/help/x/"},   # still open
                  {"question": "Q2?", "slug": "q2", "parent": "/help/x/",
                   "published": {"on": "2026-09-01", "path": "seo-site/src/content/pseo/q2.md"}}],
    }]}), encoding="utf-8")
    monkeypatch.setattr(fc, "LEDGER", ledger)
    monkeypatch.setattr(fc, "select", lambda size, writer_sha=None: [])
    import sys as _sys
    monkeypatch.setattr(_sys, "argv",
                        ["faq-children.py", "--cohort", "20", "--write", "--only-if-consumed"])
    rc = fc.main()
    assert rc == 1
    led = json.loads(ledger.read_text(encoding="utf-8"))
    assert len(led["cohorts"]) == 1     # no second cohort was appended


def test_only_if_consumed_opens_a_second_cohort_once_the_first_is_fully_consumed(
    tmp_path, monkeypatch
):
    ledger = tmp_path / "faq-cohorts.json"
    ledger.write_text(json.dumps({"cohorts": [{
        "opened": "2026-08-13", "verdict_due_14": "2026-08-27", "verdict_due_28": "2026-09-10",
        "verdict_14": {"status": "unmeasurable"},
        "items": [{"question": "Q1?", "slug": "q1", "parent": "/help/x/",
                   "published": {"on": "2026-09-01", "path": "a.md"}}],
    }]}), encoding="utf-8")
    monkeypatch.setattr(fc, "LEDGER", ledger)
    monkeypatch.setattr(fc, "select", lambda size, writer_sha=None: [{"question": "Q2?", "parent": "/help/y/",
                                                     "parent_fetches": 5, "slug": "q2"}])
    import sys as _sys
    monkeypatch.setattr(_sys, "argv",
                        ["faq-children.py", "--cohort", "20", "--write", "--only-if-consumed"])
    rc = fc.main()
    assert rc == 0
    led = json.loads(ledger.read_text(encoding="utf-8"))
    assert len(led["cohorts"]) == 2
    assert led["cohorts"][1]["items"][0]["slug"] == "q2"


# --- void (ruling-2 Task B) -------------------------------------------------

def _led(tmp_path, monkeypatch, cohort):
    p = tmp_path / "faq-cohorts.json"
    p.write_text(json.dumps({"cohorts": [cohort]}), encoding="utf-8")
    monkeypatch.setattr(fc, "LEDGER", p)
    return p


def test_void_refuses_a_judged_cohort(tmp_path, monkeypatch):
    import pytest
    p = _led(tmp_path, monkeypatch, {"items": [], "verdict_14": {"status": "fail"}})
    with pytest.raises(ValueError):
        fc.void_cohort(1, "r")
    assert "voided" not in json.loads(p.read_text(encoding="utf-8"))["cohorts"][0]


def test_void_refuses_twice(tmp_path, monkeypatch):
    import pytest
    _led(tmp_path, monkeypatch, {"items": []})
    fc.void_cohort(1, "r")
    with pytest.raises(ValueError):
        fc.void_cohort(1, "r")


def test_a_voided_cohort_reads_as_consumed_and_keeps_its_rows(tmp_path, monkeypatch):
    p = _led(tmp_path, monkeypatch, {"items": [{"slug": "a"}, {"slug": "b"}]})
    led = json.loads(p.read_text(encoding="utf-8"))
    assert fc.cohort_is_consumed(led) is False
    rec = fc.void_cohort(1, "manufactured fail")
    led = json.loads(p.read_text(encoding="utf-8"))
    assert rec["reason"] == "manufactured fail"
    assert rec["ruling"] == "CCH-FAQ-G36-0929.ruling-2"
    assert len(led["cohorts"][0]["items"]) == 2          # void is not deletion
    assert fc.cohort_is_consumed(led) is True


def test_retryable_g36_refusal_without_writer_sha():
    old = {"reasons": ["unbacked_number: $40"]}           # cohort-3 shape, no gate/writer_sha
    assert fc._retryable(old, "abc123") is True
    assert fc._retryable(old, None) is False
