"""T-0140 (ruling-1) — the boundary that classifies a shorts-pipeline failure.

`run_pipeline`'s except-block (main.py) already records every failure via
`dedup.record_topic_failure` (Y49 §6 / the pSEO cooldown). What it did NOT do
is tell a page property (the query is fixed by the page's own frontmatter, a
retry in 36h repeats the same paid TTS synth for the same `empty_results`
zero) apart from a machine failure (provider/network broke, will likely clear
on its own). This drives the REAL `run_pipeline` — Y53's rule restated: a
helper that classifies correctly and a caller nobody wires it into is still a
broken pipeline, so the wiring is what gets tested here, not `NoVisualsError`
in isolation (see tests/test_fetch_visuals_gate.py for that).
"""
from __future__ import annotations

import pytest

from pipeline import dedup, fetch_topic, fetch_visuals
from pipeline import main as pipeline_main


def _topic(id_: int = 5) -> dict:
    return {"id": id_, "class": "MOFU", "target_seconds": 30.0, "title": "t",
            "primary": "t"}


@pytest.fixture()
def spy(monkeypatch):
    calls: list[dict] = []

    def _record(topic_id, channel, reason="", page_property=False):
        calls.append({"topic_id": topic_id, "channel": channel,
                      "reason": reason, "page_property": page_property})

    monkeypatch.setattr(pipeline_main.dedup, "record_topic_failure", _record)
    return calls


def test_a_page_property_cause_is_classified_as_page_property(monkeypatch, spy, tmp_path):
    monkeypatch.setattr(pipeline_main.fetch_topic, "pick", lambda platform, seed=None: _topic())
    monkeypatch.setattr(pipeline_main, "_out_dir", lambda *a, **k: tmp_path / "out")

    def _boom(platform, topic, out_dir, *, dry_run=False):
        raise fetch_visuals.NoVisualsError("only 0 real asset(s)", cause="empty_results")
    monkeypatch.setattr(pipeline_main, "_run_after_topic", _boom)

    with pytest.raises(RuntimeError):
        pipeline_main.run_pipeline("youtube")

    assert spy[-1]["page_property"] is True, (
        "empty_results is a page property (PAGE_PROPERTY_CAUSES) — the wiring "
        "must say so, or the topic gets the 36h machine-failure cooldown "
        "instead of the 14-day one and re-synthesizes TTS on the next tick")


def test_a_machine_failure_is_not_classified_as_page_property(monkeypatch, spy, tmp_path):
    monkeypatch.setattr(pipeline_main.fetch_topic, "pick", lambda platform, seed=None: _topic())
    monkeypatch.setattr(pipeline_main, "_out_dir", lambda *a, **k: tmp_path / "out")

    def _boom(platform, topic, out_dir, *, dry_run=False):
        raise RuntimeError("all visual providers failed")
    monkeypatch.setattr(pipeline_main, "_run_after_topic", _boom)

    with pytest.raises(RuntimeError):
        pipeline_main.run_pipeline("youtube")

    assert spy[-1]["page_property"] is False, (
        "a bare RuntimeError (no .cause) must never be classified as a page "
        "property — that would give a transient outage a 14-day cooldown")


def test_all_deduped_is_not_classified_as_page_property(monkeypatch, spy, tmp_path):
    """Control: NoVisualsError itself is not sufficient, only its cause is —
    all_deduped is transient supply, not a fixed property of the page."""
    monkeypatch.setattr(pipeline_main.fetch_topic, "pick", lambda platform, seed=None: _topic())
    monkeypatch.setattr(pipeline_main, "_out_dir", lambda *a, **k: tmp_path / "out")

    def _boom(platform, topic, out_dir, *, dry_run=False):
        raise fetch_visuals.NoVisualsError("only 0 real asset(s)", cause="all_deduped")
    monkeypatch.setattr(pipeline_main, "_run_after_topic", _boom)

    with pytest.raises(RuntimeError):
        pipeline_main.run_pipeline("youtube")

    assert spy[-1]["page_property"] is False
