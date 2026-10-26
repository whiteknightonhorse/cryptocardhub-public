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


# --- T-0140 (ruling-1): закон 08-24 был в ОДНОМ из пяти селекторов ----------
# H1 (VG_MODE=h1, боевой режим) получил кулдаун провала последним и позже всех, поэтому
# `refer-friend` синтезировался повторно каждый тик 36 часов без остановки. Три остальных
# селектора (finance x2, viral, news) спят в проде сегодня, но незащищены — как только
# VG_MODE вернётся к смеси, тот же замок повторится там.

def test_finance_selector_skips_a_failed_topic(monkeypatch, db):
    from pipeline import fetch_topic
    import random

    pool = [{"id": 9, "class": "TOFU"}, {"id": 10, "class": "TOFU"}]
    monkeypatch.setattr(fetch_topic, "_pool", lambda: {"topics": pool})
    monkeypatch.setattr(fetch_topic, "_weighted_class", lambda platform, rng: "TOFU")
    monkeypatch.setattr(fetch_topic.compliance, "topic_safe", lambda t: True)
    monkeypatch.setattr(fetch_topic, "_enrich_finance", lambda t, platform: t)
    monkeypatch.setattr(fetch_topic.dedup, "topic_used_recently", lambda i, c: False)

    assert fetch_topic._pick_finance("youtube", random.Random(0))["id"] == 9

    dedup.record_topic_failure(9, "youtube", "only 0 real asset(s)")
    got = fetch_topic._pick_finance("youtube", random.Random(0))
    assert got["id"] == 10, "finance-селектор снова выдал упавшую тему"


def test_viral_selector_skips_a_failed_topic(monkeypatch, db):
    from pipeline import fetch_topic
    import random

    vp = {"topics": [
        {"id": 9, "platforms": ["youtube"], "cta_bridge_difficulty": 1,
         "viral_potential": "high", "disabled": False},
        {"id": 10, "platforms": ["youtube"], "cta_bridge_difficulty": 1,
         "viral_potential": "high", "disabled": False},
    ]}
    monkeypatch.setattr(fetch_topic, "_viral_pool", lambda: vp)
    monkeypatch.setattr(fetch_topic.compliance, "topic_safe", lambda t: True)
    monkeypatch.setattr(fetch_topic, "_enrich_viral", lambda t, platform: t)
    monkeypatch.setattr(fetch_topic.dedup, "topic_used_recently", lambda i, c: False)

    assert fetch_topic._pick_viral("youtube", random.Random(0))["id"] == 9

    dedup.record_topic_failure(9, "youtube", "only 0 real asset(s)")
    got = fetch_topic._pick_viral("youtube", random.Random(0))
    assert got["id"] == 10, "viral-селектор снова выдал упавшую тему"


def test_news_selector_skips_a_failed_topic(monkeypatch, db):
    from pipeline import fetch_topic
    import random

    articles = [{"id": 9, "title": "stuck"}, {"id": 10, "title": "next"}]
    monkeypatch.setattr(fetch_topic, "_news_articles", lambda: articles)
    monkeypatch.setattr(fetch_topic.compliance, "topic_safe", lambda a: True)
    monkeypatch.setattr(fetch_topic, "_enrich_news", lambda a, platform: a)
    monkeypatch.setattr(fetch_topic.dedup, "topic_used_recently", lambda i, c: False)

    assert fetch_topic._pick_news("youtube", random.Random(0))["id"] == 9

    dedup.record_topic_failure(9, "youtube", "only 0 real asset(s)")
    got = fetch_topic._pick_news("youtube", random.Random(0))
    assert got["id"] == 10, "news-селектор снова выдал упавшую тему"


