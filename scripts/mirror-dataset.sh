#!/usr/bin/env bash
# mirror-dataset.sh — CCH-SEO-35: push the tree export-dataset.py generated to
# HuggingFace (git over ssh) and Kaggle (datasets version). One tree, three
# destinations; this script generates nothing. Runs after the Monday 06:50
# export+commit line. One JSON row per target per run -> logs/dataset/mirror.jsonl
# (weekly-report.py reads the last row per target). The targets are independent.
#
# Env overrides exist for tests only: ROOT DATASET_DIR LOG_FILE EXPORT_CMD KAGGLE_BIN.
# Usage: mirror-dataset.sh [--dry-run]   (dry-run prints commands, writes no row)
set -uo pipefail

ROOT=${ROOT:-/home/cryptocardhub}
DATASET_DIR=${DATASET_DIR:-$ROOT/defycard-data}
LOG_FILE=${LOG_FILE:-$ROOT/logs/dataset/mirror.jsonl}
EXPORT_CMD=${EXPORT_CMD:-$ROOT/video-generator/venv/bin/python $ROOT/scripts/export-dataset.py --check}
# kaggle >=2 (reads ~/.kaggle/access_token) needs python>=3.11; the main venv is 3.10.
KAGGLE_BIN=${KAGGLE_BIN:-$ROOT/video-generator/venv-kaggle/bin/kaggle}
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1

row() {  # row <target> <ok:true|false> <cause>
  [ "$DRY" = 1 ] && return
  mkdir -p "$(dirname "$LOG_FILE")"
  python3 -c 'import json,sys,datetime as d; print(json.dumps({"ts":d.datetime.now(d.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),"target":sys.argv[1],"ok":sys.argv[2]=="true","cause":" ".join(sys.argv[3].split())[-200:]}))' \
    "$1" "$2" "$3" >> "$LOG_FILE"
}

# (a) the tree must equal a fresh export and be committed, else mirrors would ship stale data
if ! $EXPORT_CMD >/dev/null 2>&1 || ! git -C "$DATASET_DIR" diff --quiet HEAD 2>/dev/null; then
  echo "tree_stale: run the weekly export first" >&2
  row hf false tree_stale; row kaggle false tree_stale
  exit 1
fi

rc=0
run() {  # run <target> <cmd...>
  local t=$1; shift
  if [ "$DRY" = 1 ]; then echo "DRY[$t]: $*"; return; fi
  local err r; err=$("$@" 2>&1 >/dev/null); r=$?
  if [ $r = 0 ]; then row "$t" true ""; else row "$t" false "${err:-exit $r}"; rc=1; fi
}

# (b) HuggingFace
GIT_SSH_COMMAND="ssh -i $HOME/.ssh/defycard-hf -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" \
  run hf git -C "$DATASET_DIR" push hf HEAD:main

# (c) Kaggle: files of HEAD only (no .git), incl. dataset-metadata.json
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
if [ "$DRY" = 1 ]; then
  echo "DRY[kaggle]: git -C $DATASET_DIR archive HEAD | tar -x -C \$TMP; $KAGGLE_BIN datasets version -p \$TMP -m \"export $(date -u +%F)\""
else
  [ -z "${KAGGLE_API_TOKEN:-}" ] && [ -r "$HOME/.kaggle/access_token" ] && KAGGLE_API_TOKEN=$(cat "$HOME/.kaggle/access_token") && export KAGGLE_API_TOKEN
  if err=$(git -C "$DATASET_DIR" archive HEAD 2>&1 | tar -x -C "$TMP" 2>&1); then
    run kaggle "$KAGGLE_BIN" datasets version -p "$TMP" -m "export $(date -u +%F)"
  else
    row kaggle false "archive: $err"; rc=1
  fi
fi
exit $rc
