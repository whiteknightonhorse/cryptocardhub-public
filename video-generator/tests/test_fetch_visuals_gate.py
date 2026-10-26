"""G17 — a live fetch may never pad a shortfall with placeholders.

Why this gate exists: `Xm2-vLgwrc8` was published as four solid-blue slides.
Nothing had failed — every provider answered 200, keys were live. The dedup
ledger had been eaten by the long-form pilots (144 media URLs burned in a day
against a normal ~8), search depth was a fixed one page, so after dedup the
chain returned 0 and a top-up loop padded the slot with dry-run placeholders
"so render can finish".

Every expectation below is a literal. The tests drive the REAL `fetch()` with
`_http_get` mocked at the network boundary — testing the wiring, not a helper
(Y53: a correct helper nobody calls is still a broken pipeline).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import dedup, fetch_visuals
from pipeline.publish.youtube import refuse_placeholder_build


class _Resp:
    def __init__(self, status: int, payload: dict | None = None, body: bytes = b"x" * 32):
        self.status_code = status
        self._payload = payload or {}
        self.content = body

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(f"HTTP {self.status_code}")


def _api(n: int = 20):
    """Mock the three endpoints the way the real ones behave: distinct URLs per
    provider, and page 2+ empty so pagination terminates. A mock that returns
    the same page forever tests the test, not the code."""
    def _get(url, headers=None, params=None, timeout=20):
        page = (params or {}).get("page", 1)
        if "/videos/search" in url:
            return _Resp(200, {"videos": []})
        if page > 1:
            return _Resp(200, {"photos": [], "results": []})
        if "/v1/search" in url:
            return _Resp(200, {"photos": [{"src": {"large2x": f"https://img.example/px{i}.jpg"}}
                                          for i in range(n)]})
        if "/search/photos" in url:
            return _Resp(200, {"results": [{"urls": {"regular": f"https://img.example/us{i}.jpg"}}
                                           for i in range(n)]})
        return _Resp(200, {})  # the download call

    return _get


@pytest.fixture(autouse=True)
def _logs_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("VG_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "test-key")


def _log_lines(tmp_path) -> list[dict]:
    d = tmp_path / "logs"
    out: list[dict] = []
    for f in d.glob("*.jsonl"):
        out += [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    return out


# --- G17a: the shortfall law -------------------------------------------------

def test_all_deduped_refuses_and_writes_no_placeholder(tmp_path, monkeypatch):
    """The exact 03:21 situation: providers answer 200, everything is deduped."""
    monkeypatch.setattr(fetch_visuals, "_http_get", _api())
    monkeypatch.setattr(dedup, "media_used_recently", lambda url: True)
    out_dir = tmp_path / "media"

    with pytest.raises(RuntimeError) as e:
        fetch_visuals.fetch("best crypto card india", want=4, out_dir=out_dir,
                            platform="youtube", topic_id=2329861)

    assert "Refusing the slot" in str(e.value)
    assert list(out_dir.glob("placeholder_*")) == []
    run = [r for r in _log_lines(tmp_path) if "zero_cause" in r]
    assert run and run[-1]["zero_cause"] == "all_deduped"
    assert run[-1]["placeholders"] == 0


def test_min_real_threshold_is_two(tmp_path, monkeypatch):
    """One real asset is a defect, two is a slot. The literal is the law."""
    assert fetch_visuals.MIN_REAL == 2

    allowed = {"https://img.example/px0.jpg"}  # exactly one asset survives dedup
    monkeypatch.setattr(fetch_visuals, "_http_get", _api())
    monkeypatch.setattr(dedup, "media_used_recently", lambda url: url not in allowed)
    monkeypatch.setattr(dedup, "record_media", lambda *a, **k: None)

    with pytest.raises(RuntimeError) as e:
        fetch_visuals.fetch("q", want=4, out_dir=tmp_path / "m1")
    assert "only 1 real asset" in str(e.value)


def test_two_real_assets_are_enough(tmp_path, monkeypatch):
    """Control: the fix must not refuse a slot render can actually build."""
    allowed = {"https://img.example/px0.jpg", "https://img.example/us0.jpg"}
    monkeypatch.setattr(fetch_visuals, "_http_get", _api())
    monkeypatch.setattr(dedup, "media_used_recently", lambda url: url not in allowed)
    monkeypatch.setattr(dedup, "record_media", lambda *a, **k: None)

    got = fetch_visuals.fetch("q", want=4, out_dir=tmp_path / "m2")
    assert len(got) == 2
    assert all(g["source"] != "placeholder" for g in got)


# --- G17b: the publisher boundary -------------------------------------------

def test_publisher_refuses_a_placeholder_build(tmp_path):
    d = tmp_path / "topic_1"
    (d / "media").mkdir(parents=True)
    (d / "media" / "placeholder_00.jpg").write_bytes(b"x")
    (d / "final.mp4").write_bytes(b"x")
    with pytest.raises(ValueError) as e:
        refuse_placeholder_build(d / "final.mp4")
    assert "placeholder" in str(e.value)


def test_publisher_passes_a_real_build(tmp_path):
    d = tmp_path / "topic_2"
    (d / "media").mkdir(parents=True)
    (d / "media" / "clip_00.mp4").write_bytes(b"x")
    (d / "final.mp4").write_bytes(b"x")
    refuse_placeholder_build(d / "final.mp4")  # must not raise


# --- G17c: a zero names its cause -------------------------------------------

def test_non_200_names_itself_in_the_log(tmp_path, monkeypatch):
    """401 must be distinguishable from 'nothing found' and 'all deduped'."""
    monkeypatch.setattr(fetch_visuals, "_http_get", lambda *a, **k: _Resp(401, {}))
    monkeypatch.setattr(dedup, "media_used_recently", lambda url: False)

    with pytest.raises(RuntimeError):
        fetch_visuals.fetch("q", want=4, out_dir=tmp_path / "m3")

    lines = _log_lines(tmp_path)
    run = [r for r in lines if "zero_cause" in r]
    assert run and run[-1]["zero_cause"] == "non_200"
    assert any(r.get("status") == 401 for r in lines if "provider" in r)


def test_empty_results_is_not_all_deduped(tmp_path, monkeypatch):
    """Two different zeros must not collapse into one word."""
    monkeypatch.setattr(fetch_visuals, "_http_get", _api(n=0))
    monkeypatch.setattr(dedup, "media_used_recently", lambda url: False)

    with pytest.raises(RuntimeError):
        fetch_visuals.fetch("q", want=4, out_dir=tmp_path / "m4")

    run = [r for r in _log_lines(tmp_path) if "zero_cause" in r]
    assert run and run[-1]["zero_cause"] == "empty_results"


# --- dry-run keeps its placeholders -----------------------------------------

def test_dry_run_still_gets_placeholders(tmp_path):
    got = fetch_visuals.fetch("q", want=3, out_dir=tmp_path / "m5", dry_run=True)
    assert len(got) == 3
    assert all(g["source"] == "placeholder" for g in got)


# --- T-0140 (ruling-1): NoVisualsError carries the cause, so a caller can ---
# tell a page property (query structurally unfindable) apart from a machine
# failure, without weakening Y58's refusal at all.

def test_page_property_causes_is_pinned_to_empty_results_only():
    assert fetch_visuals.PAGE_PROPERTY_CAUSES == frozenset({"empty_results"})


def test_empty_results_raises_novisualserror_with_its_cause(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_visuals, "_http_get", _api(n=0))
    monkeypatch.setattr(dedup, "media_used_recently", lambda url: False)

    with pytest.raises(fetch_visuals.NoVisualsError) as e:
        fetch_visuals.fetch("q", want=4, out_dir=tmp_path / "m6")

    assert isinstance(e.value, RuntimeError)
    assert e.value.cause == "empty_results"
    assert e.value.cause in fetch_visuals.PAGE_PROPERTY_CAUSES


def test_all_deduped_is_a_novisualserror_but_not_a_page_property(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_visuals, "_http_get", _api())
    monkeypatch.setattr(dedup, "media_used_recently", lambda url: True)

    with pytest.raises(fetch_visuals.NoVisualsError) as e:
        fetch_visuals.fetch("q", want=4, out_dir=tmp_path / "m7")

    assert e.value.cause == "all_deduped"
    assert e.value.cause not in fetch_visuals.PAGE_PROPERTY_CAUSES


def test_total_provider_failure_is_a_bare_runtimeerror_not_novisualserror(tmp_path, monkeypatch):
    """Y58's other refusal (all three providers threw) is a machine failure —
    it must stay the plain RuntimeError it always was, never the page-property
    class, or a transient outage would get mistaken for a 14-day cooldown."""
    def _boom(*a, **k):
        raise ConnectionError("network down")
    monkeypatch.setattr(fetch_visuals, "_http_get", _boom)

    with pytest.raises(RuntimeError) as e:
        fetch_visuals.fetch("q", want=4, out_dir=tmp_path / "m8")

    assert not isinstance(e.value, fetch_visuals.NoVisualsError)
    assert "all visual providers failed" in str(e.value)
