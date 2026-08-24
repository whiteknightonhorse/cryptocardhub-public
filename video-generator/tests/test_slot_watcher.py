"""G4-integration (P0-4) — the gate must actually be wired into the watcher,
and the master kill-switch must outrank it in both directions.

The real python binary is replaced by a stub, so no test here can render or
publish anything: the stub answers --cadence-gate and records any attempt to
run pipeline.main instead of running it.

Mutation to prove this test bites: delete the `--cadence-gate` block from
cron/10-slot-watcher.sh → test_suppressed_channel_blocks_the_slot goes red
(a publish attempt is recorded while the gate says no).
"""
from __future__ import annotations
import os
import subprocess
from pathlib import Path

import pytest

WATCHER = Path(__file__).resolve().parent.parent / "cron" / "10-slot-watcher.sh"

STUB = """#!/usr/bin/env bash
for a in "$@"; do
  if [ "$a" = "--cadence-gate" ]; then exit "${STUB_GATE_EXIT:-0}"; fi
done
echo "$@" >> "$STUB_CALLS"
exit 0
"""


def _run(tmp_path, *, master="true", gate_exit="0"):
    stub = tmp_path / "stub-python"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o755)
    env_file = tmp_path / "env"
    env_file.write_text(
        f"VIDEO_GENERATOR_ENABLED={master}\nVG_YT_ENABLED=true\n"
        "VG_IG_ENABLED=false\nVG_TT_ENABLED=false\n", encoding="utf-8")
    calls = tmp_path / "calls.txt"

    env = {**os.environ,
           "VG_ENV_FILE": str(env_file),
           "VG_PYBIN": str(stub),
           "VG_LOG_DIR": str(tmp_path / "log"),
           "STUB_CALLS": str(calls),
           "STUB_GATE_EXIT": gate_exit}
    proc = subprocess.run(["bash", str(WATCHER)], capture_output=True,
                          text=True, env=env, timeout=60)
    attempts = calls.read_text(encoding="utf-8") if calls.exists() else ""
    return proc, attempts


def test_healthy_channel_fires_the_slot(tmp_path):
    proc, attempts = _run(tmp_path, gate_exit="0")
    assert proc.returncode == 0
    assert "--check-slot" in attempts


def test_suppressed_channel_blocks_the_slot(tmp_path):
    proc, attempts = _run(tmp_path, gate_exit="1")
    assert proc.returncode == 0
    assert "--check-slot" not in attempts, "gate said no, the watcher published anyway"
    assert "suppressed by cadence gate" in proc.stdout, "silent skip — no reason reported"


def test_master_switch_off_wins_over_an_open_gate(tmp_path):
    proc, attempts = _run(tmp_path, master="false", gate_exit="0")
    assert proc.returncode == 0
    assert attempts == "", "publication ran with VIDEO_GENERATOR_ENABLED=false"
    assert "VIDEO_GENERATOR_ENABLED!=true — skip" in proc.stdout


def test_the_watcher_obeys_whatever_the_live_switch_says():
    """The law is OBEDIENCE to the switch, not one particular position of it.

    This used to assert «the operator's real .env, untouched: publication stays
    stopped». The operator armed publication on 2026-08-12, so the test went
    red for reporting reality correctly — a test that pins production state
    rather than behaviour goes stale the moment the operator exercises his own
    switch, and a permanently red test is one nobody reads.

    What must never change is the coupling: enabled=false ⇒ the watcher skips
    and nothing is published; enabled=true ⇒ it does not claim to be paused.
    Both directions are asserted here against the REAL .env, so this stays
    honest whichever way the switch is thrown.
    """
    env_file = Path.home() / ".env"
    live_on = any(
        line.strip() == "VIDEO_GENERATOR_ENABLED=true"
        for line in env_file.read_text(encoding="utf-8").splitlines()
    ) if env_file.exists() else False

    proc = subprocess.run(["bash", str(WATCHER)], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[:400]

    skipped = "VIDEO_GENERATOR_ENABLED!=true — skip" in proc.stdout
    if live_on:
        assert not skipped, (
            "the switch is ON in the live .env, yet the watcher reports itself "
            "paused — the kill-switch and the watcher disagree"
        )
    else:
        assert skipped, (
            "the switch is OFF in the live .env, yet the watcher did not skip — "
            "publication could run against a closed gate"
        )
