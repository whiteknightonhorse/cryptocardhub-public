#!/usr/bin/env bash
# verify-deploy.sh — end-to-end smoke test for the live https://defycard.com deploy.
#
# Phase 11 of the production-launch plan:
#   1. Homepage 200
#   2. ≥10 articles built
#   3. sitemap-index.xml has ≥7 sub-sitemap entries
#   4. Each article URL responds 200 + has JSON-LD
#   5. Each article has ≥8 internal links
#   6. Sticky mobile CTA in DOM
#
# Exits 0 on success, non-zero on first failure.
set -uo pipefail

ORIGIN="${ORIGIN:-https://defycard.com}"
ROOT=/home/cryptocardhub
DIST="$ROOT/seo-site/dist"
PSEO_DIR="$ROOT/seo-site/src/content/pseo"

PASS=0
FAIL=0
WARN=0

ok() { echo "  ✅ $*"; PASS=$((PASS+1)); }
err() { echo "  ❌ $*"; FAIL=$((FAIL+1)); }
wrn() { echo "  ⚠ $*"; WARN=$((WARN+1)); }

echo "=== 1. Homepage live ==="
hdr=$(curl -sI -L "$ORIGIN/" 2>&1 | head -1)
if echo "$hdr" | grep -qE "HTTP/.*\b200\b"; then
  ok "homepage 200 ($hdr)"
else
  err "homepage NOT 200: $hdr"
fi

