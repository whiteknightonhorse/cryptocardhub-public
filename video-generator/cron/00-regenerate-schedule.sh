#!/usr/bin/env bash
[ -f $HOME/.claude/oauth.env ] && . $HOME/.claude/oauth.env
# 00:05 ET daily — regenerate per-platform 6-slot schedules.
#
# Slots are computed per platform with random distribution across 09:00-21:00 ET
# avoiding 20:00 (finance prime suppression). Per platform-specific prime windows
# from competitor-analysis §4.5.
#
# All 3 platforms get a schedule file regardless of enable flags — slot-watcher
# skips disabled platforms at fire time.

set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f /home/cryptocardhub/.env ]; then
  set -a
  # shellcheck source=/dev/null
  . /home/cryptocardhub/.env
  set +a
fi

PYBIN=/home/cryptocardhub/video-generator/venv/bin/python
LOG_DIR=/home/cryptocardhub/logs/video
mkdir -p "$LOG_DIR"

"$PYBIN" - <<'PY' >> "$LOG_DIR/schedule.log" 2>&1
from datetime import datetime, timezone
from pipeline import schedule

slots = {
    "youtube":   schedule.generate("youtube"),
    "instagram": schedule.generate("instagram"),
    "tiktok":    schedule.generate("tiktok"),
}
schedule.write(slots)
ts = datetime.now(timezone.utc).strftime("%FT%TZ")
for p, s in slots.items():
    first = s[0]["due_at_iso"] if s else "(none)"
    print(f"[schedule] {ts} {p}: {len(s)} slots — first {first}")
PY
