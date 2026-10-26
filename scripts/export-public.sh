#!/usr/bin/env bash
# CCH-PUBLIC-EXPORT-01: build the public snapshot candidate from git-tracked files, check it, and
# (with --push) commit + push it to the cryptocardhub-public clone. Never deletes the clone:
# its .git, README.md and .gitignore are its own. ALLOW is the only way a path gets out; DENY is
# a second fence over it. EXPORT_* env overrides exist for tests only.
set -euo pipefail
MODE=dry-run
case "${1:-}" in --push) MODE=push ;; --dry-run|"") ;; *) echo "usage: $0 [--dry-run|--push]" >&2; exit 64 ;; esac

PRIV="${EXPORT_PRIV:-$(cd "$(dirname "$0")/.." && pwd)}"
PUB="${EXPORT_PUB:-$HOME/export-public}"
SECRETS_FILE="${EXPORT_ENVFILE:-$HOME/.env}"
JSONL="${EXPORT_JSONL:-$PRIV/logs/export/public.jsonl}"
mkdir -p "$(dirname "$JSONL")"

ALLOW='^(video-generator/|seo-site/|scripts/|docs/architecture\.md$|\.env\.example$|\.gitignore$|pytest\.ini$|config/rates\.yaml$|config/pseo-rate\.txt$)'
DENY='(^\.claude/|^CLAUDE\.md$|^logs/|/state/|\.db$|\.sqlite|BingSiteAuth\.xml|config/proxies\.yaml|/node_modules/|\.env$)'

# values that are public by nature (served on the site / model id / channel id); measured 2026-10-26
PUBLIC_NAMES="DOMAIN ETHERFI_REF_URL PUBLIC_ETHERFI_REF_URL ETHERFI_REF_URL_SHORT WRITER_MODEL YOUTUBE_CHANNEL_ID"

TMP=$(mktemp -d); trap 'rm -r -- "$TMP"' EXIT
C="$TMP/c"; mkdir -p "$C"
SHA=$(git -C "$PRIV" rev-parse --short HEAD)
N=0; M=0; PUSHED=false

log_row() { # $1 refusal reason or "" ; $2 result
  local refused=null; [ -n "$1" ] && refused="\"$1\""
  printf '{"ts":"%s","mode":"%s","files":%s,"prior_files":%s,"refused":%s,"pushed":%s,"private_sha":"%s","result":"%s"}\n' \
    "$(date -u +%FT%TZ)" "$MODE" "$N" "$M" "$refused" "$PUSHED" "$SHA" "$2" >> "$JSONL"
}
refuse() { # $1 reason, $2 detail
  log_row "$1" refused
  echo "🔴 public export refused: $1 ($2)" >&2
  if [ -z "${EXPORT_NO_TG:-}" ]; then
    (cd "$PRIV" && python3 -c "import sys; sys.path.insert(0,'scripts'); import telegram_lib as t; t.alert(sys.argv[1])" \
      "🔴 public export refused: $1 ($2)") || true
  fi
  exit 2
}

# clone must exist and point at cryptocardhub-public
if [ ! -d "$PUB/.git" ] || ! git -C "$PUB" remote get-url origin 2>/dev/null | grep -q 'cryptocardhub-public'; then
  refuse clone_missing "$PUB"
fi
M=$(git -C "$PUB" ls-files | wc -l | tr -d ' ')

# candidate: tracked files only (git ls-files), ALLOW minus DENY
git -C "$PRIV" ls-files | grep -E "$ALLOW" | grep -vE "$DENY" > "$TMP/list" || true
while IFS= read -r f; do
  [ -f "$PRIV/$f" ] || continue
  mkdir -p "$C/$(dirname "$f")"; cp "$PRIV/$f" "$C/$f"
done < "$TMP/list"
rm -f "$C/.gitignore" # root .gitignore is the clone's own, never transferred; keep N honest
N=$(find "$C" -type f | wc -l | tr -d ' ')

