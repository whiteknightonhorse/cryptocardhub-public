# DefyCard Video Generator

Crypto-card affiliate Shorts/Reels/TikTok pipeline. Three platform-aware skills (YouTube1, Instagram1, TikTok1) share this single Python codebase. See `.claude/skills/{youtube1,instagram1,tiktok1}/SKILL.md` for the full design.

## Quick start

```bash
cd /home/cryptocardhub/video-generator
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Drop the Anton font in
mkdir -p assets/fonts
# (download Anton-Regular.ttf from Google Fonts → assets/fonts/)

# Dry-run (no API keys needed; mocks everything; produces a valid mp4)
python -m pipeline.main --platform=youtube --dry-run --topic-id=27
```

The dry-run writes:

```
output/youtube/<date>/topic_27/
  ├── script.json     (Claude writer mock)
  ├── voice.mp3       (1 s silence placeholder)
  ├── music.mp3       (silent-stereo placeholder)
  ├── media/          (5 placeholder JPEGs)
  ├── final.mp4       (1080x1920 H.264 yuv420p 30 fps with burned captions)
  ├── final.ass       (subtitle file)
  └── summary.json
```

## Pipeline (7 steps)

```
fetch_topic → write_script → tts_edge → fetch_visuals → fetch_music → render → publish
```

Each step is one module under `pipeline/`. Order is mandated by `/home/cryptocardhub/CLAUDE.md` and MUST NOT be reordered.

## Daily cron

```cron
# 00:05 ET — regenerate schedule for all 3 platforms
5 0 * * *   /home/cryptocardhub/video-generator/cron/00-regenerate-schedule.sh
# Every 10 min — fire any due slot
*/10 * * * * /home/cryptocardhub/video-generator/cron/10-slot-watcher.sh
```

Both scripts read `/home/cryptocardhub/.env`, then bail out fast if `VIDEO_GENERATOR_ENABLED!=true`. Operator flips the env flag after 10+ TG-preview-approved videos pass review.

## Operator handoff

Per skill checklists. New YT/IG/TikTok accounts → `.env` keys → flip enabled. See `.claude/skills/youtube1/SKILL.md §18`, etc.

## Cost gates (HARD — do not regress)

- Writer model is `claude-haiku-4-5-20251001`. `assert "haiku" in WRITER_MODEL` runs at module load.
- `--fallback-model claude-haiku-4-5-20251001 --no-session-persistence` on every `claude --print` call.
- Anti-pattern hooks at `.claude/hooks/*.sh` will fire if any of the above is missing.

## Source skills (read-only references)

Polymarket has 7 production-tested video skills. We never modify them; we copy and adapt. Adaptation is logged in each `SKILL.md §17 Iteration Log`.

| Source | Adapted into |
|---|---|
| `…/video-generator/SKILL.md` (v10 anchor) | `pipeline/render.py`, FFmpeg invariants, audio mix |
| `…/video-generator2/SKILL.md` (IT-10/14/15) | `pipeline/hashtag_strategy.py`, `pipeline/thumbnail.py`, B-roll branch |
| `…/video-generator4/SKILL.md` (sports shorts) | `pipeline/fetch_visuals.py` (Cloudflare-safe UA), `pipeline/tts_edge.py` (15-voice pool), 5 dynamic effects |
| `…/video-generator5/SKILL.md` (ch3 shorts) | `pipeline/render.py` (mixed video/photo branch, `max(38, tts+0.5)` duration) |
| `…/youtube-longform/SKILL.md` (4 channels) | `pipeline/publish/youtube.py` (Credentials no-scopes, tag wire-cost), `pipeline/dedup.py` (title-prefix dedup) |
| `…/ai-debates/SKILL.md` (debate channel) | `pipeline/compliance.py` (3-stage moderation, banned vocab, mandatory disclaimers) |

## Status

Phase 0 — code + skills shipped. No live publishing yet (waiting for operator's new YT/IG/TT accounts).
