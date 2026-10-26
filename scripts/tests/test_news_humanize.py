"""G37 wiring in news-ingest (CCH-HUM-05): _publish_item must emit the REWRITTEN payload,
and a refusal must be a JSONL row with its reason, never a silent skip (Y55)."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
_SPEC = importlib.util.spec_from_file_location("news_ingest_h", ROOT / "scripts" / "news-ingest.py")
ni = importlib.util.module_from_spec(_SPEC)
sys.modules["news_ingest_h"] = ni
_SPEC.loader.exec_module(ni)

import humanize_gate as hg  # noqa: E402


def _item():
    return ni.Item(title="FCA fines a crypto card firm", source_name="FCA",
                   source_url="https://example.org/1", source_category="regulator",
                   summary="s", priority=1, published_at=datetime.now(timezone.utc), body="")


def _payload():
    return {"title": "T", "description": "D", "slug": "fca-fine", "intro_paragraph": "Intro.",
            "body_markdown": "Actually, a very robust landscape.", "what_to_watch": []}


def _setup(tmp_path, monkeypatch, hres_factory):
    monkeypatch.setattr(ni, "CONTENT_DIR", tmp_path / "content")
    (tmp_path / "content").mkdir()
    monkeypatch.setattr(ni, "LOG_DIR", tmp_path / "log")
    monkeypatch.setattr(ni, "STATE_DB", tmp_path / "seen.db")
    monkeypatch.setattr(ni, "_fetch_full_body", lambda it, ua, to: "x" * 300)
    monkeypatch.setattr(ni, "_call_claude", lambda it, model: _payload())
    monkeypatch.setattr(ni.humanize_gate, "humanize", hres_factory)
    return ni._state()


def _log_rows(tmp_path):
    return [json.loads(l) for f in (tmp_path / "log").glob("*.jsonl")
            for l in f.read_text(encoding="utf-8").splitlines()]


def test_published_page_contains_the_rewrite(tmp_path, monkeypatch):
    def fake(payload):
        p = dict(payload, body_markdown="Plain rewritten body.")
        return hg.HumanizeResult(p, 3, 0, rewritten=True)

    db = _setup(tmp_path, monkeypatch, fake)
    assert ni._publish_item(_item(), db, {"user_agent": "ua", "timeout_s": 1}) is True
    md = (tmp_path / "content" / "news-fca-fine.md").read_text(encoding="utf-8")
    assert "Plain rewritten body." in md
    assert "robust landscape" not in md


def test_refused_humanize_writes_no_page_and_logs_the_reason(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch,
                lambda payload: hg.HumanizeResult(payload, 3, 3, refused_reason="G37:score_after:9.00"))
    assert ni._publish_item(_item(), db, {"user_agent": "ua", "timeout_s": 1}) is False
    assert list((tmp_path / "content").glob("*.md")) == []
    refused = [r for r in _log_rows(tmp_path) if r["event"] == "refused"]
    assert len(refused) == 1
    assert refused[0]["gate"] == "G37" and refused[0]["reason"] == "G37:score_after:9.00"
