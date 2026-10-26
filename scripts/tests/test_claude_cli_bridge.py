"""CCH-WRITER-01: ONE claude CLI bridge, tools off, failure carries its cause."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE = ROOT / "taskloop/logs/CCH-WRITER-01/exit1-gemini.stdout.json"


def _pg():
    spec = importlib.util.spec_from_file_location("pseo_generate", ROOT / "scripts/pseo-generate.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pseo_generate"] = mod
    spec.loader.exec_module(mod)
    return mod


def _patch(monkeypatch, rc, stdout, argv_sink=None):
    def fake(cmd, **kw):
        if argv_sink is not None:
            argv_sink.extend(cmd)
        return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr="")
    monkeypatch.setattr(subprocess, "run", fake)


def test_a_argv_tools_off(monkeypatch):
    argv = []
    _patch(monkeypatch, 0, '{"result":"ok","usage":{},"total_cost_usd":0}', argv)
    _pg().call_claude_cli("sys", "user")
    assert argv[0] == "/usr/bin/claude"
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--max-turns") + 1] == "1"
    assert "--no-session-persistence" in argv


def test_b_failure_carries_subtype(monkeypatch):
    _patch(monkeypatch, 1, FIXTURE.read_text())
    with pytest.raises(RuntimeError) as e:
        _pg().call_claude_cli("sys", "user")
    assert "error_max_turns" in str(e.value)
    assert str(e.value) != "claude CLI exit 1: stderr=''"


def test_c_not_logged_in(monkeypatch):
    _patch(monkeypatch, 1, "Not logged in\n")
    with pytest.raises(RuntimeError, match="Not logged in"):
        _pg().call_claude_cli("sys", "user")


def test_d_single_argv_builder():
    lit = '"/usr/bin/claude"'
    assert (ROOT / "scripts/pseo-generate.py").read_text().count(lit) == 1
    for f in ("intel-ingest.py", "news-ingest.py", "councilreview-runner.py"):
        assert (ROOT / "scripts" / f).read_text().count(lit) == 0, f


def test_e_write_script_tools_off():
    assert '"--tools", ""' in (ROOT / "video-generator/pipeline/write_script.py").read_text()
