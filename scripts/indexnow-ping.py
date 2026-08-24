#!/usr/bin/env python3
"""indexnow-ping.py — AEO-7. Tell Bing which pages actually changed.

WHY BING
OpenAI's own ChatGPT Search documentation names exactly two search partners,
and Bing is one of them: the route into a ChatGPT answer runs through the Bing
index. Bing is also the second-largest live human source on this site (328
sessions, 2.1 % conversion). IndexNow is the published, free way to tell it
that a URL changed, instead of waiting for a recrawl.

WHY ONLY THE CHANGED ONES
IndexNow's own guidance is to submit URLs when they change. Submitting all 758
URLs on every deploy is the definition of the spam pattern the protocol bans
keys for — and it would also destroy the signal we just spent AEO-5 repairing.
So:

  * the first run submits NOTHING. With no prior state, "changed" is not
    knowable, and guessing means guessing "everything".
  * later runs submit exactly the URLs whose content file's hash moved.
  * a run that would submit more than MAX_URLS refuses and says why.

State lives in logs/aeo/indexnow-state.json (content hashes, not mtimes —
mtimes change when a file is merely rewritten with identical bytes).

Usage:
    indexnow-ping.py --dry-run     # print what would be sent, touch nothing
    indexnow-ping.py               # send, then record the new hashes
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "seo-site" / "src" / "content" / "pseo"
PUBLIC = ROOT / "seo-site" / "public"
STATE = ROOT / "logs" / "aeo" / "indexnow-state.json"
SITE = "https://defycard.com"
ENDPOINT = "https://api.indexnow.org/indexnow"

# A deploy that changed more than this many pages is a migration, not an edit.
# Migrations get recrawled normally; blasting them at IndexNow is what gets a
# key banned.
MAX_URLS = 200


def _key() -> str | None:
    """The IndexNow key, read from the public key file that proves ownership.

    The key is deliberately NOT a secret — IndexNow verifies it by fetching
    https://defycard.com/<key>.txt. It lives in seo-site/public/ for exactly
    that reason and must never be treated as a credential.
    """
    for f in PUBLIC.glob("*.txt"):
        if re.fullmatch(r"[0-9a-f]{32,128}", f.stem) and f.read_text().strip() == f.stem:
            return f.stem
    return None


def _bucket_of(md: Path) -> str | None:
    head = md.read_text(encoding="utf-8", errors="replace")[:4000]
    m = re.search(r"^bucket:\s*(.+)$", head, re.MULTILINE)
    if not m:
        return None
    return m.group(1).strip().strip('"').strip("'")


def current_hashes() -> dict[str, str]:
    """url -> sha256 of the content file that produces it."""
    out: dict[str, str] = {}
    for md in sorted(CONTENT.glob("*.md")):
        bucket = _bucket_of(md)
        if not bucket:
            continue
        digest = hashlib.sha256(md.read_bytes()).hexdigest()
        out[f"{SITE}/{bucket}/{md.stem}/"] = digest
    # The matrix is generated from YAML, not from a content file.
    avail = ROOT / "config" / "availability.yaml"
    if avail.exists():
        out[f"{SITE}/geo/availability-matrix/"] = hashlib.sha256(avail.read_bytes()).hexdigest()
    return out


def changed_urls(current: dict[str, str], previous: dict[str, str] | None) -> list[str]:
    """URLs to submit. `previous is None` means first run -> submit nothing.

    New URLs and modified URLs both count; a URL that disappeared is not
    submitted (there is nothing at it to index).
    """
    if previous is None:
        return []
    return sorted(u for u, h in current.items() if previous.get(u) != h)


def load_state() -> dict[str, str] | None:
    if not STATE.exists():
        return None
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def save_state(hashes: dict[str, str]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(hashes, indent=0, sort_keys=True), encoding="utf-8")


def submit(urls: list[str], key: str) -> int:
    body = json.dumps({
        "host": "defycard.com",
        "key": key,
        "keyLocation": f"{SITE}/{key}.txt",
        "urlList": urls,
    }).encode()
    req = urllib.request.Request(
        ENDPOINT, data=body,
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        print(f"[indexnow] HTTP {e.code}: {e.read()[:200]!r}", file=sys.stderr)
        return e.code


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    previous = load_state()
    current = current_hashes()
    urls = changed_urls(current, previous)

    if previous is None:
        print(f"[indexnow] first run — recording {len(current)} hashes, submitting nothing")
        if not a.dry_run:
            save_state(current)
        return 0

    print(f"[indexnow] {len(urls)} changed URL(s) of {len(current)}")
    for u in urls[:20]:
        print(f"    {u}")
    if len(urls) > 20:
        print(f"    ... and {len(urls) - 20} more")
    if not urls:
        return 0
    if len(urls) > MAX_URLS:
        print(f"[indexnow] REFUSING: {len(urls)} > MAX_URLS={MAX_URLS}. That is a "
              f"migration, not an edit — submit nothing and let the normal "
              f"recrawl handle it.", file=sys.stderr)
        if not a.dry_run:
            save_state(current)
        return 0
    if a.dry_run:
        print("[indexnow] dry-run — not submitted")
        return 0

    key = _key() or os.environ.get("INDEXNOW_KEY")
    if not key:
        print("[indexnow] no key file in seo-site/public/<key>.txt — skipping",
              file=sys.stderr)
        return 0
    status = submit(urls, key)
    print(f"[indexnow] submitted {len(urls)} URL(s) -> HTTP {status}")
    if status in (200, 202):
        save_state(current)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
