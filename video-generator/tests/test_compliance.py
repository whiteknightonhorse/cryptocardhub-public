"""T-0139 — the gambling-vocab false positives, and the real gate around them.

`gambling_vocab` used to carry bare "bet"/"betting"/"gamble"/"gambling",
inherited from polymarket video-generator4/ai-debates (a prediction-market
product, where "bet" *was* the product). On the card-affiliate corpus those
four words fired on 36 of 574 pages and 0 of them were gambling promotion —
idioms ("your best bet") and compliance-positive copy ("blocks gambling
merchants, MCC 7995"). Ruling-1 narrowed the vocab to phrases that only match
real gambling promotion, without touching `_BANNED_RE`/`_scan_text` — the law
changed in data, not in the mechanism, and the mechanism must keep refusing
real hits.
"""
from __future__ import annotations

import pytest

from pipeline import compliance


def test_gambling_idiom_is_not_flagged():
    assert compliance._scan_text(
        "In Europe, non-custodial cards are your best bet.") == []


def test_compliance_positive_mcc_copy_is_not_flagged():
    assert compliance._scan_text(
        "ether.fi Cash blocks gambling merchants (MCC 7995).") == []


def test_real_gambling_promotion_is_flagged():
    hits = compliance._scan_text("place your bet at this sportsbook")
    assert set(hits) == {"place your bet", "sportsbook"}


@pytest.mark.parametrize("text", [
    "this is the best card for travel",
    "totally risk-free rewards",
])
def test_marketing_absolutes_still_flagged(text):
    assert compliance._scan_text(text) != []


def test_youtube_platform_ban_still_flagged():
    assert compliance._scan_text(
        "what if I told you this card pays you back", platform="youtube") != []


def test_scan_script_raises_on_a_real_hit():
    with pytest.raises(compliance.ComplianceError):
        compliance.scan_script({"voiceover": "sports betting made easy"})


def test_scan_final_raises_on_a_real_hit():
    with pytest.raises(compliance.ComplianceError):
        compliance.scan_final("online casino bonuses explained")
