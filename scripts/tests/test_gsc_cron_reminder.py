"""T-0142-03 — bin/gsc-cron-reminder.sh generalized from one crontab line
(gsc-report.py) to all five R1 lines (postanovlenie T-0142.ruling-1, "Только
оператор" section). The law: check all five by script name/flag, one
Telegram message naming exactly the missing ones, self-remove from crontab
only once ALL five are present.

Runs the REAL script (Y53: the wiring, not a reimplementation) against a
throwaway crontab/curl/date, with only the hardcoded `cd
/home/cryptocardhub` swapped for a temp root so the test never touches this
repo's real crontab, logs, or Telegram.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
REAL_SCRIPT = ROOT / "bin" / "gsc-cron-reminder.sh"

FULL_CRON = "\n".join([
    "0 6 * * 2 cd /home/cryptocardhub && ... /usr/bin/python3 scripts/page-updates.py >> logs/pseo/page-updates.log 2>&1",
    "35 6 * * 1 cd /home/cryptocardhub && ... /usr/bin/python3 scripts/page-updates.py --verdict >> logs/pseo/page-updates-verdict.log 2>&1",
    "30 4 * * * /home/cryptocardhub/scripts/backup-daily.sh >> /home/cryptocardhub/logs/backup/cron.log 2>&1",
    "30 5 * * * cd /home/cryptocardhub && ... /usr/bin/python3 scripts/intel-ingest.py >> logs/intel/cron.log 2>&1",
    "30 6 * * 1 cd /home/cryptocardhub && ... /home/cryptocardhub/video-generator/venv/bin/python scripts/gsc-report.py >> logs/gsc/cron.log 2>&1",
    "0 6 * * * /home/cryptocardhub/bin/gsc-cron-reminder.sh >/dev/null 2>&1",
]) + "\n"


def _make_exec(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _setup(tmp_path: Path, cron_lines: str) -> dict:
    root = tmp_path / "root"
    (root / "logs").mkdir(parents=True)
    (root / "bin").mkdir(parents=True)
    (root / ".env").write_text("TG_BOT_TOKEN=fake-token\nTG_CHAT_ID=fake-chat\n", encoding="utf-8")

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    cron_file = tmp_path / "fake-crontab.txt"
    cron_file.write_text(cron_lines, encoding="utf-8")
    curl_calls = tmp_path / "curl-calls.txt"

    # Buffer stdin to a temp file and atomically `mv` it onto F, never
    # truncate F in place: `crontab -l | grep ... | crontab -` starts all
    # three pipeline stages concurrently, so an in-place `cat > "$F"` in this
    # stage could truncate F before the earlier `crontab -l` stage has
    # actually read it -- a race in this stub, not in the real crontab(1)
    # (which replaces the spool file atomically) and not in the script
    # under test.
    _make_exec(fake_bin / "crontab", f"""#!/usr/bin/env bash
F={cron_file}
if [ "$1" = "-l" ]; then
  [ -f "$F" ] && cat "$F"
  exit 0
elif [ "$1" = "-" ]; then
  TMP="$F.tmp.$$"
  cat > "$TMP"
  mv "$TMP" "$F"
  exit 0
fi
exit 1
""")
    _make_exec(fake_bin / "curl", f"""#!/usr/bin/env bash
{{ echo "---CALL---"; printf '%s\\n' "$@"; }} >> {curl_calls}
exit 0
""")
    _make_exec(fake_bin / "date", """#!/usr/bin/env bash
if [ "$1" = "-u" ] && [ "$2" = "+%u" ]; then
  echo 1   # force Monday so the noise-throttle never silently swallows the test
  exit 0
