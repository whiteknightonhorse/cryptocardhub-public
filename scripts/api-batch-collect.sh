#!/usr/bin/env bash
# API-CREDITS-1108/3b collector tick (cron */15): finish ended batch-lane results for the three jobs.
# Empty state = no network call. Env (telegram etc.) comes from the cron line, as for every other job.
cd "$(dirname "$0")/.." || exit 1
rc=0
python3 scripts/page-updates.py --collect || rc=1
python3 scripts/intel-ingest.py --collect || rc=1
python3 scripts/humanize_gate.py --collect || rc=1
exit $rc
