#!/usr/bin/env python3
"""
render-trailer.py — one-off DefyCard channel-trailer renderer.

The auto-mode pipeline (write_script → tts_edge → fetch_visuals → fetch_music
→ render) produces the same template every time, which is the exact pattern
YT's anti-mass-production classifier suppresses. The CHANNEL TRAILER must
visually + textually differ:
  - explicit channel-intro framing ("we rate every crypto card...")
  - different visual mix (more brand/copy frames vs. travel stock)
  - 30-40s, hook-value-CTA structure
  - intentional pacing (slower hook beat, value-prop callout, end CTA)

Output: video-generator/output/youtube/<date>/trailer/final.mp4
After render, sends to Telegram for operator manual upload via YT Studio
(operator wants Studio upload, not API publish, to test cold-start signal).

Run:
  /home/cryptocardhub/video-generator/venv/bin/python scripts/render-trailer.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VG = ROOT / "video-generator"
sys.path.insert(0, str(VG))

# --- Hand-crafted trailer script ----------------------------------------------
# 38-second target. Read aloud at -5% rate the line count below should hit
# ~80-95 words = ~36-40s. Hook-value-CTA pattern.

TRAILER_SCRIPT = {
    "hook": "Every crypto card promises cashback.",
    "body": (
        "Most lie. Some lock you in a token that crashes. "
        "Others bury fees in fine print. We test all of them. "
        "ether-fi-cash, Crypto-com, Coinbase, RedotPay, Gnosis Pay, Wirex, "
        "Bybit, Nexo. We rate cashback math, FX fees, custody, country gates. "
        "Every card, every week. So you don't waste rewards on the wrong card."
    ),
    "cta": "Subscribe at DefyCard for daily breakdowns.",
    "voiceover": (
        "Every crypto card promises cashback. Most lie. "
        "Some lock you in a token that crashes. Others bury fees in fine print. "
        "We test all of them. ether-fi-cash, Crypto dot com, Coinbase, "
        "RedotPay, Gnosis Pay, Wirex, Bybit, Nexo. "
        "We rate cashback math, FX fees, custody, country gates. "
        "Every card, every week. So you don't waste rewards on the wrong card. "
        "Subscribe at defycard.com for daily breakdowns."
    ),
    "visual_cues": [
        {"t": 0.0,  "scene": "credit_card_payment"},
        {"t": 6.0,  "scene": "stock_chart_red"},
        {"t": 11.0, "scene": "fine_print_document"},
        {"t": 15.0, "scene": "crypto_card_stack"},
        {"t": 22.0, "scene": "world_map_finance"},
        {"t": 28.0, "scene": "coffee_payment_contactless"},
        {"t": 34.0, "scene": "phone_screen_app"},
    ],
    "hashtags": {
        "youtube_visible": [
            "#CryptoCard", "#DeFi", "#Cashback", "#etherFi",
            "#CryptoNews", "#Shorts", "#CryptoCom",
        ],
        "youtube_api": [
            "crypto card", "best crypto card", "cashback crypto", "DeFi",
            "ether.fi", "crypto.com card", "RedotPay", "non-custodial card",
            "stablecoin card", "crypto credit card", "crypto debit card",
            "card review", "shorts", "DefyCard",
        ],
    },
    "thumbnail_hook": "EVERY CARD | TESTED",
    "_creative_patterns": {
        "hook_type": "hook_problem_statement",
        "format": "format_channel_intro",
        "audio_strategy": "audio_voiceover_authoritative",
        "pacing": "pacing_steady_no_micro_change",
        "duration_band": "duration_trailer_35_45",
        "visual_style": "visual_mixed_brand_overlay",
        "cta_bridge": "cta_explicit_subscribe",
    },
    "_creative_phase": "0_forced_exploration",
}

TRAILER_TOPIC = {
    "id": 36,
    "class": "TRAILER",
    "category": "channel-intro",
    "primary": "DefyCard channel intro",
    "title": "Every crypto card tested — DefyCard intro",
    "platform": "youtube",
    "platforms": ["youtube"],
    "target_seconds": 38.0,
    "target_words": 90,
    "duration_band": [35, 45],
    "viral_potential": "n/a",
    "cta_bridge_difficulty": 1,
}


def main() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = VG / "output" / "youtube" / today / "trailer"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[trailer] output dir: {out_dir}")

    # Persist script + topic for downstream use + audit
    (out_dir / "script.json").write_text(
        json.dumps(TRAILER_SCRIPT, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "topic.json").write_text(
        json.dumps(TRAILER_TOPIC, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 3. TTS — Edge with WordBoundary alignment
    from pipeline import tts_edge, fetch_visuals, fetch_music, render, captions
    voice_path = out_dir / "voice.mp3"
    print("[trailer] step: TTS (Microsoft Edge, en-US-GuyNeural, -5%)")
    tts_edge.synthesize(
        TRAILER_SCRIPT["voiceover"], "youtube", voice_path, dry_run=False
    )

    # 4. Visuals — broader brand-relevant queries (not travel-specific)
    print("[trailer] step: fetch visuals (8 frames, brand-finance theme)")
    media = fetch_visuals.fetch(
        "crypto card payment finance",
        want=8,
        out_dir=out_dir / "media",
        dry_run=False,
    )

    # 5. Music — Jamendo CC-BY ducked
    music_path = out_dir / "music.mp3"
    print("[trailer] step: fetch music (CC-BY)")
    fetch_music.fetch(
        music_path, target_seconds=int(TRAILER_TOPIC["target_seconds"]),
        dry_run=False, day_seed=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )

    # 6. Captions + render
    print("[trailer] step: align captions via WordBoundary sidecar")
    words = captions.align(voice_path, TRAILER_SCRIPT["voiceover"])
    ftc_overlay = VG / "assets" / "disclaimers" / "ftc_overlay.png"
    final_path = out_dir / "final.mp4"
    print("[trailer] step: render mp4 (1080x1920 @ 30fps)")
    render.render(
        voiceover_path=voice_path,
        music_path=music_path,
        clips=media,
        words=words,
        ftc_overlay_path=ftc_overlay if ftc_overlay.exists() else None,
        target_seconds=TRAILER_TOPIC["target_seconds"],
        out_path=final_path,
        platform="youtube",
    )
    print(f"[trailer] DONE: {final_path}")
    print(f"[trailer] size: {final_path.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