fi
exec /usr/bin/date "$@"
""")

    script = REAL_SCRIPT.read_text(encoding="utf-8").replace(
        "cd /home/cryptocardhub", f"cd {root}")
    script_path = tmp_path / "gsc-cron-reminder.sh"
    _make_exec(script_path, script)

    return {"root": root, "script": script_path, "cron_file": cron_file,
            "curl_calls": curl_calls, "fake_bin": fake_bin}


def _run(env_extra: dict, fixture: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = f"{fixture['fake_bin']}:{env['PATH']}"
    env.update(env_extra)
    return subprocess.run(["bash", str(fixture["script"])], env=env,
                          capture_output=True, text=True, timeout=30)


def test_self_removes_and_stays_silent_when_all_five_lines_present(tmp_path):
    fx = _setup(tmp_path, FULL_CRON)
    proc = _run({}, fx)
    assert proc.returncode == 0, proc.stderr

    remaining = fx["cron_file"].read_text(encoding="utf-8")
    assert "gsc-cron-reminder.sh" not in remaining, "must self-remove once complete"
    for must_stay in ("page-updates.py --verdict", "backup-daily.sh",
                       "intel-ingest.py", "gsc-report.py"):
        assert must_stay in remaining

    assert not fx["curl_calls"].exists(), "a complete set must never message Telegram"
    log = (fx["root"] / "logs" / "gsc-reminder.log").read_text(encoding="utf-8")
    assert "все 5 строк R1" in log


def test_lists_exactly_the_missing_lines_by_name(tmp_path):
    # Only the verdict half of page-updates.py and backup-daily.sh are present.
    partial = "\n".join([
        "35 6 * * 1 cd /home/cryptocardhub && ... scripts/page-updates.py --verdict >> x 2>&1",
        "30 4 * * * /home/cryptocardhub/scripts/backup-daily.sh >> x 2>&1",
        "0 6 * * * /home/cryptocardhub/bin/gsc-cron-reminder.sh >/dev/null 2>&1",
    ]) + "\n"
    fx = _setup(tmp_path, partial)
    proc = _run({}, fx)
    assert proc.returncode == 0, proc.stderr

    remaining = fx["cron_file"].read_text(encoding="utf-8")
    assert "gsc-cron-reminder.sh" in remaining, "must NOT self-remove while incomplete"

    calls = fx["curl_calls"].read_text(encoding="utf-8")
    assert calls, "missing lines must trigger a Telegram send"
    assert "page-updates.py (Tue 06:00" in calls
    assert "intel-ingest.py (05:30" in calls
    assert "gsc-report.py (Mon 06:30)" in calls
    # already present -- must NOT be listed as missing
    assert "page-updates.py --verdict (Mon 06:35" not in calls
    assert "backup-daily.sh (04:30" not in calls


def test_the_two_page_updates_lines_are_told_apart(tmp_path):
    """MUTATION this pins: collapsing both page-updates.py checks onto one
    pattern (e.g. bare `page-updates\\.py`) would let the --verdict line
    satisfy BOTH checks and hide that the plain Tuesday line is missing."""
    only_verdict = "\n".join([
        "35 6 * * 1 cd /home/cryptocardhub && ... scripts/page-updates.py --verdict >> x 2>&1",
        "30 4 * * * /home/cryptocardhub/scripts/backup-daily.sh >> x 2>&1",
        "30 5 * * * cd /home/cryptocardhub && ... scripts/intel-ingest.py >> x 2>&1",
        "30 6 * * 1 cd /home/cryptocardhub && ... scripts/gsc-report.py >> x 2>&1",
        "0 6 * * * /home/cryptocardhub/bin/gsc-cron-reminder.sh >/dev/null 2>&1",
    ]) + "\n"
    fx = _setup(tmp_path, only_verdict)
    proc = _run({}, fx)
    assert proc.returncode == 0, proc.stderr

    calls = fx["curl_calls"].read_text(encoding="utf-8")
    assert "page-updates.py (Tue 06:00" in calls, (
        "the bare page-updates.py line must still read as missing "
        "even though --verdict is present")
    assert "page-updates.py --verdict (Mon 06:35" not in calls
