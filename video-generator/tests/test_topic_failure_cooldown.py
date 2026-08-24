"""Провал ТОЖЕ охлаждает тему — иначе селектор зацикливается по построению.

Правило куплено 169 отказами. С 16 по 24 августа 2026 канал отказал 169 раз, и все 169 — ОДНА
тема: `mark_used` зовётся только после успешной публикации, `_pick_pseo` берёт первую
неохлаждённую страницу каталога, и падающая тема выдавалась каждый следующий слот навсегда.
Публикации встали в ноль 23-го, когда исчерпались кулдауны веток, стоящих в `pick()` выше pSEO.

Здесь проверяется РАЗЛИЧЕНИЕ и в обе стороны: до провала тема выдаётся, после — пропускается,
а по истечении окна возвращается. Сеть не участвует.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from pipeline import dedup


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(dedup, "_DB_PATH", tmp_path / "t.db")
    return tmp_path / "t.db"


def test_a_topic_with_no_failure_is_not_cooled(db):
    assert dedup.topic_failed_recently(999, "youtube") is False


def test_a_failed_topic_is_cooled(db):
    dedup.record_topic_failure(999, "youtube", "RuntimeError: only 0 real asset(s)")
    assert dedup.topic_failed_recently(999, "youtube") is True


def test_the_cooldown_is_per_channel(db):
    """Тема, упавшая на youtube, не обязана падать на tiktok — там другой дедуп медиа."""
    dedup.record_topic_failure(999, "youtube")
    assert dedup.topic_failed_recently(999, "tiktok") is False


def test_the_cooldown_expires(db):
    """Это кулдаун, а не бан: причина может пройти сама (провайдер, квота, сеть)."""
    dedup.record_topic_failure(999, "youtube")
    old = (datetime.now(timezone.utc)
           - timedelta(hours=dedup._FAILURE_TTL_HOURS + 1)).isoformat()
    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE topic_failure SET last_failed=? WHERE topic_id=999", (old,))
    assert dedup.topic_failed_recently(999, "youtube") is False


def test_the_counter_accumulates(db):
    """По счётчику видно тему, которая падает СИСТЕМАТИЧЕСКИ, а не разово."""
    for _ in range(3):
        dedup.record_topic_failure(999, "youtube", "boom")
    with sqlite3.connect(db) as cx:
        n = cx.execute("SELECT fails FROM topic_failure WHERE topic_id=999").fetchone()[0]
    assert n == 3, "провалы не копятся — систематический отказ неотличим от разового"


def test_the_selector_skips_a_failed_topic(monkeypatch, db):
    """Главное утверждение: селектор обязан ПРОПУСТИТЬ упавшую и выдать следующую."""
    from pipeline import fetch_topic

    pages = [{"id": 9, "title": "stuck"}, {"id": 10, "title": "next"}]
    monkeypatch.setattr(fetch_topic, "_pseo_pages", lambda: pages)
    monkeypatch.setattr(fetch_topic.compliance, "topic_safe", lambda p: True)
    monkeypatch.setattr(fetch_topic, "_enrich_pseo", lambda p, platform: p)
    monkeypatch.setattr(fetch_topic.dedup, "topic_used_recently", lambda i, c: False)

    import random
    assert fetch_topic._pick_pseo("youtube", random.Random(0))["id"] == 9

    dedup.record_topic_failure(9, "youtube", "only 0 real asset(s)")
    got = fetch_topic._pick_pseo("youtube", random.Random(0))
    assert got["id"] == 10, (
        "селектор снова выдал упавшую тему — это и есть живой замок, стоивший 169 слотов")
