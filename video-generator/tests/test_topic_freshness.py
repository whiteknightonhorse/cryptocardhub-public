"""Y49 P1-3 gates — 21-day topic cooldown, replayed against the channel's own
publication history, and news-ingest wired in as a topic source.

fixtures/publish_history.json is a snapshot of the live publish_log: 213
publications over 65 unique topics, 148 repeats. 94 of those repeats came back
within 21 days, only 1 within 7 — which is why the old 7-day window let
essentially every repeat through.
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipeline import dedup, fetch_topic

HISTORY = json.loads(
    (Path(__file__).parent / "fixtures" / "publish_history.json").read_text(encoding="utf-8")
)

# The law, stated here and not read back from the code under test: a topic seen
# in the last 21 days does not publish again.
COOLDOWN_DAYS = 21


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """dedup on a throwaway DB — never the live state/published.db."""
    monkeypatch.setattr(dedup, "_DB_PATH", tmp_path / "published.db")
    return tmp_path


def _seed_used(days_ago: float, topic_id: int, channel: str = "youtube") -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    with dedup._conn() as cx:
        cx.execute(
            "INSERT OR REPLACE INTO topic_usage(topic_id, channel, last_used) VALUES (?,?,?)",
            (topic_id, channel, stamp),
        )


def test_every_historical_repeat_inside_the_window_is_blocked(ledger):
    """Replay the real repeat intervals: nothing reused within the cooldown
    may pass, and topics older than it must still be reachable."""
    blocked_inside = allowed_outside = inside = outside = 0
    for i, rep in enumerate(HISTORY["repeats"]):
        gap = rep["gap_days"]
        _seed_used(gap, topic_id=i, channel=rep["channel"])
        used = dedup.topic_used_recently(i, rep["channel"])
        if gap <= COOLDOWN_DAYS:
            inside += 1
            blocked_inside += used
        else:
            outside += 1
            allowed_outside += not used
    assert inside and outside, "fixture must contain repeats on both sides"
    assert blocked_inside == inside, (
        f"{inside - blocked_inside} of {inside} historical repeats inside the "
        f"{COOLDOWN_DAYS}-day cooldown would still publish"
    )
    assert allowed_outside == outside, "the window must not block older topics"


def _write_news(dirpath: Path, slug: str, hours_ago: float, title: str) -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    fm = {"title": title, "slug": slug, "bucket": "news",
          "primary_keyword": "eu stablecoin rule", "news_published_at": stamp}
    body = "\n".join(f"{k}: {json.dumps(v)}" for k, v in fm.items())
    (dirpath / f"{slug}.md").write_text(f"---\n{body}\n---\n\ntext\n", encoding="utf-8")


@pytest.fixture
def news_dir(tmp_path, monkeypatch):
    d = tmp_path / "pseo"
    d.mkdir()
    monkeypatch.setattr(fetch_topic, "_CONTENT_DIR", d)
    return d


def test_fresh_news_item_becomes_the_topic(ledger, news_dir, monkeypatch):
    _write_news(news_dir, "news-eu-stablecoin-rule", 12, "EU sets stablecoin card rule")
    monkeypatch.setenv("NEWS_POOL_RATIO", "1.0")
    topic = fetch_topic.pick("youtube", seed=1)
    assert topic["pool"] == "news"
    assert topic["title"] == "EU sets stablecoin card rule"
    assert topic["target_seconds"] and topic["target_words"], "must be render-ready"


def test_stale_and_used_news_items_are_skipped(ledger, news_dir, monkeypatch):
    _write_news(news_dir, "news-old", 24 * 30, "A month-old story")
    _write_news(news_dir, "news-recent", 6, "A six-hour-old story")
    _seed_used(2.0, fetch_topic._news_id("news-recent"))
    assert fetch_topic._pick_news("youtube", __import__("random").Random(1)) is None
    monkeypatch.setenv("NEWS_POOL_RATIO", "1.0")
    assert fetch_topic.pick("youtube", seed=1)["pool"] != "news"  # falls back, no crash


def test_empty_news_catalog_does_not_break_the_pipeline(ledger, news_dir, monkeypatch):
    monkeypatch.setenv("NEWS_POOL_RATIO", "1.0")
    topic = fetch_topic.pick("youtube", seed=3)
    assert topic["pool"] in ("finance", "viral")
