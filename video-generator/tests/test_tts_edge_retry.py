import asyncio
import sys
import types

import pytest

from pipeline import tts_edge


def _install(monkeypatch, plans):
    made = []

    class FakeComm:
        def __init__(self, **kw):
            self.plan = plans[len(made)]
            made.append(self)

        async def stream(self):
            if isinstance(self.plan, Exception):
                raise self.plan
            yield {"type": "audio", "data": b"abc"}
            yield {"type": "WordBoundary", "offset": 0, "duration": 10_000_000, "text": "hi"}

    monkeypatch.setitem(sys.modules, "edge_tts", types.SimpleNamespace(Communicate=FakeComm))
    return made


def _run(tmp_path):
    return asyncio.run(tts_edge._synthesize_async(
        "hi", "v", "-5%", tmp_path / "a.mp3", tmp_path / "t.json"))


def test_retry_uses_fresh_communicate(monkeypatch, tmp_path):
    made = _install(monkeypatch, [ValueError("boom"), None])
    assert _run(tmp_path)["word_count"] == 1
    assert len(made) == 2 and made[0] is not made[1]


def test_both_errors_reported(monkeypatch, tmp_path):
    _install(monkeypatch, [ValueError("first-err"), KeyError("second-err")])
    with pytest.raises(RuntimeError) as ei:
        _run(tmp_path)
    m = str(ei.value)
    assert "first=ValueError: first-err" in m and "second=KeyError" in m
