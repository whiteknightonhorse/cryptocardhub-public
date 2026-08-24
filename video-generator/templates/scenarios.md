# Video Scenarios — DefyCard

Six baseline formats. Pick one per video. Length target: 15–60 s vertical (1080×1920).
The `write_script` step reads this file and the chosen format's beat list to produce the final voiceover script.

---

## Format A — "Never Sell Your Crypto" (ether.fi USP)

| Beat | Time | Voiceover | Visual |
|---|---|---|---|
| Hook | 0–3 s | "What if you could spend your ETH without selling it?" | App icon, ETH glyph |
| Demo | 3–10 s | App walkthrough — Borrow Mode, card linked to ETH | Screen capture or mockup |
| Value | 10–18 s | "Your ETH keeps staking. You spend fiat. No tax event." | ETH price chart + tap-to-pay |
| Proof | 18–22 s | "3% cashback in crypto on every purchase." | Cashback ticker animation |
| CTA | 22–25 s | "Link in bio — get your card today." | Affiliate URL card |

**Primary triggers:** tax avoidance (US), passive income, crypto-native pride.

---

## Format B — "Replace Your Bank" (anti-bank)

| Beat | Time | Voiceover | Visual |
|---|---|---|---|
| Hook | 0–3 s | "I threw away my bank card 3 months ago." | Card going into trash |
| Demo | 3–10 s | Tap-to-pay in store, café, gas station | Multi-cut B-roll |
| Value | 10–18 s | "Zero foreign fees. 3% cashback in ETH. My keys, my money." | Stat overlay |
| Proof | 18–22 s | "Banks charge you to hold YOUR money. This pays you." | Bank fees screenshot |
| CTA | 22–25 s | "Free card — link in bio." | CTA card |

**Primary triggers:** anti-establishment, self-custody, financial autonomy.

---

## Format C — "Monthly Cashback" (greed)

| Beat | Time | Voiceover | Visual |
|---|---|---|---|
| Hook | 0–3 s | "$127 in free crypto this month — just from groceries." | App screenshot, total |
| Demo | 3–10 s | Cashback history view | App screen recording |
| Value | 10–18 s | "Every coffee, every Uber, every grocery run = crypto back." | B-roll (coffee, taxi, store) |
| Proof | 18–22 s | "Works everywhere Visa is accepted. 150+ countries." | World map highlight |
| CTA | 22–25 s | "Start earning — link in bio." | CTA card |

**Primary triggers:** greed, easy money, FOMO.

---

## Format D — "Travel Hack" (travelers)

| Beat | Time | Voiceover | Visual |
|---|---|---|---|
| Hook | 0–3 s | "My bank charged me $180 in foreign fees last trip." | Bank statement red-circled |
| Demo | 3–10 s | Statement → cross out → new card | Animated transition |
| Value | 10–18 s | "This card: 0% foreign fees. Plus 3% cashback." | Stat overlay |
| Proof | 18–22 s | "I saved $400 in Tokyo in one week." | Tokyo B-roll |
| CTA | 22–25 s | "Link in bio — works worldwide." | CTA card |

**Primary triggers:** loss-aversion, travel envy, savings.

---

## Format E — "Beginner Friendly" (newcomers)

| Beat | Time | Voiceover | Visual |
|---|---|---|---|
| Hook | 0–3 s | "You don't need to understand DeFi to use this card." | Confused-face stock |
| Demo | 3–8 s | Download → add crypto → get card | 3-step animation |
| Value | 8–15 s | "It works like any Visa card. Tap, pay, done." | POS terminal close-up |
| Proof | 15–20 s | "But instead of useless points — you get real crypto back." | Points vs ETH split-screen |
| CTA | 20–25 s | "Free to get started — link in bio." | CTA card |

**Primary triggers:** simplicity, fear-removal, "I can do this too".

---

## Format F — "Watch This BEFORE You Get…" (fear-removal)

| Beat | Time | Voiceover | Visual |
|---|---|---|---|
| Hook | 0–3 s | "Watch this BEFORE you get a crypto card." | Stop-sign overlay |
| Setup | 3–10 s | "Most people make these 3 mistakes…" | Numbered list intro |
| List | 10–20 s | Mistake 1 → 2 → 3 with visual examples | 3 micro-segments |
| Resolution | 20–25 s | "Do it right — link in bio." | CTA card |

**Primary triggers:** fear-of-mistake, social proof, authority.

---

## Emotional triggers — global map

Use to season any format. Mix max 2 per video.

| Trigger | Example phrase | When to use |
|---|---|---|
| **FOMO** | "Smart crypto holders already use this" | Format A, C, D |
| **Greed** | "3% = $90/month at $3000 spending" | Format C |
| **Anti-bank** | "Banks charge you. This pays you." | Format B |
| **Tax (US)** | "No sell = no tax event" | Format A only — US audiences |
| **Status** | Metal card, ETH logo, crypto-native lifestyle | Format A, B |
| **Simplicity** | "Works like any Visa card — everywhere" | Format E |
| **Passive income** | "Spend money AND earn crypto" | Format A, C |

---

## Schedule policy

- 6 videos / day, every day.
- Window: 09:00–21:00 ET.
- Random gap: 1–5 hours; minimum 60 minutes between any two posts.
- Daily cron at 00:05 ET regenerates `state/schedule.json` with 6 fresh slots.
- Slot-watcher cron every 10 min triggers the pipeline if a slot is due.

---

## Voice rotation (round-robin)

1. `en-US-GuyNeural`
2. `en-US-JennyNeural`
3. `en-GB-RyanNeural`
4. `en-US-AriaNeural`

State in `state/voice_rotation.json`. Advance index on every successful render.
