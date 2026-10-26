#!/usr/bin/env bash
# backup-daily.sh — E-04: daily tar.gz of pSEO content + config + video state,
# verified, with a 14-day local rotation. Not wired into crontab by this
# script — crontab is off-limits to Fleet (sandbox rule); the line to add is
# in the Runbook in PROGRESS.md#T-w0-04.
#
# ROOT/BACKUP_DIR/LOG_DIR are overridable by env for testing against a temp
# tree (scripts/tests/test_backup_daily.py) — the real cron run uses the
# defaults untouched.
#
# Exits: 1 tar create failed · 2 tar verify failed
set -uo pipefail

ROOT=${ROOT:-/home/cryptocardhub}
BACKUP_DIR=${BACKUP_DIR:-$ROOT/backups}
LOG_DIR=${LOG_DIR:-$ROOT/logs/backup}
mkdir -p "$BACKUP_DIR" "$LOG_DIR"

STAMP=$(date -u +%Y%m%dT%H%MZ)
ARCHIVE="$BACKUP_DIR/daily-$STAMP.tar.gz"
LOG="$LOG_DIR/$(date -u +%Y-%m-%d).jsonl"

log() {  # log <step> <success:true|false> <extra_json_fields, starting with a comma>
  printf '{"ts":"%s","step":"%s","success":%s%s}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" "$3" >> "$LOG"
}

cd "$ROOT" || exit 1

T0=$(date +%s)
if ! tar -czf "$ARCHIVE" seo-site/src/content/pseo config video-generator/state \
     2>/tmp/backup-daily-create.err; then
  log create false ",\"duration_s\":$(($(date +%s) - T0)),\"stderr_file\":\"/tmp/backup-daily-create.err\""
  echo "ERROR: tar create failed — see /tmp/backup-daily-create.err and $LOG" >&2
  exit 1
fi
SIZE=$(stat -c%s "$ARCHIVE" 2>/dev/null || stat -f%z "$ARCHIVE")
log create true ",\"duration_s\":$(($(date +%s) - T0)),\"path\":\"$ARCHIVE\",\"size_bytes\":$SIZE"

T1=$(date +%s)
if ! tar -tzf "$ARCHIVE" >/dev/null 2>/tmp/backup-daily-verify.err; then
  log verify false ",\"duration_s\":$(($(date +%s) - T1)),\"path\":\"$ARCHIVE\",\"stderr_file\":\"/tmp/backup-daily-verify.err\""
  echo "ERROR: tar verify failed for $ARCHIVE — see /tmp/backup-daily-verify.err and $LOG" >&2
  exit 2
fi
log verify true ",\"duration_s\":$(($(date +%s) - T1)),\"path\":\"$ARCHIVE\""

# 14-day local rotation. -mtime +14 = strictly older than 14 days, so an
# archive is kept through its 14th day and removed once a 15th accumulates.
DELETED=$(find "$BACKUP_DIR" -maxdepth 1 -name 'daily-*.tar.gz' -mtime +14 -print -delete | wc -l | tr -d ' ')
log rotate true ",\"deleted\":$DELETED"

echo "OK: $ARCHIVE ($SIZE bytes), $DELETED old archive(s) rotated out"
