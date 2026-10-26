"""E-02 — `PSEO_ENABLED` stop-cran.

One `.env` line must stop all pSEO printing/corpus edits before any
Haiku/network call, in every stream that prints or edits the corpus:
pseo-generate.py (bank + FAQ children), page-updates.py (strengthen),
news-ingest.py, market-digest.py.

Unset means ENABLED (opposite of the video pipeline's
VIDEO_GENERATOR_ENABLED) -- see scripts/flags.py's own docstring for why:
Fleet cannot write `.env` in this sandbox, so unset-means-disabled would
leave printing stopped forever.

The literal acceptance command from the plan is run verbatim as a subprocess
test below -- not paraphrased, not derived from the code under test (Y49).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from flags import pseo_enabled  # noqa: E402


# ---------------------------------------------------------------------------
# flags.py — the law, as literals
# ---------------------------------------------------------------------------

def test_unset_means_enabled(monkeypatch):
    monkeypatch.delenv("PSEO_ENABLED", raising=False)
    assert pseo_enabled() is True


def test_false_disables_case_insensitive(monkeypatch):
    for v in ("false", "False", "FALSE"):
        monkeypatch.setenv("PSEO_ENABLED", v)
        assert pseo_enabled() is False, v


def test_true_stays_enabled(monkeypatch):
    monkeypatch.setenv("PSEO_ENABLED", "true")
    assert pseo_enabled() is True


# ---------------------------------------------------------------------------
# pseo-generate.py — the exact acceptance command from the plan, verbatim
# ---------------------------------------------------------------------------

def _run(cmd: list[str], env_overrides: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(env_overrides)
    return subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)


def test_pseo_generate_disabled_skips_before_writer():
    proc = _run(
        [sys.executable, "scripts/pseo-generate.py", "--faq-children", "--dry-run"],
        {"PSEO_ENABLED": "false"},
    )
    assert proc.returncode == 0, proc.stderr
    assert '"reason": "pseo_disabled"' in proc.stdout
    assert '"event": "skip"' in proc.stdout
    # Proof it exited BEFORE the queue was ever built (would print this line
    # otherwise) -- the mutation "check moved after the writer call" would
    # leave this line present even with the flag off.
    assert "FAQ cohort queue" not in proc.stdout


def test_pseo_generate_unset_env_behaves_as_before():
    env_without_flag = dict(os.environ)
    env_without_flag.pop("PSEO_ENABLED", None)
    proc = subprocess.run(
        [sys.executable, "scripts/pseo-generate.py", "--dry-run", "--limit", "1"],
        cwd=ROOT, env=env_without_flag, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "pseo_disabled" not in proc.stdout
    assert "[pseo] rate=1" in proc.stdout


# ---------------------------------------------------------------------------
# In-process mutation gate: the writer is never called, counted by a stub --
# not inferred from stdout. This goes RED if the check is moved to after
# call_claude_cli() (the mutation the plan names).
# ---------------------------------------------------------------------------

_SPEC = importlib.util.spec_from_file_location("pseo_generate", ROOT / "scripts" / "pseo-generate.py")
pg = importlib.util.module_from_spec(_SPEC)
sys.modules["pseo_generate"] = pg
_SPEC.loader.exec_module(pg)  # type: ignore[union-attr]


def test_writer_stub_never_called_when_disabled(monkeypatch):
    calls = {"n": 0}

    def _stub(*a, **k):
        calls["n"] += 1
        raise AssertionError("call_claude_cli must not be reached when PSEO_ENABLED=false")

    monkeypatch.setattr(pg, "call_claude_cli", _stub)
    monkeypatch.setenv("PSEO_ENABLED", "false")
    rc = pg.main(["--faq-children"])  # live mode, not --dry-run
    assert rc == 0
    assert calls["n"] == 0


# ---------------------------------------------------------------------------
# The other three corpus-writing consumers named in the plan
# ---------------------------------------------------------------------------

VENV_PY = ROOT / "video-generator" / "venv" / "bin" / "python"


def test_page_updates_disabled_skips():
    proc = _run(
        [sys.executable, "scripts/page-updates.py", "--dry-run"],
        {"PSEO_ENABLED": "false"},
    )
    assert proc.returncode == 0, proc.stderr
    assert '"reason": "pseo_disabled"' in proc.stdout


def test_market_digest_disabled_skips():
    proc = _run(
        [sys.executable, "scripts/market-digest.py"],
        {"PSEO_ENABLED": "false"},
    )
    assert proc.returncode == 0, proc.stderr
    assert '"reason": "pseo_disabled"' in proc.stdout


@pytest.mark.skipif(not VENV_PY.exists(), reason="news-ingest.py's deps (feedparser/bs4) live in the pipeline venv")
def test_news_ingest_disabled_skips():
    proc = _run(
        [str(VENV_PY), "scripts/news-ingest.py", "--dry-run"],
        {"PSEO_ENABLED": "false"},
    )
    assert proc.returncode == 0, proc.stderr
    assert '"reason": "pseo_disabled"' in proc.stdout
