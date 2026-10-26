"""CCH-HUM-06: the writer sees the voice law BEFORE its skill/contract text (Y63: a rule past
the byte window is a rule the writer never sees). Three producers, three asserts."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import humanize_gate as hg  # noqa: E402


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / fname)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


pg = _load("pg_voice", "pseo-generate.py")
ni = _load("ni_voice", "news-ingest.py")
pu = _load("pu_voice", "page-updates.py")

LEX = ["delve", "tapestry", "landscape", "seamless", "robust", "game-changer", "crucial"]


def _check(prompt, first_contract_marker):
    assert hg.VOICE_SPEC in prompt
    assert prompt.index(hg.VOICE_SPEC) < prompt.index(first_contract_marker)
    ban = next(l for l in prompt.splitlines() if l.startswith("Never use these words or phrases: "))
    for w in hg.LEXICON:
        assert w in ban
    for w in LEX:  # literals, not read from the module under test
        assert w in ban


def test_pseo_prompt():
    prompt = pg.build_cached_system_prompt()
    first_sentence = pg.SKILL_WRITE_ARTICLE.read_text(encoding="utf-8").strip().splitlines()[0]
    assert first_sentence in prompt or "## Article-writing contract" in prompt
    marker = "## Article-writing contract"
    assert prompt.index(hg.VOICE_SPEC) < prompt.index(marker)
    _check(prompt, marker)


def test_news_prompt():
    _check(ni.SYSTEM_PROMPT, "CONTRACT (P0")


def test_page_updates_prompt():
    system, _ = pu.build_prompt("q", "body", "facts")
    _check(system, "## Reference facts")