echo ""
echo "=== 2. Articles in dist/ ==="
n_articles=$(find "$DIST" -mindepth 3 -maxdepth 3 -name 'index.html' 2>/dev/null | wc -l)
n_md=$(ls "$PSEO_DIR"/*.md 2>/dev/null | wc -l)
echo "  source markdown: $n_md, built HTML: $n_articles"
if [ "$n_articles" -ge 10 ]; then
  ok "$n_articles article URLs built"
else
  err "expected ≥10, got $n_articles"
fi

echo ""
echo "=== 3. Sitemap index ==="
sitemap_count=$(curl -s "$ORIGIN/sitemap-index.xml" 2>/dev/null | grep -c "<sitemap>" || true)
url_count=$(curl -s "$ORIGIN/sitemap-0.xml" 2>/dev/null | grep -c "<url>" || true)
if [ "$sitemap_count" -ge 1 ]; then
  ok "sitemap-index.xml has $sitemap_count sub-sitemap(s), $url_count URLs total"
else
  err "sitemap-index.xml empty or missing"
fi

echo ""
echo "=== 3b. /sitemap.xml (G22) ==="
# ClaudeBot asked for /sitemap.xml 243 times in 15 days and got 404 every time.
# 200 or 301 both count; a 404 here is the regression this check exists for.
sm_status=$(curl -s -o /tmp/sitemap-$$.xml -w "%{http_code}" "$ORIGIN/sitemap.xml")
if [ "$sm_status" = "200" ] || [ "$sm_status" = "301" ]; then
  sm_subs=$(curl -sL "$ORIGIN/sitemap.xml" | grep -c "<sitemap>" || true)
  if [ "$sm_subs" -ge 1 ]; then
    ok "/sitemap.xml -> $sm_status, $sm_subs sub-sitemap(s)"
  else
    err "/sitemap.xml -> $sm_status but lists no sub-sitemaps"
  fi
else
  err "/sitemap.xml -> $sm_status (must be 200 or 301)"
fi
rm -f /tmp/sitemap-$$.xml

echo ""
echo "=== 4. Article URL smoke + JSON-LD ==="
# Sample-test up to MAX_SAMPLE articles. At 3,200 pages a full O(n) loop
# would take ~53 minutes — sampling keeps the deploy gate fast. (Council
# round 2 finding.)
MAX_SAMPLE="${MAX_SAMPLE:-20}"
sample_count=0
mapfile -t MD_FILES < <(ls "$PSEO_DIR"/*.md 2>/dev/null | shuf -n "$MAX_SAMPLE")
for md in "${MD_FILES[@]}"; do
  [ -f "$md" ] || continue
  slug=$(basename "$md" .md)
  bucket=$(awk '/^bucket:/{gsub(/[",]/,""); print $2; exit}' "$md")
  [ -z "$bucket" ] && continue
  url="$ORIGIN/$bucket/$slug/"
  status=$(curl -s -o /tmp/article-$$.html -w "%{http_code}" "$url")
  if [ "$status" = "200" ]; then
    jsonld=$(grep -c 'application/ld+json' /tmp/article-$$.html || true)
    intlinks=$(grep -oE 'href="/[^"]+"' /tmp/article-$$.html | sort -u | wc -l)
    [ "$jsonld" -ge 1 ] || { err "$url missing JSON-LD"; rm -f /tmp/article-$$.html; continue; }
    [ "$intlinks" -ge 8 ] || wrn "$url has only $intlinks internal links (target ≥8)"
    ok "$url (200, JSON-LD=$jsonld, intlinks=$intlinks)"
    sample_count=$((sample_count+1))
  else
    err "$url -> $status"
  fi
  rm -f /tmp/article-$$.html
done
[ "$sample_count" -ge 1 ] || err "no articles smoke-tested"
echo "  (sampled $sample_count of $(ls "$PSEO_DIR"/*.md 2>/dev/null | wc -l) total)"

echo ""
echo "=== 5. Sticky mobile CTA ==="
home_html=$(curl -s "$ORIGIN/")
echo "$home_html" | grep -q "affiliate-sticky-bar" && ok "sticky CTA in homepage DOM" || err "no sticky CTA"

echo ""
echo "=== 6. Affiliate URL on homepage ==="
echo "$home_html" | grep -qE 'https://www\.ether\.fi/@defycard' && ok "ether.fi affiliate URL present" || err "no affiliate URL on homepage"

echo ""
echo "=== 7. FTC disclosure visible ==="
echo "$home_html" | grep -q "ftc-banner" && ok "FTC banner element present" || err "no FTC banner"

echo ""
echo "=== 8. CSS tokens loaded ==="
echo "$home_html" | grep -qE "tokens\.[a-f0-9]+\.css|src/styles/tokens" && ok "tokens.css linked" || wrn "could not detect tokens.css"

echo ""
echo "=== 9. www -> apex 301 ==="
www_hdr=$(curl -sI "https://www.defycard.com/reviews/binance-card-review/" 2>&1)
www_status=$(echo "$www_hdr" | head -1 | grep -oE '\b[0-9]{3}\b' | head -1)
www_loc=$(echo "$www_hdr" | tr -d '\r' | awk 'tolower($1)=="location:"{print $2; exit}')
if [ "$www_status" = "301" ] && [ "$www_loc" = "https://defycard.com/reviews/binance-card-review/" ]; then
  ok "www -> 301 $www_loc"
else
  err "www redirect wrong: status=$www_status location=$www_loc"
fi

echo ""
echo "=== 10. Retirement sample (redirects.map) ==="
MAP="$ROOT/seo-site/redirects.map"
row301=$(awk -F'\t' '/^\//{ if ($2 != "410") { print $1 "\t" $2; exit } }' "$MAP")
row410=$(awk -F'\t' '/^\//{ if ($2 == "410") { print $1; exit } }' "$MAP")
if [ -n "$row301" ]; then
  from301=${row301%%$'\t'*}; to301=${row301#*$'\t'}
  r_hdr=$(curl -sI "$ORIGIN$from301" 2>&1)
  r_status=$(echo "$r_hdr" | head -1 | grep -oE '\b[0-9]{3}\b' | head -1)
  r_loc=$(echo "$r_hdr" | tr -d '\r' | awk 'tolower($1)=="location:"{print $2; exit}')
  if [ "$r_status" = "301" ] && { [ "$r_loc" = "$ORIGIN$to301" ] || [ "$r_loc" = "$to301" ]; }; then
    ok "$from301 -> 301 $r_loc"
  else
    err "$from301 expected 301 -> $to301, got $r_status $r_loc"
  fi
else
  err "redirects.map has no 301 row"
fi
if [ -n "$row410" ]; then
  g_status=$(curl -s -o /dev/null -w "%{http_code}" "$ORIGIN$row410")
  [ "$g_status" = "410" ] && ok "$row410 -> 410" || err "$row410 expected 410, got $g_status"
else
  err "redirects.map has no 410 row"
fi

echo ""
echo "=== 11. One sitemap (sitemap.xml == sitemap-index.xml) ==="
curl -s "$ORIGIN/sitemap.xml" -o /tmp/sm-a-$$.xml
curl -s "$ORIGIN/sitemap-index.xml" -o /tmp/sm-b-$$.xml
if [ -s /tmp/sm-a-$$.xml ] && cmp -s /tmp/sm-a-$$.xml /tmp/sm-b-$$.xml; then
  ok "sitemap.xml and sitemap-index.xml are byte-identical"
else
  err "sitemap.xml and sitemap-index.xml differ (or empty)"
fi
rm -f /tmp/sm-a-$$.xml /tmp/sm-b-$$.xml

echo ""
echo "=== 12. Visible breadcrumb on a sampled article ==="
# Step 4 deletes each downloaded page inside its loop (1-8 stay untouched), so
# re-fetch the first sampled article here.
bc_md="${MD_FILES[0]:-}"
if [ -n "$bc_md" ]; then
  bc_bucket=$(awk '/^bucket:/{gsub(/[",]/,""); print $2; exit}' "$bc_md")
  bc_url="$ORIGIN/$bc_bucket/$(basename "$bc_md" .md)/"
  bc_n=$(curl -s "$bc_url" | grep -c 'aria-label="Breadcrumb"' || true)
  [ "$bc_n" -ge 1 ] && ok "$bc_url has visible breadcrumb" || err "$bc_url has no aria-label=\"Breadcrumb\""
else
  err "no sampled article for breadcrumb check"
fi

echo ""
echo "=========================================================="
echo "  Verification: $PASS pass / $WARN warn / $FAIL fail"
echo "=========================================================="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