def test_h1_selector_skips_a_failed_topic(monkeypatch, db):
    """Прод сегодня (VG_MODE=h1) — это и есть путь, что жёг чужую квоту ElevenLabs."""
    from pipeline import fetch_topic
    import random

    candidates = [{"id": 9, "slug": "stuck"}, {"id": 10, "slug": "next"}]
    monkeypatch.setattr(fetch_topic, "_h1_candidates", lambda platform: candidates)
    monkeypatch.setattr(fetch_topic.compliance, "topic_safe", lambda p: True)
    monkeypatch.setattr(fetch_topic, "_enrich_h1", lambda p, platform: p)

    assert fetch_topic._pick_h1("youtube", random.Random(0))["id"] == 9

    dedup.record_topic_failure(9, "youtube", "only 0 real asset(s)")
    got = fetch_topic._pick_h1("youtube", random.Random(0))
    assert got["id"] == 10, "H1-селектор снова выдал упавшую тему — живая утечка квоты"


# --- T-0140 ruling-2 (M2): фолбэк-проход finance тоже обязан охлаждать -----
# `_pick_finance` делает ДВА прохода: сначала кандидаты целевого класса, затем — фолбэк
# по всему пулу. Ruling-1 требовал перенос строки `topic_failed_recently` в оба, но
# `test_finance_selector_skips_a_failed_topic` выше кладёт в пул только один класс —
# он никогда не доходит до второго цикла, и мутация «строка убрана только из фолбэка»
# оставалась зелёной (REJECT ruling-2, пункт 1). Здесь — фейковый rng с фиксированным
# порядком `shuffle`/`sample`, чтобы утверждение не зависело от того, какой элемент
# случайный `Random(seed)` поставит первым.

def test_finance_selector_fallback_pass_skips_a_failed_topic(monkeypatch, db):
    from pipeline import fetch_topic

    class _FixedOrderRng:
        def shuffle(self, lst):
            pass  # keep insertion order

        def sample(self, pool, k):
            return list(pool)  # keep insertion order

    # id=9 — единственный кандидат ЦЕЛЕВОГО класса (TOFU), поэтому первый проход его
    # исключает по кулдауну и исчерпывается без результата. id=10 — другого класса
    # (MOFU), достижим только фолбэком.
    pool = [{"id": 9, "class": "TOFU"}, {"id": 10, "class": "MOFU"}]
    monkeypatch.setattr(fetch_topic, "_pool", lambda: {"topics": pool})
    monkeypatch.setattr(fetch_topic, "_weighted_class", lambda platform, rng: "TOFU")
    monkeypatch.setattr(fetch_topic.compliance, "topic_safe", lambda t: True)
    monkeypatch.setattr(fetch_topic, "_enrich_finance", lambda t, platform: t)
    monkeypatch.setattr(fetch_topic.dedup, "topic_used_recently", lambda i, c: False)

    dedup.record_topic_failure(9, "youtube", "only 0 real asset(s)")

    got = fetch_topic._pick_finance("youtube", _FixedOrderRng())
    assert got["id"] == 10, (
        "фолбэк-проход finance-селектора снова выдал упавшую тему — "
        "второй цикл не охлаждается (M2, ruling-2)")


# --- T-0140: свойство страницы остывает 14 суток, не 36 часов ---------------

def test_page_property_failure_cools_for_fourteen_days_not_thirty_six_hours(db):
    dedup.record_topic_failure(999, "youtube", "empty_results", page_property=True)

    still_37h = (datetime.now(timezone.utc) - timedelta(hours=37)).isoformat()
    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE topic_failure SET last_failed=? WHERE topic_id=999", (still_37h,))
    assert dedup.topic_failed_recently(999, "youtube") is True, (
        "37h > _FAILURE_TTL_HOURS=36 но < 14 суток — свойство страницы обязано ещё остывать")

    gone_15d = (datetime.now(timezone.utc) - timedelta(hours=15 * 24)).isoformat()
    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE topic_failure SET last_failed=? WHERE topic_id=999", (gone_15d,))
    assert dedup.topic_failed_recently(999, "youtube") is False, (
        "15 суток > 14-суточного потолка — окно обязано истечь")
