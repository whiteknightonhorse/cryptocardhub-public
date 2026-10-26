#!/usr/bin/env python3
"""telegram_lib.py — the ONE Telegram alert sender for this repo's cron scripts.

Stdlib only: cron scripts under scripts/ run via /usr/bin/python3, which has
no `requests`. video-generator's own pipeline/telegram_preview.py is a
separate module (its own venv, its own richer sendVideo use) — this one is
for a one-line text alert from anywhere in scripts/.
"""
from __future__ import annotations

import os
import urllib.parse
import urllib.request


def alert(text: str, prefix: str = "[cryptocardhub]") -> bool:
    """Best-effort text alert. Never raises — an alert must not crash the
    pipeline it is reporting on. Returns False (silently) if creds are
    missing so a misconfigured env doesn't take down a cron job."""
    token = os.environ.get("TG_BOT_TOKEN")
    chat = os.environ.get("TG_CHAT_ID")
    if not token or not chat:
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": f"{prefix} {text}"}).encode()
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=30
        ).read()
        return True
    except Exception:
        return False


def _demo() -> None:
    old = os.environ.pop("TG_BOT_TOKEN", None), os.environ.pop("TG_CHAT_ID", None)
    try:
        assert alert("self-check, no creds") is False
    finally:
        if old[0] is not None:
            os.environ["TG_BOT_TOKEN"] = old[0]
        if old[1] is not None:
            os.environ["TG_CHAT_ID"] = old[1]
    print("telegram_lib self-check OK")


if __name__ == "__main__":
    _demo()
