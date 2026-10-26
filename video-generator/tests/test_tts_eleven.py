"""E-19 — ElevenLabs adapter + shared-quota guard.

The quota is SHARED with another tenant on this machine (FABLE_DEFYCARD_PART2
§8.1) — the guard must read the live `/v1/user/subscription` on every call,
never a local counter (Y49's law: a local counter cannot see the other
tenant's usage, and a test that mutates the guard into reading its own state
instead of the API must go red).

Every expectation below is a literal from the E-19 acceptance line, driven
through the real `synthesize()` with `_http_get`/`_http_post` mocked at the
network boundary — not a helper in isolation (Y53).
"""
from __future__ import annotations

import base64
import json

import pytest

from pipeline import tts_eleven


class _Resp:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(f"HTTP {self.status_code}")


def _subscription(limit: int, count: int):
    calls = []

    def _get(url, headers):
        calls.append(url)
        assert url.endswith("/v1/user/subscription")
        assert headers["xi-api-key"] == "test-key"
        return _Resp(200, {"character_limit": limit, "character_count": count})

    return _get, calls


_FAKE_ALIGNMENT = {
    "characters": list("hi there"),
    "character_start_times_seconds": [0.0, 0.1, 0.2, 0.2, 0.3, 0.4, 0.5, 0.6],
    "character_end_times_seconds":   [0.1, 0.2, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7],
}


def _tts_post(calls_out):
    def _post(url, headers, body, params):
        calls_out.append((url, params.get("output_format"), body.get("model_id")))
        audio_b64 = base64.b64encode(b"fake-mp3-bytes").decode()
        return _Resp(200, {"audio_base64": audio_b64, "alignment": _FAKE_ALIGNMENT})

    return _post


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice-abc")


# --- the quota law -----------------------------------------------------------

def test_over_reserve_refuses_before_synthesis(tmp_path, monkeypatch):
    """{limit: 41502, count: 38000}: remaining=3502, text=1100 -> 1100+5000
    > 3502 -> refuse. The literal from the E-19 acceptance line."""
    get_fn, get_calls = _subscription(41_502, 38_000)
    monkeypatch.setattr(tts_eleven, "_http_get", get_fn)
    post_calls: list = []
    monkeypatch.setattr(tts_eleven, "_http_post", _tts_post(post_calls))

    text = "x" * 1_100
    with pytest.raises(RuntimeError) as e:
        tts_eleven.synthesize(text, "youtube", tmp_path / "voice.mp3")

    assert "eleven_quota" in str(e.value)
    assert get_calls, "guard must call the live subscription endpoint"
    assert post_calls == [], "must refuse before the TTS call, not after"


def test_quota_refusal_sends_telegram_alert(tmp_path, monkeypatch):
    """D7 (FABLE_DEFYCARD_PART2.md:321-322): eleven_quota must alert Telegram,
    not fail silently into stderr — the ruling-1 REJECT this fixes."""
    get_fn, _ = _subscription(41_502, 38_000)
    monkeypatch.setattr(tts_eleven, "_http_get", get_fn)
    monkeypatch.setattr(tts_eleven, "_http_post", _tts_post([]))
    alerts: list = []
    monkeypatch.setattr(tts_eleven.telegram_preview, "alert",
                        lambda text, **k: alerts.append(text))

    with pytest.raises(RuntimeError):
        tts_eleven.synthesize("x" * 1_100, "youtube", tmp_path / "voice.mp3")

    assert len(alerts) == 1
    assert "eleven_quota" in alerts[0]


def test_zero_used_synthesizes_with_word_timed_sidecar(tmp_path, monkeypatch):
    """{count: 0}: plenty of quota -> synth proceeds, sidecar has >=1 word
    carrying start/end times."""
    get_fn, get_calls = _subscription(41_502, 0)
    monkeypatch.setattr(tts_eleven, "_http_get", get_fn)
    post_calls: list = []
    monkeypatch.setattr(tts_eleven, "_http_post", _tts_post(post_calls))

    out_path = tmp_path / "voice.mp3"
    meta = tts_eleven.synthesize("hi there", "youtube", out_path)

    assert out_path.read_bytes() == b"fake-mp3-bytes"
    assert post_calls and post_calls[0][1] == "mp3_44100_128"
    words = json.loads(out_path.with_suffix(".timestamps.json").read_text())
    assert len(words) >= 1
    assert all("start" in w and "end" in w for w in words)
    assert words[0]["word"] == "hi"
    assert meta["chars_before"] == 0
    assert meta["chars_after"] == len("hi there")


def test_quota_guard_reads_the_live_api_not_a_local_counter(tmp_path, monkeypatch):
    """Mutation target named in the E-19 spec: a guard reading its own state
    instead of the API must fail this test."""
    calls = {"n": 0}

    def _get(url, headers):
        calls["n"] += 1
        return _Resp(200, {"character_limit": 41_502, "character_count": 0})

    monkeypatch.setattr(tts_eleven, "_http_get", _get)
    monkeypatch.setattr(tts_eleven, "_http_post", _tts_post([]))

    tts_eleven.synthesize("hello", "youtube", tmp_path / "a.mp3")
    tts_eleven.synthesize("hello", "youtube", tmp_path / "b.mp3")

    assert calls["n"] == 2, "every call must hit the live API, not a cached/local count"


# --- refuses before any network call ------------------------------------------

def test_empty_voice_id_refuses_before_network(tmp_path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    called = {"get": False, "post": False}
    monkeypatch.setattr(tts_eleven, "_http_get", lambda *a, **k: called.__setitem__("get", True))
    monkeypatch.setattr(tts_eleven, "_http_post", lambda *a, **k: called.__setitem__("post", True))

    with pytest.raises(RuntimeError) as e:
        tts_eleven.synthesize("hello", "youtube", tmp_path / "voice.mp3")

    assert "eleven_no_voice" in str(e.value)
    assert called == {"get": False, "post": False}


# --- dry-run keeps interface parity with tts_edge -----------------------------

def test_dry_run_writes_placeholder_and_empty_sidecar(tmp_path):
    out_path = tmp_path / "voice.mp3"
    meta = tts_eleven.synthesize("hello", "youtube", out_path, dry_run=True,
                                 dry_run_seconds=2.0)
    assert out_path.exists()
    assert meta["dry_run"] is True
    assert json.loads(out_path.with_suffix(".timestamps.json").read_text()) == []
