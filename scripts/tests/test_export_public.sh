#!/usr/bin/env bash
# CCH-PUBLIC-EXPORT-01 gate: runs the REAL scripts/export-public.sh against a fixture private repo
# and a fixture public clone. Expected values are literals (Y49), never read from the script.
set -uo pipefail
HERE=$(cd "$(dirname "$0")/../.." && pwd)
T=$(mktemp -d); trap 'rm -r -- "$T"' EXIT
fails=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fails=$((fails+1)); }

mk() { # fresh fixtures: private repo (3 tracked ALLOW files) + public clone (README + 3 files)
  rm -rf "$T/priv" "$T/pub" "$T/jsonl" "$T/cryptocardhub-public.git" "$T/other"
  mkdir -p "$T/priv/scripts" "$T/pub"
  git -C "$T/priv" init -q
  for i in 1 2 3; do echo "echo $i" > "$T/priv/scripts/s$i.sh"; done
  git -C "$T/priv" add -A; git -C "$T/priv" -c user.name=t -c user.email=t@t commit -qm init
  git init -q --bare -b main "$T/cryptocardhub-public.git"
  git -C "$T/pub" init -q -b main; git -C "$T/pub" remote add origin "$T/cryptocardhub-public.git"
  echo "own readme" > "$T/pub/README.md"
  mkdir -p "$T/pub/scripts"; for i in 1 2 3; do echo "old $i" > "$T/pub/scripts/s$i.sh"; done
  git -C "$T/pub" add -A; git -C "$T/pub" -c user.name=t -c user.email=t@t commit -qm base
  git -C "$T/pub" push -q origin main
}
other_commit() { # someone pushes provek.json to origin by hand, so the clone is now behind
  git clone -q "$T/cryptocardhub-public.git" "$T/other"; echo '{}' > "$T/other/provek.json"
  git -C "$T/other" add -A; git -C "$T/other" -c user.name=t -c user.email=t@t commit -qm hand
  git -C "$T/other" push -q origin main
}
run() { EXPORT_PRIV="$T/priv" EXPORT_PUB="$T/pub" EXPORT_ENVFILE="$T/none" EXPORT_JSONL="$T/jsonl" EXPORT_NO_TG=1 \
  GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t \
  "$HERE/scripts/export-public.sh" "$@" >"$T/out" 2>&1; }
head_pub() { git -C "$T/pub" rev-parse HEAD; }

# (1) dry-run leaves README.md + .git intact and writes a JSONL row
mk; h=$(head_pub); run --dry-run; rc=$?
[ $rc -eq 0 ] && [ -f "$T/pub/README.md" ] && [ "$(head_pub)" = "$h" ] && grep -q '"result":"dry_run"' "$T/jsonl" \
  && [ "$(grep -c old "$T/pub/scripts/s1.sh")" = 1 ] && ok "1 dry-run" || bad "1 dry-run rc=$rc"

# (2) a tracked PEM file -> rc 2, reason pem_or_sa_key, clone untouched
mk; printf -- '%s%s\nabc\n' '-----BEGIN ' 'PRIVATE KEY-----' > "$T/priv/scripts/k.txt"
git -C "$T/priv" add -A; git -C "$T/priv" -c user.name=t -c user.email=t@t commit -qm k
h=$(head_pub); run --push; rc=$?
[ $rc -eq 2 ] && grep -q '"refused":"pem_or_sa_key"' "$T/jsonl" && [ "$(head_pub)" = "$h" ] \
  && [ "$(cat "$T/pub/scripts/s1.sh")" = "old 1" ] && ok "2 pem refused" || bad "2 pem rc=$rc"

# (2b) the same without a PEM frame: only check 4 can catch a private-key field
mk; printf '{"%s": "x"}\n' private_key > "$T/priv/scripts/sa.json"
git -C "$T/priv" add -A; git -C "$T/priv" -c user.name=t -c user.email=t@t commit -qm sa
run --push; rc=$?
[ $rc -eq 2 ] && grep -q '"refused":"pem_or_sa_key"' "$T/jsonl" && ok "2b sa key without PEM frame refused" || bad "2b rc=$rc"

# (3) candidate of 1 file against M=3 -> shrink_ceiling
mk; git -C "$T/priv" rm -q scripts/s2.sh scripts/s3.sh; git -C "$T/priv" -c user.name=t -c user.email=t@t commit -qm shrink
run --push; rc=$?
[ $rc -eq 2 ] && grep -q '"refused":"shrink_ceiling"' "$T/jsonl" && ok "3 shrink ceiling" || bad "3 shrink rc=$rc"

# (4) no changes -> no_change, rc 0
mk; for i in 1 2 3; do cp "$T/priv/scripts/s$i.sh" "$T/pub/scripts/s$i.sh"; done
git -C "$T/pub" add -A; git -C "$T/pub" -c user.name=t -c user.email=t@t commit -qm same
run --push; rc=$?
[ $rc -eq 0 ] && grep -q '"result":"no_change"' "$T/jsonl" && ok "4 no_change" || bad "4 no_change rc=$rc"

# (5) clone behind origin (hand-added provek.json): --push fast-forwards, keeps provek.json, pushes on top
mk; other_commit; echo "echo 9" > "$T/priv/scripts/s9.sh"
git -C "$T/priv" add -A; git -C "$T/priv" -c user.name=t -c user.email=t@t commit -qm s9
run --push; rc=$?
[ $rc -eq 0 ] && [ -e "$T/pub/provek.json" ] && [ -e "$T/pub/scripts/s9.sh" ] \
  && [ "$(git -C "$T/cryptocardhub-public.git" ls-tree -r --name-only main | grep -c provek.json)" = 1 ] \
  && [ "$(git -C "$T/cryptocardhub-public.git" log -1 --format=%s main | cut -c1-9)" = "snapshot " ] \
  && ok "5 clone behind: ff + provek kept + pushed" || bad "5 behind rc=$rc"

# (6) clone diverged from origin (unpushed local commit + remote commit) -> clone_behind, nothing pushed
mk; echo l > "$T/pub/local.txt"; git -C "$T/pub" add -A; git -C "$T/pub" -c user.name=t -c user.email=t@t commit -qm local
other_commit; run --push; rc=$?
[ $rc -eq 2 ] && grep -q '"refused":"clone_behind"' "$T/jsonl" && ok "6 diverged refused" || bad "6 diverged rc=$rc"

[ $fails -eq 0 ] && echo "6 ok" || { echo "$fails failed"; exit 1; }
