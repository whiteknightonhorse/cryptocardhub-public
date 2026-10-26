#!/usr/bin/env bash
# push.sh — the only sanctioned path from a local commit to origin.
#
# Four gates, in this order, then the push:
#
#   1. pytest (root suite: video-generator + seo-site + scripts)
#   2. secret-scan
#   3. current branch is not main/master
#   4. the remote repository is PRIVATE
#   5. git push origin HEAD:ci-staging
#
# Gate 4 is the one that is easy to think unnecessary. The repo holds an
# affiliate corpus, referral handles and the whole keyword bank; a repository
# that silently flips to public publishes all of it, and nothing else in this
# project ever asks GitHub what the visibility currently is. Unknown counts as
# public here: if `gh` cannot answer, the push does not happen.
#
# Exits: 1 tests · 2 secret · 3 branch · 4 visibility · 5 push
set -uo pipefail

ROOT=/home/cryptocardhub
PYBIN=$ROOT/video-generator/venv/bin/python
TARGET_BRANCH=ci-staging

cd "$ROOT" || exit 1

# Same load as deploy.sh: several gates import weekly-report.py, which reads
# GA4_PROPERTY_ID at import time and raises KeyError without it — the suite
# would fail to COLLECT, which reads exactly like a red gate.
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck source=/dev/null
  . "$ROOT/.env"
  set +a
fi

echo "==> [1/5] pytest (root suite)"
if ! "$PYBIN" -m pytest -rf; then
  echo "ERROR: tests are red — refusing to push" >&2
  exit 1
fi

echo ""
echo "==> [2/5] secret scan"
# Reuses the PreToolUse hook rather than a second copy of the pattern list —
# two scanners drift, and the one that matters is the one the hook enforces.
if ! printf '{"tool_input":{"command":"git push"}}' \
     | bash "$ROOT/.claude/hooks/secret-scan.sh"; then
  echo "ERROR: secret scan flagged the tree — refusing to push" >&2
  exit 2
fi
echo "    clean"

echo ""
echo "==> [3/5] branch check"
BRANCH=$(git branch --show-current)
case "$BRANCH" in
  main|master|"")
    echo "ERROR: refusing to push from branch '${BRANCH:-<detached>}'" >&2
    exit 3
    ;;
esac
echo "    on '$BRANCH'"

echo ""
echo "==> [4/5] remote visibility must be PRIVATE"
VIS=$(gh repo view --json visibility -q .visibility 2>&1) || VIS="UNREADABLE: $VIS"
if [ "$VIS" != "PRIVATE" ]; then
  echo "ERROR: remote visibility is '$VIS', not PRIVATE — refusing to push." >&2
  echo "       If gh is unauthenticated, run 'gh auth login' — an unreadable" >&2
  echo "       visibility is treated as public, never as fine." >&2
  exit 4
fi
echo "    PRIVATE"

echo ""
echo "==> [5/5] git push origin HEAD:$TARGET_BRANCH"
if ! git push origin "HEAD:$TARGET_BRANCH"; then
  echo "ERROR: push failed" >&2
  exit 5
fi

echo ""
echo "✓ pushed $(git rev-parse --short HEAD) → origin/$TARGET_BRANCH"
