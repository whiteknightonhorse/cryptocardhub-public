"""G17 (Y52) — a visuals outage degrades or fails, but never ships silently.

WHY THIS GATE EXISTS: the provider chain was wrapped so that one blocked source
(Unsplash timing out) could not abort a fetch the other sources could already
satisfy. That wrapper, applied naively, swallows the total-outage case too —
and `fetch()` ends by topping up whatever it is short with solid-colour
placeholder JPEGs. So "every provider is down" would not raise; it would return
a full set of placeholders and render a video of coloured slides with a
perfectly valid duration and a perfectly clickable link. That is the Y49 failure
shape exactly (a shortfall padded over in silence rather than raised).

Both halves are asserted here, because either one alone is a different bug:
partial failure MUST survive, total failure MUST raise.
"""
from __future__ import annotations

import pytest

from pipeline import fetch_visuals


def _hit(n: int) -> list[dict]:
    return [{"kind": "photo", "url": f"https://example.test/{n}.jpg",
             "source": "test"}]


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    """Real keys present, so fetch() takes the network branch and not the
    dry-run placeholder shortcut."""
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "test-key")


def _boom(*_a, **_k):
    raise TimeoutError("provider is down")


def test_one_dead_provider_does_not_sink_the_fetch(tmp_path, monkeypatch):
    """The whole reason the wrapper exists: Unsplash being blocked must not
    lose footage Pexels already returned."""
    monkeypatch.setattr(fetch_visuals, "_pexels_videos", lambda q, w: _hit(1))
    monkeypatch.setattr(fetch_visuals, "_pexels_photos", _boom)
    monkeypatch.setattr(fetch_visuals, "_unsplash_photos", _boom)
    monkeypatch.setattr(fetch_visuals, "_download", lambda url, dest: dest.write_bytes(b"x"))
    monkeypatch.setattr(fetch_visuals.dedup, "record_media", lambda *a, **k: None)

    out = fetch_visuals.fetch("crypto card", want=1, out_dir=tmp_path)
    assert len(out) == 1
    assert out[0]["source"] == "test", "real footage was dropped for a placeholder"


def test_a_total_outage_raises_instead_of_shipping_slides(tmp_path, monkeypatch):
    """LAW: no real footage at all is a hard failure. If this ever returns, it
    returns `want` solid-colour JPEGs and the video looks broken to every
    viewer while every downstream gate stays green."""
    monkeypatch.setattr(fetch_visuals, "_pexels_videos", _boom)
    monkeypatch.setattr(fetch_visuals, "_pexels_photos", _boom)
    monkeypatch.setattr(fetch_visuals, "_unsplash_photos", _boom)

    with pytest.raises(RuntimeError, match="all visual providers failed"):
        fetch_visuals.fetch("crypto card", want=6, out_dir=tmp_path)


def test_empty_results_refuse_the_slot_and_name_the_cause(tmp_path, monkeypatch):
    """REVERSED BY Y58 (2026-08-12). This test used to assert the opposite —
    that a niche query with no matches is "the placeholder top-up's actual
    job". That law published `Xm2-vLgwrc8` as four solid-blue slides.

    An honest empty result is still not an outage — the distinction the old
    test defended is kept, and it survives in the reported cause
    (`empty_results`, not `provider_error`). What changed is the consequence:
    a slot with nothing real to show is refused, not padded. A video of
    solid-colour slides is worse than a missed slot in every measure,
    including YouTube's own inauthentic-content policy.
    """
    monkeypatch.setattr(fetch_visuals, "_pexels_videos", lambda q, w: [])
    monkeypatch.setattr(fetch_visuals, "_pexels_photos", lambda q, w: [])
    monkeypatch.setattr(fetch_visuals, "_unsplash_photos", lambda q, w: [])

    with pytest.raises(RuntimeError, match="Refusing the slot"):
        fetch_visuals.fetch("no such footage anywhere", want=3, out_dir=tmp_path)
    assert list(tmp_path.glob("placeholder_*")) == []
