# TikTok — Caption Template

Lines 1–2 carry the CTA above the "more" fold (~150 chars).

```
👉 Get the card: dfy.cc/{topic_short}
🔍 Compare 11 cards: dfy.cc/compare

{voiceover_line_1}
{payoff_line_with_emoji}

Affiliate links — we may earn commission. Not financial advice.

{tiktok_hashtags}
```

## Hashtag set

7–8 tags. Format:

```
#fyp #cryptocard #etherfi #cashback #crypto #cryptolife #defycard #cryptopayments
```

## Hard rules

- `#fyp` MANDATORY (FYP eligibility).
- NEVER use `#Reels`, `#ReelsInstagram`, `#YouTubeShorts`, `#Shorts` (cross-platform tag = de-rank).
- Never stuff > 8 tags (TikTok flags as low quality).
- `@defycard` mention in caption body counts separately from hashtag count.

## Short URL

`dfy.cc/<topic_short>` is our self-hosted Caddy redirect to defycard.com/<deep_link>. Avoids TikTok suppressing raw `defycard.com` in some surfaces. Setup: Caddy config maps each of the 35 topic short-codes to a permanent 302 redirect.
