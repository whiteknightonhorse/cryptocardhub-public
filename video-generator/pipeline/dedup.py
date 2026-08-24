"""SQLite dedup ledger — topics, media URLs, title prefixes.

Per polymarket vg4 §5.4 + ytl 2026-05-05 (7d title-prefix dedup).
"""
from __future__ import annotations
import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import _config

_DB_PATH = _config.root() / "state" / "published.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS media_usage (
  url_hash TEXT PRIMARY KEY,
  first_used TIMESTAMP NOT NULL,
  last_used TIMESTAMP NOT NULL,
  platform TEXT NOT NULL,
  source_url TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS topic_usage (
  topic_id INTEGER NOT NULL,
  channel TEXT NOT NULL,
  last_used TIMESTAMP NOT NULL,
  PRIMARY KEY (topic_id, channel)
);
CREATE TABLE IF NOT EXISTS title_prefix_usage (
  prefix_hash TEXT PRIMARY KEY,
  channel TEXT NOT NULL,
  last_used TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS topic_failure (
  topic_id INTEGER NOT NULL,
  channel TEXT NOT NULL,
  last_failed TIMESTAMP NOT NULL,
  fails INTEGER NOT NULL DEFAULT 1,
  reason TEXT,
  PRIMARY KEY (topic_id, channel)
);
CREATE TABLE IF NOT EXISTS publish_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TIMESTAMP NOT NULL,
  channel TEXT NOT NULL,
  topic_id INTEGER NOT NULL,
  transport TEXT NOT NULL,
  external_id TEXT,
  status TEXT NOT NULL,
  notes TEXT
);
"""

_TTL_DAYS = 7

# Y49 P1-3 — topic cooldown raised 7 -> 21 days. Evidence from the live
# publish_log: 213 publications over 65 unique topics (3.3x reuse); of the 148
# repeats, 94 happened within 21 days and only 1 within 7 — i.e. the 7-day
# window was letting through nearly every repeat a viewer could still remember.
# Media and title-prefix cooldowns stay at 7 days (different pools, no evidence).
_TOPIC_TTL_DAYS = 21


@contextmanager
def _conn():
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(_DB_PATH)
    try:
        cx.executescript(_SCHEMA)
        yield cx
    finally:
        cx.commit()
        cx.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cutoff(days: int = _TTL_DAYS) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def topic_used_recently(topic_id: int, channel: str) -> bool:
    with _conn() as cx:
        row = cx.execute(
            "SELECT last_used FROM topic_usage WHERE topic_id=? AND channel=?",
            (topic_id, channel),
        ).fetchone()
    return bool(row and row[0] >= _cutoff(_TOPIC_TTL_DAYS))


#: Провал охлаждает тему НЕНАДОЛГО. Это не бан: тема может падать по причине, которая пройдёт
#: сама (провайдер, квота, сеть). Смысл окна ровно один — убрать её с дороги, чтобы следующий слот
#: достался другой теме, а не той же самой в 169-й раз.
_FAILURE_TTL_HOURS = 36


def topic_failed_recently(topic_id: int, channel: str) -> bool:
    """Падала ли тема так недавно, что выдавать её снова — значит повторить отказ.

    ⚠️ «Не падала» и «не смогли посмотреть» — РАЗНЫЕ миры, и здесь они не сливаются: отсутствие
    строки означает именно отсутствие зарегистрированного провала, а отказ базы поднимется
    исключением, а не вернётся как False.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=_FAILURE_TTL_HOURS)).isoformat()
    with _conn() as cx:
        row = cx.execute(
            "SELECT last_failed FROM topic_failure WHERE topic_id=? AND channel=?",
            (topic_id, channel),
        ).fetchone()
    return bool(row and row[0] >= cutoff)


def record_topic_failure(topic_id: int, channel: str, reason: str = "") -> None:
    """Зарегистрировать провал слота на этой теме. Счётчик копится и не обнуляется успехом
    соседней темы: по нему видно тему, которая падает систематически, а не разово."""
    with _conn() as cx:
        cx.execute(
            """INSERT INTO topic_failure(topic_id, channel, last_failed, fails, reason)
               VALUES (?, ?, ?, 1, ?)
               ON CONFLICT(topic_id, channel) DO UPDATE SET
                 last_failed=excluded.last_failed,
                 fails=topic_failure.fails + 1,
                 reason=excluded.reason""",
            (topic_id, channel, _now(), reason[:200]),
        )


def record_topic(topic_id: int, channel: str) -> None:
    with _conn() as cx:
        cx.execute(
            "INSERT OR REPLACE INTO topic_usage(topic_id, channel, last_used) VALUES (?, ?, ?)",
            (topic_id, channel, _now()),
        )


def media_used_recently(url: str) -> bool:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()
    with _conn() as cx:
        row = cx.execute(
            "SELECT last_used FROM media_usage WHERE url_hash=?", (h,),
        ).fetchone()
    return bool(row and row[0] >= _cutoff())


def record_media(url: str, platform: str) -> None:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()
    now = _now()
    with _conn() as cx:
        cx.execute(
            """INSERT INTO media_usage(url_hash, first_used, last_used, platform, source_url)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(url_hash) DO UPDATE SET last_used=excluded.last_used""",
            (h, now, now, platform, url),
        )


def title_prefix_used_recently(title: str, channel: str) -> bool:
    """ytl 2026-05-05 — 7d dedup of first 4 words (lowercased)."""
    prefix = " ".join(title.lower().split()[:4])
    h = hashlib.sha256(prefix.encode("utf-8")).hexdigest()
    with _conn() as cx:
        row = cx.execute(
            "SELECT last_used FROM title_prefix_usage WHERE prefix_hash=? AND channel=?",
            (h, channel),
        ).fetchone()
    return bool(row and row[0] >= _cutoff())


def record_title_prefix(title: str, channel: str) -> None:
    prefix = " ".join(title.lower().split()[:4])
    h = hashlib.sha256(prefix.encode("utf-8")).hexdigest()
    with _conn() as cx:
        cx.execute(
            "INSERT OR REPLACE INTO title_prefix_usage(prefix_hash, channel, last_used) VALUES (?, ?, ?)",
            (h, channel, _now()),
        )


def log_publish(channel: str, topic_id: int, transport: str,
                external_id: str | None, status: str, notes: str = "") -> None:
    with _conn() as cx:
        cx.execute(
            """INSERT INTO publish_log(ts, channel, topic_id, transport, external_id, status, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (_now(), channel, topic_id, transport, external_id, status, notes),
        )
