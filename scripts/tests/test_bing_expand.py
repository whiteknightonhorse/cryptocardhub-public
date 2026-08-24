"""G32 — what may enter the keyword bank, and what decides the order.

The bank chose topics for 544 pages by a column its own header called an
order-of-magnitude estimate. Replacing that key is only worth anything if the
new one is pinned down, so the laws are literals here (Y49): the adjacency
floor, the topicality rule, and the fact that impressions decide NOTHING.

One test exists because of a bug that looked like working code: the
navigational filter was written into the file as a literal backspace (0x08)
instead of `\\b`, because the patch that generated it used a non-raw Python
string. It read correctly in review and matched nothing at all. Regexes that
carry a law now get asserted against real strings from the real pull.
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


be = _load("bing_expand", "scripts/bing-expand.py")


# --- topicality: volume is not relevance -----------------------------------

def test_the_biggest_numbers_in_the_pull_are_the_ones_we_must_refuse():
    """Real rows, with their real impression counts, all rejected:
    coinbase login 84,538 · metamask download 27,319 · coinbase stock 8,052."""
    for q in ("coinbase login", "metamask download", "coinbase stock",
              "coinbase customer service number", "www.coinbase.com sign in",
              "metamask wallet site apps-metamask.org", "coinbase.com"):
        assert not be.on_topic(q), q


def test_a_domain_is_navigational_but_a_domain_plus_a_product_is_not():
    """crypto.com is a brand we cover — a blanket .com rule threw away real
    topics along with the navigation."""
    assert not be.on_topic("crypto.com")
    assert not be.on_topic("crypto.com login")
    assert be.on_topic("crypto.com card")


def test_traditional_finance_demand_is_refused_however_large():
    """'pay my capital one credit card' is 724 impressions of a business we
    are not in. Card + fee words are not enough; crypto or a brand is."""
    for q in ("pay my capital one credit card", "how to pay off credit card debt",
              "credit cards with rewards", "best travel credit cards no fees",
              "kids debit cards with no fees"):
        assert not be.on_topic(q), q


def test_brands_match_on_word_boundaries():
    """Substring matching on the brand 'gate' admitted 'com surrogate how to
    close' and 'are summit racing wastegates worth it'."""
    assert not be.on_topic("com surrogate how to close")
    assert not be.on_topic("are summit racing wastegates worth it")
    assert be.on_topic("gate card")


def test_our_own_subject_survives_all_of_it():
    for q in ("crypto debit card", "kast card", "bybit card", "gnosis pay",
              "best crypto card for travel", "buy crypto with debit card"):
        assert be.on_topic(q), q


# --- the selection key -----------------------------------------------------

def test_adjacency_is_measured_against_what_agents_actually_fetch():
    profile = {"ether": 108.0, "fi": 108.0, "cash": 108.0, "kyc": 77.0,
               "crypto": 49.0, "card": 49.0, "country": 49.0}
    near = be.adjacency("ether fi cash limits", profile)
    far = be.adjacency("nerd wallet compare", profile)
    assert near > far
    assert 0.0 <= far < be.MIN_ADJACENCY <= near <= 1.0


def test_an_empty_profile_scores_zero_rather_than_guessing():
    """No aggregate yet means UNKNOWN. Scoring anything above the floor here
    would let a candidate through on no evidence at all."""
    assert be.adjacency("kast card", {}) == 0.0


def test_impressions_do_not_decide_anything():
    """The instrument reads 0 for terms with thousands of Google searches a
    month, so a threshold on it discards good strings for no reason. The
    constant that remains is a readability marker, not a gate."""
    assert not hasattr(be, "MIN_IMPRESSIONS")
    assert be.READABLE_ABOVE == 10
    assert be.MIN_ADJACENCY == 0.05


def test_plural_and_stopword_variants_are_one_keyword():
    """Otherwise 'best crypto debit cards' poses as new against the covered
    'best crypto debit card' and we print the same page twice."""
    assert be.normalise("best crypto debit cards") == be.normalise("crypto debit card")
    assert be.normalise("the best crypto card 2026") == be.normalise("crypto card")
