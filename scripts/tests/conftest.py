"""No test in this suite may reach Telegram.

telegram_lib.alert() sends whenever TG_* credentials sit in the environment, so
one unstubbed failure path sent real messages (2026-09-29). Every caller resolves
telegram_lib through sys.modules, so one patch here covers all of them.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def tg_alerts(monkeypatch):
    import telegram_lib

    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TG_CHAT_ID", raising=False)
    sent = []
    monkeypatch.setattr(telegram_lib, "alert", lambda text, *a, **k: sent.append(text) or True)
    return sent
