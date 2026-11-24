---
name: defycard-design
description: DefyCard (@DefyCard) Shorts — new 50+50 rotating design (HTML caption-overlay over b-roll + custom previews). Load when working on the DefyCard video look.
user-invocable: false
---

# DefyCard — new design engine (2026-06-22)

> **Scope note (Y52, 2026-08-11).** `DEFY_NEW_DESIGN` is a **Shorts-only** skin, but the flag used to
> be read inside `render.py`, where it suppressed burned captions for *every* caller while only
> `main.py`'s YouTube branch added them back. The long-form pilot (`pipeline/longform.py`, born after
> this skill) calls `render()` directly and shipped five videos with no captions at all; Instagram and
> TikTok were the same mine, asleep behind disabled flags. The flag is now a parameter —
> `render(..., burn_captions=...)`, default `True` — and `main.py` opts out only where step 6b really
> runs. **Nothing in this engine changed**: `render_overlay.mjs`, the 50-style rotation and the Shorts
> output are byte-identical. If you extend the overlay to a new format, pass `burn_captions=False`
> from that caller yourself. Gate: `tests/test_captions_visible.py`.

DefyCard Shorts keep their concept (b-roll crypto-card footage + edge-tts voice + music + FTC banner +
"defycard.com" CTA). We added a **colorful rotating skin**: 50 caption-overlay styles + 50 custom previews from
claude.ai/design, randomly rotated (no-repeat 1..50, then recycle), nonstop. Lives in
`~/video-generator/defy_design/`.

## Files
- `engine/defycard-video-styles.js` — `window.VIDEO_STYLES` (50) + `renderStyle(spec,opts)` → transparent
  1080×1920 overlay. STATELESS + no CSS animation (frame-renderable by `activeIndex`). spec:
  {index,hook,activeIndex,phase:'caption'|'cta',showFTC}. Captions(karaoke)/badge/motion/frame/brand-chip/flash.
- `engine/defycard-previews.js` — `window.PREVIEW_TEMPLATES` (50) + `renderPreviewHTML(spec)` → 1080×1920 poster.
  Honors {{HOOK}}+{{STAT}}, DefyCard mark + defycard.com CTA.
- `render_overlay.mjs` — chunks `voice.timestamps` words → ~3-word karaoke phrases → transparent overlay PNG seq
  (playwright bundled chromium) → ffmpeg-composites onto the b-roll. `--captions-only` keeps FTC+CTA in render.py.
  `--style auto` = no-repeat rotation (`.rotation_vid.json`). Auto-probes duration from `--video`. ~25s/video.
- `render_preview.mjs` — `--style auto` (`.rotation_prev.json`) + `--hook` → poster screenshot → thumbnail.

## Integration (env flag `DEFY_NEW_DESIGN=1`, set in ~/.env → loaded by the slot-watcher cron)
- `pipeline/render.py`: when flag set, SKIPS the ASS caption burn (keeps FTC overlay + CTA drawtext). `import os`.
- `pipeline/main.py` step 6b: composites the overlay onto `final.mp4` (rotated style) + renders a rotated preview
  → passes `thumbnail_path=` to `pub_yt.upload` (uploader already supported it; main.py never set it before).
  Best-effort (failures non-fatal). Backups `*.bak-defy`.
- `pipeline/fetch_visuals.py`: providers now wrapped in `_safe()` — a slow/blocked source (Unsplash timeout) no
  longer aborts the whole fetch when Pexels already has clips. (This was silently killing ALL video production.)

## THE LAW (per Short)
Data correct (hook/topic match the video) · captions burned via overlay, no overflow (engine ≥60px safe) ·
1080×1920 · DIFFERENT (rotation cycles all 50 then restarts, independent for video + preview) · concept/format/
footage/voice/FTC/CTA UNCHANGED — skin only.

## Test / verify
Standalone overlay: `node defy_design/render_overlay.mjs --style 12 --words <T>/voice.timestamps.json --fps 30 \
  --outdir /tmp/ovl --captions-only --video <broll.mp4> --out /tmp/out.mp4`. Preview:
`node defy_design/render_preview.mjs --style auto --hook "SPEND CRYPTO ANYWHERE" --out /tmp/p.png`.
Full pipeline test (publishes): `set -a; . ~/.env; set +a; DEFY_NEW_DESIGN=1 venv/bin/python -m pipeline.main
--platform=youtube`. First live new-design publish 2026-06-22: style 12, https://youtu.be/7aryCQFCFH4 .
Needs the `claude` CLI login alive (write_script) — if 401, copy a refresh-capable sibling token (polymarket).
