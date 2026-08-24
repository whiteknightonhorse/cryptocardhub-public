#!/usr/bin/env bash
[ -f $HOME/.claude/oauth.env ] && . $HOME/.claude/oauth.env
# monthly-rebuild.sh — fire on the 1st of each calendar month at 00:05 ET.
#
# Purpose: keep the homepage hero text fresh.
#   - Astro reads `new Date()` at build time, so the H1 month/year auto-updates.
#   - The lead paragraph rotates from src/data/hero-variants.json by
#     (year*12 + monthIndex0) % variants.length, so each month gets fresh prose.
#
# Cron entry (operator installs once; never auto-installed per CLAUDE.md):
#
#   TZ=America/New_York
#   5 0 1 * *  /home/cryptocardhub/scripts/monthly-rebuild.sh >> /home/cryptocardhub/logs/monthly-rebuild.log 2>&1
#
# Per CLAUDE.md schedule policy + seo2026/SKILL.md §12.4.
# Sends a Telegram alert on failure so the operator notices a stale homepage.

set -euo pipefail

ROOT=/home/cryptocardhub
SEO=$ROOT/seo-site
LOGFILE=$ROOT/logs/monthly-rebuild.log

mkdir -p "$ROOT/logs"

# Load .env (Telegram alert keys + PUBLIC_* env vars)
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck source=/dev/null
  . "$ROOT/.env"
  set +a
fi

stamp() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

tg_alert() {
  local msg="$1"
  if [ -n "${TG_BOT_TOKEN:-}" ] && [ -n "${TG_CHAT_ID:-}" ]; then
    curl -sS -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TG_CHAT_ID}" \
      --data-urlencode "text=[monthly-rebuild] ${msg}" \
      >/dev/null 2>&1 || true
  fi
}

on_fail() {
  local ec=$?
  local msg="FAIL ec=$ec at $(stamp). Homepage may be showing stale month. Check $LOGFILE."
  echo "$msg" >&2
  tg_alert "$msg"
  exit "$ec"
}
trap on_fail ERR

echo "==> [$(stamp)] monthly-rebuild start"
cd "$SEO"

# Verify hero-variants.json is parseable BEFORE rebuilding (no half-broken homepage)
node -e "JSON.parse(require('fs').readFileSync('src/data/hero-variants.json','utf8')).variants.length || (console.error('hero-variants.json: variants[] empty'),process.exit(1))"

# AEO-1: re-check every card's availability list against the issuer BEFORE
# rebuilding, so the matrix ships with a `verified` date that a check actually
# produced. On a mismatch it writes an alert and edits nothing — the weekly
# report surfaces the diff and a human decides what the new fact is.
echo "==> [$(stamp)] availability re-check (issuer vs config/availability.yaml)"
"$ROOT/video-generator/venv/bin/python" "$ROOT/scripts/availability-check.py" 2>&1 | tail -10 \
  || echo "WARN: availability check failed — matrix keeps the older verified date"

echo "==> [$(stamp)] pnpm build"
pnpm build 2>&1 | tail -10

# Sanity check: the freshly-built homepage MUST contain the current month name.
CURRENT_MONTH=$(date '+%B')
if ! grep -q "$CURRENT_MONTH" dist/index.html; then
  echo "ERROR: built dist/index.html does not contain '$CURRENT_MONTH' — refusing to deploy stale build" >&2
  tg_alert "FAIL: built homepage missing current month '$CURRENT_MONTH'. Refused to deploy."
  exit 5
fi
echo "==> [$(stamp)] sanity OK — '$CURRENT_MONTH' present in built homepage"

echo "==> [$(stamp)] deploy"
bash "$ROOT/scripts/deploy.sh" 2>&1 | tail -25

# Confirm live homepage shows the current month after deploy
sleep 2
if curl -sk https://defycard.com/ | grep -q "$CURRENT_MONTH"; then
  echo "==> [$(stamp)] LIVE OK — homepage shows '$CURRENT_MONTH'"
  tg_alert "OK $(stamp): homepage refreshed to '$CURRENT_MONTH'"
else
  echo "WARN: live homepage did not show '$CURRENT_MONTH' after deploy" >&2
  tg_alert "WARN $(stamp): homepage live check did not find '$CURRENT_MONTH' — may be CDN cache; investigate."
fi

echo "==> [$(stamp)] monthly-rebuild done"
