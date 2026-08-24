#!/bin/bash
# yt-apply-brand-watermark.sh <env_prefix>
#
# Applies YouTube Branding Watermark + channel description in one shot.
# Reads OAuth creds from ${PREFIX}_CLIENT_ID / _SECRET / _REFRESH_TOKEN env
# vars; description from ./yt-channel-descriptions/${PREFIX}.txt.
#
# Usage:
#   set -a; source /home/cryptocardhub/.env; set +a
#   ./scripts/yt-apply-brand-watermark.sh DEFYCARD
#
# Hard rules (Y47 in youtube1/SKILL.md — DO NOT change without reading the
# invariant):
#   - durationMs MUST be 3600000 (1h), never 60000.
#     60000 silently breaks the moment any long-form video lands.
#   - watermarks.set returns HTTP 204 with empty body — this IS success.
#   - channels.update uses GET-merge-PUT so banner/keywords/country survive.
#   - Use the youtube.force-ssl scope refresh token (same one the publish
#     pipeline uses); youtube.readonly cannot write.
#
# Prereqs: ffmpeg + python3 (stdlib only).

set -u
PREFIX=${1:?"missing env_prefix"}
CID_VAR="${PREFIX}_CLIENT_ID"
CSEC_VAR="${PREFIX}_CLIENT_SECRET"
RT_VAR="${PREFIX}_REFRESH_TOKEN"
[ -z "${!CID_VAR:-}" ] && { echo "ERR: $CID_VAR not set" >&2; exit 1; }
[ -z "${!CSEC_VAR:-}" ] && { echo "ERR: $CSEC_VAR not set" >&2; exit 1; }
[ -z "${!RT_VAR:-}" ] && { echo "ERR: $RT_VAR not set" >&2; exit 1; }

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
DESC_FILE="$SCRIPT_DIR/yt-channel-descriptions/${PREFIX}.txt"
[ -f "$DESC_FILE" ] || { echo "ERR: missing $DESC_FILE" >&2; exit 2; }

# 1. Refresh access token + fetch channel info (id, title, profile thumbnail).
read ACCESS CHID THUMB_URL TITLE <<<"$(CID="${!CID_VAR}" CSEC="${!CSEC_VAR}" RT="${!RT_VAR}" python3 - <<'PY'
import json, os, urllib.parse, urllib.request
body = urllib.parse.urlencode({
    'client_id': os.environ['CID'], 'client_secret': os.environ['CSEC'],
    'refresh_token': os.environ['RT'], 'grant_type': 'refresh_token',
}).encode()
req = urllib.request.Request('https://oauth2.googleapis.com/token', data=body, method='POST')
with urllib.request.urlopen(req, timeout=15) as r:
    a = json.loads(r.read())['access_token']
req = urllib.request.Request(
    'https://youtube.googleapis.com/youtube/v3/channels?part=snippet,brandingSettings&mine=true',
    headers={'Authorization': f'Bearer {a}'})
with urllib.request.urlopen(req, timeout=15) as r:
    d = json.loads(r.read())['items'][0]
print(a, d['id'], d['snippet']['thumbnails']['high']['url'],
      d['snippet']['title'].replace(' ', '_'))
PY
)"
echo "channel: $TITLE ($CHID)"

# 2. Download profile thumbnail → 300×300 PNG (sweet spot for the API; 150-800
# accepted but 300 keeps it sharp on all viewports without bloat).
TMPDIR=$(mktemp -d); trap 'rm -rf "$TMPDIR"' EXIT
RAW="$TMPDIR/raw.png"; FINAL="$TMPDIR/wm-300.png"
curl -sLo "$RAW" "$THUMB_URL"
ffmpeg -y -loglevel error -i "$RAW" -vf "scale=300:300:flags=lanczos" -pix_fmt rgba "$FINAL"
echo "watermark: $FINAL ($(stat -c%s "$FINAL") bytes)"

# 3. POST watermarks.set — multipart body (JSON metadata + PNG bytes).
# Y47: durationMs=3600000 covers any reasonable video length. 60000 looks
# correct for 60s Shorts but breaks the moment a long-form upload lands.
BOUNDARY="brand-$$-$(date +%s)"; BODY="$TMPDIR/body.bin"
BOUNDARY="$BOUNDARY" FINAL_PATH="$FINAL" OUT="$BODY" python3 - <<'PY'
import os
boundary = os.environ['BOUNDARY']; img = os.environ['FINAL_PATH']
metadata = (
    '{"timing":{"type":"offsetFromStart","offsetMs":0,"durationMs":3600000},'
    '"position":{"type":"corner","cornerPosition":"bottomRight"}}'
)
parts = [
    f'--{boundary}\r\n'.encode(),
    b'Content-Type: application/json; charset=UTF-8\r\n\r\n',
    metadata.encode(),
    b'\r\n--' + boundary.encode() + b'\r\n',
    b'Content-Type: image/png\r\n\r\n',
    open(img, 'rb').read(),
    f'\r\n--{boundary}--\r\n'.encode(),
]
open(os.environ['OUT'], 'wb').write(b''.join(parts))
PY

echo "=== POST watermarks.set ==="
curl -s -X POST -w 'HTTP %{http_code}\n' \
  -H "Authorization: Bearer $ACCESS" \
  -H "Content-Type: multipart/related; boundary=$BOUNDARY" \
  --data-binary @"$BODY" \
  "https://www.googleapis.com/upload/youtube/v3/watermarks/set?channelId=$CHID"
# HTTP 204 = success (empty body by design — NOT an error per Y47).

# 4. PUT channels.update part=brandingSettings — GET-merge-PUT pattern.
# Never construct brandingSettings from scratch — without an `image`
# block the banner reverts; without `channel.title/keywords/country`
# passthrough, those fields can drop.
echo "=== PUT channels.update brandingSettings ==="
ACCESS="$ACCESS" CHID="$CHID" DESC_FILE="$DESC_FILE" python3 - <<'PY'
import json, os, sys, urllib.request, urllib.error
ACCESS = os.environ['ACCESS']; CHID = os.environ['CHID']
new_desc = open(os.environ['DESC_FILE']).read().strip()
req = urllib.request.Request(
    f"https://youtube.googleapis.com/youtube/v3/channels?part=brandingSettings&id={CHID}",
    headers={"Authorization": f"Bearer {ACCESS}"})
with urllib.request.urlopen(req, timeout=15) as r:
    cur = json.loads(r.read())["items"][0]
bs = cur.get("brandingSettings", {})
ch = bs.get("channel", {})
ch["description"] = new_desc
body = {"id": CHID, "brandingSettings": {"channel": ch, "image": bs.get("image", {})}}
req = urllib.request.Request(
    "https://youtube.googleapis.com/youtube/v3/channels?part=brandingSettings",
    data=json.dumps(body).encode(), method="PUT",
    headers={"Authorization": f"Bearer {ACCESS}", "Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        sys.stdout.write(f"description applied (HTTP {r.status})\n")
except urllib.error.HTTPError as e:
    sys.stderr.write(f"HTTP {e.code}: {e.read().decode()[:400]}\n"); sys.exit(1)
PY

echo "DONE for $PREFIX ($TITLE)"