# check 1: real values from the secrets file inside the candidate
if [ -f "$SECRETS_FILE" ]; then
  leaked=""
  while IFS='=' read -r name val; do
    [ -z "${val:-}" ] && continue
    val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
    [ "${#val}" -lt 12 ] && continue
    case " $PUBLIC_NAMES " in *" $name "*) continue ;; esac
    if grep -rqF -- "$val" "$C"; then leaked="$leaked $name"; fi
  done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$SECRETS_FILE" || true)
  [ -n "$leaked" ] && refuse env_value_leak "$leaked"
fi
echo "✅ 1 secret values: none in candidate"

# check 2: known key formats (a PEM frame gets its own reason; check 4 overlaps on purpose)
h=$(grep -rInE 'gh[pous]_[A-Za-z0-9_]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,}|AIza[0-9A-Za-z_-]{30,}|ya29\.[A-Za-z0-9_-]{20,}|xox[baprs]-|-----BEGIN [A-Z ]*PRIVATE KEY' "$C" | head -3 || true)
if [ -n "$h" ]; then
  if echo "$h" | grep -q 'PRIVATE KEY'; then refuse pem_or_sa_key "${h%%:*}"; fi
  refuse key_pattern "${h%%:*}"
fi
echo "✅ 2 key formats: none"

# check 3: long values next to marker words
h=$(grep -rInE '(secret|token|password|api_key|apikey|client_secret|refresh_token)[^A-Za-z0-9]{0,4}[:=][^A-Za-z0-9]{0,4}[A-Za-z0-9/_+-]{16,}' "$C" \
    | grep -viE 'example|placeholder|your_|<|\$\{|os\.environ|getenv|process\.env|import\.meta' | head -3 || true)
[ -n "$h" ] && refuse marker_string "${h%%:*}"
echo "✅ 3 marker strings: none"

# check 4: service-account key material, explicit (no PEM frame needed). Content: PEM frame,
# a private-key JSON field, a service-account type field (regex spelled with [] so this file does
# not match itself). Name: any ga4-sa* file. A bare code mention of the
# ~/.claude/ga4-sa.json PATH is not key material (3 scripts read it) and is deliberately allowed.
h=$(grep -rlE -- '-----BEGIN [A-Z ]*PRIVATE KEY|"private_ke[y]"|"type": *"service_accoun[t]"' "$C" | head -3 || true)
h="$h$(find "$C" -name 'ga4-sa*' | head -1)"
[ -n "$h" ] && refuse pem_or_sa_key "$(echo "$h" | head -1)"
echo "✅ 4 service-account key material: none"

# shrink ceiling: the script declares how many files it expects against the clone
echo "files: N=$N candidate / M=$M in clone"
if [ "$N" -eq 0 ] || [ $((N * 2)) -lt "$M" ]; then refuse shrink_ceiling "N=$N M=$M"; fi

if [ "$MODE" = dry-run ]; then log_row "" dry_run; exit 0; fi

# the clone may be behind origin (files added by hand on GitHub): fast-forward first, else refuse
GIT_SSH_COMMAND="ssh -i $HOME/.ssh/cryptocardhub-public-deploy" git -C "$PUB" fetch -q origin main \
  || refuse fetch_failed "$PUB"
git -C "$PUB" merge -q --ff-only origin/main || refuse clone_behind "diverged from origin/main"
# the clone owns README.md, .gitignore and provek.json (hand-placed accountability file, not in ALLOW)
rsync -a --delete --exclude=.git --exclude=/README.md --exclude=/.gitignore --exclude=/provek.json "$C/" "$PUB/"
git -C "$PUB" add -A
if git -C "$PUB" diff --cached --quiet; then log_row "" no_change; echo "no_change"; exit 0; fi
git -C "$PUB" commit -qm "snapshot $(date -u +%F) from private $SHA"
GIT_SSH_COMMAND="ssh -i $HOME/.ssh/cryptocardhub-public-deploy" git -C "$PUB" push -q origin main
PUSHED=true; log_row "" pushed; echo "pushed"
