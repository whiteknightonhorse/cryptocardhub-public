"""G35 (E-20, VG_MODE=h1) — a script may not state a disputed or expired
ether.fi fact as settled.

Same law as longform.py's `refuse_expired_claims` (a voiceover cannot be
corrected after upload, so the refusal must land before TTS, not after
render), applied here against facts_lib's structured model instead of a
SKILL.md text pattern — because H1 scripts are written FROM
`config/facts/etherfi.yaml`'s rendered `current` facts, not from SKILL.md
prose (see `write_script._build_user_prompt`).
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

VG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VG.parent / "scripts"))
import facts_lib  # noqa: E402

from pipeline import write_script as ws  # noqa: E402


def _facts(**overrides):
    fact = {
        "id": "etherfi.test.x", "value": 30000, "unit": "USD/day", "applies_to": {},
        "source_url": "https://example.com", "source_quote": "q",
        "verified_at": "2026-09-14", "expires": None, "status": "disputed",
        "class": "limit", "confidence": "issuer_help",
    }
    fact.update(overrides)
    return [fact]


def test_a_script_stating_a_disputed_fact_is_refused(monkeypatch):
    monkeypatch.setattr(facts_lib, "load", lambda *a, **k: _facts(status="disputed", value=30000))
    script = {"voiceover": "Your daily limit is $30,000/day on the core tier."}
    with pytest.raises(RuntimeError, match="G35"):
        ws.check_facts_gate(script)


def test_a_script_stating_an_expired_fact_is_refused(monkeypatch):
    monkeypatch.setattr(facts_lib, "load", lambda *a, **k: _facts(
        status="expired", value=15, unit="%", expires="2026-02-01"))
    script = {"hook": "", "body": "Earn up to 15% cashback on every purchase.",
             "cta": "", "voiceover": "Earn up to 15% cashback on every purchase."}
    with pytest.raises(RuntimeError, match="G35"):
        ws.check_facts_gate(script)


def test_a_clean_script_passes(monkeypatch):
    monkeypatch.setattr(facts_lib, "load", lambda *a, **k: _facts(
        status="disputed", value=30000, unit="USD/day"))
    script = {"voiceover": "ether.fi Cash gives up to 3% cashback on spend."}
    ws.check_facts_gate(script)  # must not raise


def test_no_stale_facts_at_all_short_circuits(monkeypatch):
    monkeypatch.setattr(facts_lib, "load", lambda *a, **k: _facts(status="current"))
    script = {"voiceover": "anything at all, 30000 included"}
    ws.check_facts_gate(script)  # current facts never gate anything


def test_the_gate_is_wired_into_run_pipeline_before_tts():
    """Y53: a correct helper nobody calls is still a broken pipeline. The
    mutation this catches is 'gate written, never invoked', or invoked after
    TTS instead of before it (D7's step order)."""
    src = (VG / "pipeline" / "main.py").read_text(encoding="utf-8")
    body = src[src.index("def _run_after_topic("):]
    assert "check_facts_gate(" in body, "_run_after_topic does not call the gate"
    assert body.index("write_script.write(") < body.index("check_facts_gate(") \
        < body.index("_tts_mod.synthesize("), \
        "the gate must run after script generation and before TTS"


def test_the_gate_only_fires_for_h1_pool_topics():
    """A non-h1 script (finance/viral/pseo pool) is never written from
    facts.yaml, so the gate must not run for it — gating it unconditionally
    would refuse legitimate copy that mentions an unrelated coincidental
    number."""
    src = (VG / "pipeline" / "main.py").read_text(encoding="utf-8")
    body = src[src.index("def _run_after_topic("):]
    gate_call = body.index("check_facts_gate(")
    guard = body[:gate_call].rsplit("\n\n", 1)[-1]
    assert 'topic.get("pool") == "h1"' in guard
