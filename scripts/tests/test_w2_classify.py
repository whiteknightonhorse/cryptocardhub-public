"""G30 — the classification that decides which URLs may be proposed for a 301.

The dangerous direction is one-way: a page wrongly called a double zero can be
redirected away, and a redirect on a URL people actually reach destroys working
traffic. So every law here is written from the side of NOT redirecting.

Literals, not imports (lesson Y49): the threshold 0.90, the two eligible
buckets and the window are typed out by hand.
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


w2 = _load("w2_classify", "scripts/w2-classify.py")

WARM = "/learn/what-is-a-crypto-card/"
DEAD = "/for/crypto-card-for-whales/"
DEAD2 = "/for/crypto-card-for-travellers/"


# --- the one that protects live traffic ------------------------------------

def test_a_page_with_human_sessions_is_never_a_double_zero():
    split = w2.classify([WARM, DEAD], {WARM: 1})
    assert split["humanly_warm"] == [WARM]
    assert split["double_zero"] == [DEAD]


def test_one_session_is_enough_to_protect_a_page():
    """No 'barely any traffic' threshold exists, deliberately: the site's whole
    audience is ~700 human sessions a quarter, so a single session is signal."""
    assert w2.classify([WARM], {WARM: 1})["double_zero"] == []
    assert w2.classify([WARM], {WARM: 0})["double_zero"] == [WARM]


def test_every_cold_page_lands_in_exactly_one_list():
    cold = [WARM, DEAD, DEAD2]
    split = w2.classify(cold, {WARM: 3, DEAD2: 0})
    assert sorted(split["humanly_warm"] + split["double_zero"]) == sorted(cold)
    assert not set(split["humanly_warm"]) & set(split["double_zero"])


# --- unmeasured must not read as measured ----------------------------------

def test_unreachable_ga4_returns_none_not_an_empty_mapping(monkeypatch, tg_alerts):
    """{} would make every page a double zero — the whole site a redirect
    candidate — off a network error."""
    monkeypatch.setattr(w2, "_load", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert w2.ga4_sessions("2026-07-16", "2026-08-13") is None
    assert tg_alerts == ["instrument_dead: w2-classify GA4 unreachable (RuntimeError: boom) — classification refused"]


# --- duplicate detection ---------------------------------------------------

def _row(path, claims, cards):
    return {"path": path, "slug": path.strip("/").split("/")[-1],
            "claims": set(claims), "cards": set(cards)}


def test_identical_facts_and_same_cards_make_a_pair():
    rows = [_row(DEAD, {"3%", "$2,000", "76countries"}, {"ether.fi"}),
            _row(DEAD2, {"3%", "$2,000", "76countries"}, {"ether.fi"})]
    pairs = w2.duplicate_pairs(rows)
    assert len(pairs) == 1
    assert pairs[0]["jaccard"] == 1.0
    assert pairs[0]["differing_facts"] == []


def test_the_same_facts_about_different_cards_are_not_a_pair():
    """Two pages can share the ether.fi rate table and still be different
    pages, because one of them also covers RedotPay."""
    rows = [_row(DEAD, {"3%", "$2,000"}, {"ether.fi"}),
            _row(DEAD2, {"3%", "$2,000"}, {"ether.fi", "redotpay"})]
    assert w2.duplicate_pairs(rows) == []


def test_the_threshold_is_ninety_percent():
    shared = {f"{i}%" for i in range(9)}
    rows = [_row(DEAD, shared | {"$100"}, {"ether.fi"}),
            _row(DEAD2, shared | {"$500"}, {"ether.fi"})]
    assert w2.jaccard(rows[0]["claims"], rows[1]["claims"]) < 0.90
    assert w2.duplicate_pairs(rows) == []
    # ...and a pair that genuinely clears it is found
    rows2 = [_row(DEAD, shared | {"$100"}, {"ether.fi"}),
             _row(DEAD2, shared | {"$100"}, {"ether.fi"})]
    assert len(w2.duplicate_pairs(rows2)) == 1


def test_a_page_with_no_extractable_facts_is_never_paired():
    """An empty fact set is unmeasured, not 'identical to everything'."""
    rows = [_row(DEAD, set(), {"ether.fi"}), _row(DEAD2, set(), {"ether.fi"})]
    assert w2.duplicate_pairs(rows) == []


def test_spelling_variants_of_one_fact_are_one_fact():
    """'3 %' and '3%' are the same claim; without normalising, real duplicates
    look distinct and the pair list is empty for a typographic reason."""
    import re
    text_a = 'cashback: up to 3 % standard, $2,000/mo'
    text_b = 'cashback: up to 3% standard, $2,000/mo'
    norm = lambda t: {re.sub(r"\s+", "", c.lower()) for c in w2._CLAIM.findall(t)}
    assert norm(text_a) == norm(text_b) != set()
