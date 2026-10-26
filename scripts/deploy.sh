#!/usr/bin/env bash
# deploy.sh — atomic deploy to https://defycard.com nginx root.
#
#   1. pnpm build (Astro -> dist/)
#   2. Post-build sed: replace <<AFFILIATE_URL>> placeholder with PUBLIC_ETHERFI_REF_URL
#      (single source of truth = .env; URL change = re-deploy, no re-generate)
#   3. rsync dist/ → /var/www/defycard.com/html/
#   4. chown www-data + nginx reload
#   5. Run scripts/verify-deploy.sh smoke
#
# Exits non-zero on any failure (no partial deploy).
set -euo pipefail

ROOT=/home/cryptocardhub
SEO=$ROOT/seo-site
WEB=/var/www/defycard.com/html

# T-0143 point 4: a red deploy must not go silent. deploy-needed.sh retries
# hourly since the stamp only moves on success (below), but nobody reads a
# retry loop's log — this is the line that surfaces it. $BASH_COMMAND at EXIT
# time is the command that was running when the script stopped, which is the
# closest thing to a step name without hand-labeling every stage.
_alert_deploy_failure() {
  local rc=$?
  [ "$rc" -eq 0 ] && return
  python3 -c '
import sys
sys.path.insert(0, "/home/cryptocardhub/scripts")
import telegram_lib as tg
tg.alert(f"[deploy][FAILED] rc={sys.argv[1]} step={sys.argv[2]}")
' "$rc" "$BASH_COMMAND" 2>/dev/null || true
}
trap _alert_deploy_failure EXIT

# Load .env
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck source=/dev/null
  . "$ROOT/.env"
  set +a
fi

AFFIL="${PUBLIC_ETHERFI_REF_URL:-${ETHERFI_REF_URL:-https://www.ether.fi/@defycard}}"

