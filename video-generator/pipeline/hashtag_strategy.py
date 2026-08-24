"""Platform-differentiated hashtag composition.

Per polymarket vg2 IT-10-HASHTAGS:
  - YT visible: 5-7  (#Shorts + 2 niche + 1 trending + brand)
  - YT API:     15-20 long-tail (≤500-char wire incl. quote overhead)
  - Instagram:  25-28 niche + brand + IG-specific
  - TikTok:     7-8 (#fyp + 3 niche + 2 trending + 1 brand + 1 category)
"""
from __future__ import annotations
import hashlib
import random
from typing import Iterable

CRYPTO_CARD_HASHTAGS: dict[str, list[str]] = {
    "core":           ["#cryptocard", "#cryptocards", "#cryptodebitcard", "#cryptopayments"],
    "brand_specific": ["#etherfi", "#etherficash", "#cryptocom", "#redotpay",
                       "#metamaskcard", "#gnosispay"],
    "feature":        ["#cashback", "#yieldwhilespending", "#stablecoin", "#USDC",
                       "#defipayments", "#tapttopay", "#applepay"],
    "audience":       ["#crypto", "#DeFi", "#bitcoin", "#ethereum", "#fintech",
                       "#cryptoinvestor", "#cryptolife"],
    "trending":       ["#cryptolife", "#paywithcrypto"],
    "brand":          ["#defycard"],
}


def _seed(topic_id: int, day: str) -> int:
    return int(hashlib.sha256(f"{topic_id}:{day}".encode()).hexdigest()[:8], 16)


def _sample(rng: random.Random, pool: Iterable[str], k: int) -> list[str]:
    pool = [t for t in pool]
    if k >= len(pool):
        return list(pool)
    return rng.sample(pool, k)


def _tag_wire_cost(t: str) -> int:
    return len(t) + (2 if " " in t else 0)


def for_youtube(topic_id: int, day: str,
                long_tail: list[str] | None = None,
                shorts: bool = True) -> dict[str, list[str]]:
    """Visible + API hashtags for one YouTube video.

    `shorts` exists because #Shorts is a claim about the format, not a topic
    tag. On a long-form video (Y52) it is simply false: the file is past the
    180 s boundary, YouTube serves it as a watch page, and tagging it #Shorts
    both misdescribes it and blurs the one signal the long-form pilot was built
    to measure. Callers rendering above the boundary pass shorts=False.
    """
    rng = random.Random(_seed(topic_id, day))
    visible = (
        (["#Shorts"] if shorts else [])
        + ["#cryptocard"]
        + _sample(rng, CRYPTO_CARD_HASHTAGS["brand_specific"], 2)
        + _sample(rng, CRYPTO_CARD_HASHTAGS["feature"], 2)
        + ["#defycard"]
    )
    visible = list(dict.fromkeys(visible))[:7]

    api_seed = (
        [t.lstrip("#") for t in visible]
        + _sample(rng, [t.lstrip("#") for t in CRYPTO_CARD_HASHTAGS["audience"]], 5)
        + (long_tail or [])
    )
    # Trim per-tag at last word boundary if > 30 chars
    trimmed = []
    for t in api_seed:
        if len(t) > 30:
            t = " ".join(t.split()[:5])[:30].rsplit(" ", 1)[0]
        trimmed.append(t)
    # Wire budget 480 (20 char safety margin under YouTube's 500 cap)
    out: list[str] = []
    wire = 0
    for t in trimmed:
        cost = _tag_wire_cost(t) + (1 if out else 0)
        if wire + cost > 480:
            break
        out.append(t)
        wire += cost
    return {"visible": visible, "api": out}


def for_instagram(topic_id: int, day: str) -> list[str]:
    rng = random.Random(_seed(topic_id, day))
    tags = (
        ["#Reels", "#ReelsInstagram", "#defycard"]
        + _sample(rng, CRYPTO_CARD_HASHTAGS["core"], 4)
        + _sample(rng, CRYPTO_CARD_HASHTAGS["brand_specific"], 4)
        + _sample(rng, CRYPTO_CARD_HASHTAGS["feature"], 5)
        + _sample(rng, CRYPTO_CARD_HASHTAGS["audience"], 6)
    )
    return list(dict.fromkeys(tags))[:28]


def for_tiktok(topic_id: int, day: str) -> list[str]:
    rng = random.Random(_seed(topic_id, day))
    tags = (
        ["#fyp"]
        + _sample(rng, CRYPTO_CARD_HASHTAGS["core"], 3)
        + _sample(rng, CRYPTO_CARD_HASHTAGS["trending"], 2)
        + ["#defycard"]
        + _sample(rng, CRYPTO_CARD_HASHTAGS["feature"], 1)
    )
    # NEVER use cross-platform tags
    forbidden = {"#Reels", "#ReelsInstagram", "#YouTubeShorts", "#Shorts"}
    return [t for t in dict.fromkeys(tags) if t not in forbidden][:8]
