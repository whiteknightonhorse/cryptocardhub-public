#!/usr/bin/env bash
[ -f $HOME/.claude/oauth.env ] && . $HOME/.claude/oauth.env
# Every 10 min — fire any due slot for any ENABLED platform.
#
# Per platform enable flags (in /home/cryptocardhub/.env):
#   VIDEO_GENERATOR_ENABLED=true            # master switch (default false = paused)
#   VG_YT_ENABLED=true                      # per-platform toggle (default false until creds wired)
#   VG_IG_ENABLED=true                      # idem
#   VG_TT_ENABLED=true                      # idem
#
# Per shorts-analysis SKILL §17 — videos are auto-generated, scored, and
# pattern weights updated nightly by analytics.py.

set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE=${VG_ENV_FILE:-/home/cryptocardhub/.env}
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck source=/dev/null
  . "$ENV_FILE"
  set +a
fi

# Isolate from API path — generator uses Max plan via `claude --print`.
unset ANTHROPIC_API_KEY

# Master kill-switch
if [ "${VIDEO_GENERATOR_ENABLED:-false}" != "true" ]; then
  echo "[slot-watcher] VIDEO_GENERATOR_ENABLED!=true — skip ($(date -u +%FT%TZ))"
  exit 0
fi

PYBIN=${VG_PYBIN:-/home/cryptocardhub/video-generator/venv/bin/python}
LOG_DIR=${VG_LOG_DIR:-/home/cryptocardhub/logs/video}
mkdir -p "$LOG_DIR"

# Per-platform check + fire (each gated by its own _ENABLED flag).
# Env vars use 2-letter platform codes (YT/IG/TT), NOT first 2 letters of
# platform name. Map explicitly — the previous `${platform:0:2}` derivation
# produced VG_YO/IN/TI which silently never matched, leaving the watcher
# always-skip after master switch passed (overnight 2026-05-06 dead).
for platform in youtube instagram tiktok; do
  case "$platform" in
    youtube)   flag_var=VG_YT_ENABLED ;;
    instagram) flag_var=VG_IG_ENABLED ;;
    tiktok)    flag_var=VG_TT_ENABLED ;;
    *) echo "[slot-watcher] unknown platform '$platform' ($(date -u +%FT%TZ))"; continue ;;
  esac
  flag_val=${!flag_var:-false}
  if [ "$flag_val" != "true" ]; then
    continue
  fi
  # Circuit breaker (P0-4): while the channel is suppressed (14-day median
  # views < 10, or no data at all) only 1 slot/day gets through, with a
  # Telegram alert. Never a silent skip. Can only take slots away — the
  # master switch above is what enables publishing.
  if ! "$PYBIN" -m pipeline.analytics --cadence-gate --platform="$platform" \
       >> "$LOG_DIR/cadence.log" 2>&1; then
    echo "[slot-watcher] $platform suppressed by cadence gate — skip ($(date -u +%FT%TZ))"
    continue
  fi
  echo "[slot-watcher] check $platform ($(date -u +%FT%TZ))"
  "$PYBIN" -m pipeline.main --platform="$platform" --check-slot \
    >> "$LOG_DIR/${platform}.log" 2>&1 || true
done