# Validate scheme + ban any char that could escape sed delimiter or HTML
# context (council round 2 added | / & to the ban list to prevent sed
# delimiter injection).
if [[ "$AFFIL" != https://* && "$AFFIL" != http://* ]]; then
  echo "ERROR: ETHERFI_REF_URL must be http(s) URL (got: $AFFIL)" >&2
  exit 2
fi
if [[ "$AFFIL" == *\"* || "$AFFIL" == *\<* || "$AFFIL" == *\>* \
   || "$AFFIL" == *\|* || "$AFFIL" == *$'\001'* || "$AFFIL" == *\\* \
   || "$AFFIL" == *\&* ]]; then
  echo "ERROR: ETHERFI_REF_URL contains unsafe chars (\" < > | \\ & SOH)" >&2
  exit 2
fi

echo "==> [0/5] sync YT publish_log → seo-site/src/content/videos/"
# Bridges video-generator/state/published.db -> Astro content collection
# so every build picks up newly-published shorts (videos page + sitemap).
# IMPORTANT: must use the venv python — system python3 lacks google-auth /
# googleapiclient and falls through to "skip every video as deleted",
# emptying the videos collection. (Burned operator 2026-05-08; see Y36.)
PYBIN=/home/cryptocardhub/video-generator/venv/bin/python
"$PYBIN" "$ROOT/scripts/sync-yt-videos.py" 2>&1 | tail -20 || true

echo ""
echo "==> [0b/5] build-changelog.py (ether.fi Cash changelog page, or nothing)"
# E-16: the one /learn/ether-fi-cash-changelog/ page, rebuilt from
# logs/intel/changelog.jsonl. Must run before the build below (it writes a
# content/pseo source file Astro reads); non-fatal like the sync above — a
# failure here must not block the rest of the site from deploying.
"$PYBIN" "$ROOT/scripts/build-changelog.py" --write 2>&1 | tail -20 || true

echo ""
echo "==> [1/5] pnpm build"
cd "$SEO"
pnpm build 2>&1 | tail -20

echo ""
echo "==> [1b/5] build-video-sitemap.py (sitemap-videos.xml + index merge)"
"$PYBIN" "$ROOT/scripts/build-video-sitemap.py" 2>&1 | tail -10 || true

echo ""
echo "==> [1c/5] build-markdown.js (markdown sidecars + llms.txt + /sitemap.xml)"
# MUST run after build-video-sitemap.py: it copies the finished sitemap index
# to /sitemap.xml (AEO-6), and the index only lists the video sub-sitemap once
# the step above has added it.
node scripts/build-markdown.js

echo ""
echo "==> [2/5] post-build sed: <<AFFILIATE_URL>> -> $AFFIL"
# Escape sed delimiters in the URL (shouldn't happen with @ handle but defensive)
ESC_AFFIL=$(printf '%s\n' "$AFFIL" | sed 's:[\/&]:\\&:g')
HIT_COUNT=$(grep -rl '<<AFFILIATE_URL>>' "$SEO/dist" 2>/dev/null | wc -l || echo 0)
find "$SEO/dist" -type f \( -name '*.html' -o -name '*.xml' \) -print0 \
  | xargs -0 -r sed -i "s|<<AFFILIATE_URL>>|$ESC_AFFIL|g"
echo "    sed touched $HIT_COUNT files"
# Y50 P0-1: check the PREFIX, not the exact bare token. The tagged form
# <<AFFILIATE_URL:etherfi>> is resolved by the remark plugin and never by this
# sed; grepping only for '<<AFFILIATE_URL>>' would let an unresolved tagged
# placeholder ship to production as visible literal text.
LEFT=$(grep -rl '<<AFFILIATE_URL' "$SEO/dist" 2>/dev/null | wc -l || echo 0)
if [ "$LEFT" -ne 0 ]; then
  echo "ERROR: $LEFT files still contain an unresolved <<AFFILIATE_URL...>> placeholder" >&2
  grep -rl '<<AFFILIATE_URL' "$SEO/dist" 2>/dev/null | head -5 >&2
  exit 3
fi

echo ""
echo "==> [2b/5] internal-link gate (G20) — reads the built dist, blocks the deploy"
# 117 dead targets and 218 dead links once reached production because nothing
# between the writer and the network looked at what was being shipped. This is
# that look. A red gate here means no rsync: the live site keeps the last good
# copy rather than gaining a fresh crop of dead ends.
if ! "$PYBIN" -m pytest "$SEO/tests/test_internal_links.py" -q; then
  echo "ERROR: internal-link gate failed — refusing to deploy" >&2
  exit 4
fi

echo "==> [2c/5] rendered-output gate (G34) — no markup reaches the reader"
# 544 of 782 pages once served raw markdown — 4,272 literal ** and 1,300 literal
# [text](url) — from four separate causes, and nothing was watching the symptom.
# Placeholders and the eth.fi typo are zero-tolerance; the emphasis-inside-a-
# label residue is a NAMED list in the test that may only shrink.
if ! "$PYBIN" -m pytest "$SEO/tests/test_rendered_output.py" -q; then
  echo "ERROR: rendered-output gate failed — refusing to deploy" >&2
  exit 5
fi

echo "==> [2d/5] partner-link gate (G12/Y50) — no reader is sent to a card they cannot get"
# The money bug: geo pages for jurisdictions where ether.fi Cash cannot be
# issued rendered ether.fi as their ONLY external card link. It lived between
# the markdown and the HTML, so only the built dist shows it — which is why the
# gate runs here, on the artifact, and not in the writer.
if ! "$PYBIN" -m pytest "$SEO/tests/test_partner_links.py" -q; then
  echo "ERROR: partner-link gate failed — refusing to deploy" >&2
  exit 6
fi

echo "==> [2e/5] url-retirement gate (G37) — no URL the live sitemap names becomes a bare 404"
# A URL once in the live sitemap is remembered by crawlers. The previous copy of
# the site (LIVE_ROOT) is the ledger: its sitemaps vs the new dist + redirects.map.
# A first deploy needs URL_RETIREMENT_FIRST_DEPLOY=1 set BY HAND — never here.
if ! LIVE_ROOT="$WEB" "$PYBIN" -m pytest "$SEO/tests/test_url_retirement.py" -q; then
  echo "ERROR: url-retirement gate failed — refusing to deploy" >&2
  exit 7
fi

echo ""
# EVERY gate above this line runs BEFORE the renames below. They used to sit
# after them: the deploy was already live by the time a red gate "blocked" it,
# and the exit left the bad copy serving with no rollback. A gate whose failure
# cannot change what is served is a log line, not a gate.
echo "==> [3/5] atomic deploy (rsync to staging + rename)"
WEB_NEW="${WEB}-new"
WEB_OLD="${WEB}-old"
sudo rm -rf "$WEB_NEW"
sudo rsync -a "$SEO/dist/" "$WEB_NEW/"
sudo chown -R www-data:www-data "$WEB_NEW"
# Two atomic renames — no 404 window. Live root never points at a partial copy.
# Keep $WEB_OLD until smoke test passes (rollback path per council round 3).
sudo rm -rf "$WEB_OLD"
# First-deploy guard: if $WEB doesn't exist, skip the old-root rename
# (council round 4 finding — would otherwise leave site with no web root).
if [ -e "$WEB" ]; then
  sudo mv -Tf "$WEB" "$WEB_OLD"
fi
sudo mv -Tf "$WEB_NEW" "$WEB"

echo ""
echo "==> [3b/5] render redirects.map -> nginx snippet"
# Y61: five URLs that never existed are already in crawler memory (they were
# hardcoded into the writer prompt as "realistic targets"). Repairing the
# internal links stops new hits; only a 301 honours the memory that exists.
# The map lives in the repo so the rule is reviewable; nginx -t below is the
# gate — a malformed map fails the deploy instead of the site.
SNIP=/etc/nginx/snippets/defycard-redirects.conf
{
  echo "# generated by deploy.sh from seo-site/redirects.map — do not edit by hand"
  while read -r from to; do
    case "$from" in \#*|"") continue ;; esac
    [ -n "$to" ] || continue
    case "$to" in
      410) printf 'location = %s { return 410; }\n' "$from" ;;
      *)   printf 'location = %s { return 301 %s; }\n' "$from" "$to" ;;
    esac
  done < "$SEO/redirects.map"
} > /tmp/defycard-redirects.conf
sudo mkdir -p /etc/nginx/snippets
sudo cp /tmp/defycard-redirects.conf "$SNIP"
echo "    $(grep -c "^location" /tmp/defycard-redirects.conf) redirect(s) installed"

echo ""
echo "==> [4/5] nginx reload"
sudo nginx -t 2>&1 | tail -3
sudo systemctl reload nginx

echo ""
echo "==> [5/5] smoke test (with rollback on failure)"
if bash "$ROOT/scripts/verify-deploy.sh"; then
  # All-good path: drop the kept-around old root
  sudo rm -rf "$WEB_OLD"
  echo ""
  echo "==> [5b/5] IndexNow: submit CHANGED URLs to Bing (AEO-7)"
  # After verify, never before: we only announce pages that are actually live.
  # Failure here is not a deploy failure — the site is already up.
  "$PYBIN" "$ROOT/scripts/indexnow-ping.py" || echo "WARN: IndexNow ping failed (non-fatal)"

  # T-0143 point 1: success stamp for scripts/deploy-needed.sh. Only written
  # on this branch (smoke passed) — a failed deploy never moves it, so
  # deploy-needed.sh keeps saying "deploy" every hour until one actually works.
  mkdir -p "$ROOT/logs/deploy"
  touch "$ROOT/logs/deploy/last-success.stamp"

  echo ""
  echo "✓ deploy complete: https://defycard.com"
else
  # Roll back: swap old root back into place (if there was one)
  echo "" >&2
  echo "ERROR: smoke test failed — ROLLING BACK to previous root" >&2
  sudo mv -Tf "$WEB" "$WEB_NEW"          # park bad version
  if [ -d "$WEB_OLD" ]; then
    sudo mv -Tf "$WEB_OLD" "$WEB"        # restore previous
    sudo systemctl reload nginx || true
    sudo rm -rf "$WEB_NEW"               # clean up bad version
    echo "ROLLED BACK: $WEB now points at the pre-deploy state" >&2
  else
    # First deploy — no previous root to restore. Leave bad version
    # parked at $WEB_NEW so operator can inspect.
    echo "WARN: first-deploy failure — bad root parked at $WEB_NEW" >&2
    echo "WARN: $WEB is empty; manual fix required" >&2
  fi
  exit 4
fi
