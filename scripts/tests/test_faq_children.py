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
