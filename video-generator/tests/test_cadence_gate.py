"""G4 (P0-4) — the scoring verdict must be able to stop publication.

Until now the verdict fed nothing but the pattern weights: neither cadence,
nor topic choice, nor "should we publish at all" depended on it.

Mutation to prove these tests bite: delete the median check from
analytics.channel_health (always `suppressed = False`) → the suppression and
fail-closed tests go red.
"""
from __future__ import annotations
import json
from datetime import timedelta

import pytest

from pipeline import analytics
from conftest import now_utc, write_scored_events


def _history(log_dir, views, n=20, success_every=None):
    write_scored_events(log_dir, [
        {"video_id": f"v{i}", "views": views,
         "success": bool(success_every and i % success_every == 0)}
        for i in range(n)
    ])


def _schedule(tmp_path, platform, fired):
    from pipeline import schedule
    today = now_utc().astimezone(schedule.ET).date().isoformat()
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "schedule.json").write_text(json.dumps({
        "by_platform": {platform: [
            {"slot_id": f"{platform}-{today}-{i+1}", "platform": platform,
             "due_at_iso": f"{today}T12:0{i}:00-04:00",
             "due_at_utc": f"{today}T16:0{i}:00+00:00", "fired": i < fired}
            for i in range(2)
        ]}
    }), encoding="utf-8")


def test_no_data_is_fail_closed(sandbox):
    health = analytics.channel_health()
    assert health["suppressed"] is True and health["reason"] == "no_data"


def test_median_below_ten_suppresses(sandbox):
    _history(analytics._LOG_DIR, views=3)
    health = analytics.channel_health()
    assert health["median_views"] == 3
    assert health["suppressed"] is True


def test_healthy_channel_is_not_suppressed(sandbox):
    _history(analytics._LOG_DIR, views=50)
    health = analytics.channel_health()
    assert health["median_views"] == 50
    assert health["suppressed"] is False


def test_stale_events_outside_the_window_do_not_count(sandbox):
    write_scored_events(analytics._LOG_DIR,
                        [{"video_id": f"old{i}", "views": 500} for i in range(20)],
                        base_ts=now_utc() - timedelta(days=30))
    assert analytics.channel_health()["suppressed"] is True


def test_suppressed_channel_gets_one_slot_a_day(sandbox, monkeypatch):
    from pipeline import _config
    monkeypatch.setattr(_config, "root", lambda: sandbox)
    _history(analytics._LOG_DIR, views=3)

    _schedule(sandbox, "youtube", fired=0)
    allowed, payload = analytics.cadence_allowed("youtube")
    assert allowed is True and payload["fired_today"] == 0

    _schedule(sandbox, "youtube", fired=1)
    allowed, payload = analytics.cadence_allowed("youtube")
    assert allowed is False and payload["cap"] == 1


def test_healthy_channel_keeps_full_cadence(sandbox, monkeypatch):
    from pipeline import _config
    monkeypatch.setattr(_config, "root", lambda: sandbox)
    _history(analytics._LOG_DIR, views=50)
    _schedule(sandbox, "youtube", fired=2)
    allowed, _ = analytics.cadence_allowed("youtube")
    assert allowed is True


def test_alert_fires_once_per_day(sandbox, monkeypatch):
    """Not a silent skip — but not 144 identical alerts a day either."""
    sent = []
    monkeypatch.setattr(analytics, "_tg_alert", sent.append)
    payload = {"median_views": 3, "success_rate": 0.0, "n": 20}
    assert analytics._cadence_alert_once("youtube", payload) is True
    assert analytics._cadence_alert_once("youtube", payload) is False
    assert len(sent) == 1
