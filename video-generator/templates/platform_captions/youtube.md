# YouTube — Description + Pinned Comment Templates

## Description (NO https://, NO www. — small-channel demotion rule)

```
{thumbnail_hook_line_1}
{thumbnail_hook_line_2}

{voiceover_first_2_sentences}

🤖 Compare 11 crypto cards — see pinned comment below
✉️ Weekly card-fee tracker — link in pinned comment

{youtube_visible_hashtags}

Affiliate links — we may earn commission. Not financial advice.
```

## Title

Format: `{emoji_or_digit} {hook_line_1}`

If `thumbnail_hook_line_1` does NOT begin with an emoji or a digit, prepend a category emoji per topic class:
- TOFU → `📈`
- MOFU → `🔍`
- BOFU → `💳`

Cap at 60 chars. Truncate at last word boundary.

## Pinned comment (URLs allowed here)

```
👉 Get the card: https://defycard.com/hub/best-crypto-card-2026/
🔍 Compare 11 cards: https://defycard.com/compare/
✉️ Weekly fee tracker: https://defycard.com/news/

Affiliate links — we may earn commission. Not financial advice.
```

## Hashtag block (visible — appears at end of description)

5–7 tags. Format: space-separated, leading `#`, no commas. Example:

```
#Shorts #cryptocard #etherfi #cashback #crypto #defycard
```

## API tags (hidden — `tags[]` field on `videos.insert`)

15–20 long-tail phrases. Wire-cost ≤ 480 (computed by `_sanitize_tags`).
