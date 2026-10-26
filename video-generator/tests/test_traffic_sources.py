"""E-15: pull_traffic_sources() must never return a bare {} — a caller that
cannot tell "API unreachable" from "zero EXT_URL traffic" would silently
score H1 against a hole in the instrument, the same failure class as
pull_ga4_register_clicks (analytics.py's own docstring, YOUTUBE_REFRESH_TOKEN
scope story).

Mutation to prove this test bites: make pull_traffic_sources return {} on
missing creds instead of the typed status dict -> this test goes red.
"""
from __future__ import annotations

from pipeline import analytics


def test_no_creds_is_instrument_dead_not_a_bare_dict(sandbox, monkeypatch):
    monkeypatch.delenv("YOUTUBE_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)
    monkeypatch.delenv("YOUTUBE_CLIENT_SECRET", raising=False)
    result = analytics.pull_traffic_sources("2026-08-15", "2026-09-13")
    assert result == {"status": "instrument_dead",
                       "error": "YouTube OAuth creds missing"}
