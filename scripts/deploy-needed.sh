#!/usr/bin/env bash
# exit 0 if any site INPUT is newer than the last successful deploy.
#
# T-0143: the old gate compared two of the site's ~dozen inputs against
# dist/index.html — any pnpm build on the side (or a red deploy gate, which
# also rebuilds dist) silently muted the next tick. This gate reads INPUTS
# against a stamp that only moves on a verified-good deploy (scripts/deploy.sh,
# "smoke passed" branch). The find list below is literal and IS the
# documentation of "what changes the site" — dist/, node_modules/, .astro are
# deliberately absent so a build on the side can never mute this again.
ROOT=${ROOT:-/home/cryptocardhub}
STAMP=${STAMP:-$ROOT/logs/deploy/last-success.stamp}
[ -e "$STAMP" ] || exit 0          # never deployed via stamp -> deploy
cd "$ROOT"
find seo-site/src seo-site/public seo-site/scripts seo-site/redirects.map \
     seo-site/astro.config.mjs seo-site/package.json \
     config scripts/build-changelog.py scripts/build-markdown.js \
     scripts/sync-yt-videos.py video-generator/state/published.db \
     logs/intel/changelog.jsonl .env \
     -newer "$STAMP" -print -quit 2>/dev/null | grep -q .
