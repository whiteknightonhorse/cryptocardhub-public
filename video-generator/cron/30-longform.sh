#!/usr/bin/env bash
# Y59 — long-form publisher. Tuesday and Friday, 11:00 UTC.
#
#   0 11 * * 2,5 /home/cryptocardhub/video-generator/cron/30-longform.sh
#
# Why 11:00 UTC: the YouTube Data API quota resets at 07:00 UTC and the Shorts
# window opens at 09:00 ET, so a long-form upload (~2100 quota units with its
# thumbnail, pinned comment and caption track) can never be the one that runs
# into an exhausted quota.
#
# Why 2/week: the format has zero publications and therefore zero measurements.
# Scaling before a signal exists is work against an unmeasured variable.
#
# The Shorts cadence gate is deliberately NOT reused here — it measures the
# median views of Shorts, and throttling an unproven different format by
# another format's metric is a meaningless coupling. Long-form has its own
# limiter: at most one publication per UTC day, enforced in publish_next().
set -u

cd /home/cryptocardhub/video-generator || exit 1
set -a; . /home/cryptocardhub/.env 2>/dev/null; set +a
# Isolate from the API path — the generator uses the Max plan via `claude --print`.
unset ANTHROPIC_API_KEY

LOG_DIR=${VG_LOG_DIR:-/home/cryptocardhub/logs/video}
mkdir -p "$LOG_DIR"
ts() { date -u +%FT%TZ; }

# Master kill-switch, then the format's own flag. Explicit names, never derived
# from a substring: a derived flag name silently never matched for a whole
# night once (Y32-Y34) and the watcher looked healthy while publishing nothing.
if [ "${VIDEO_GENERATOR_ENABLED:-false}" != "true" ]; then
  echo "[longform] VIDEO_GENERATOR_ENABLED!=true — skip ($(ts))"
  exit 0
fi
if [ "${VG_LF_ENABLED:-false}" != "true" ]; then
  echo "[longform] VG_LF_ENABLED!=true — skip ($(ts))"
  exit 0
fi

PYBIN=${VG_PYBIN:-/home/cryptocardhub/video-generator/venv/bin/python}
echo "[longform] publishing next ($(ts)), privacy=${VG_LF_PRIVACY:-unlisted}"
"$PYBIN" -m pipeline.longform --publish-next
rc=$?
echo "[longform] exit rc=$rc ($(ts))"
exit $rc
