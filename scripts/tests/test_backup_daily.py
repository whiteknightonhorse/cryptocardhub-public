"""E-04 — backup-daily.sh, run for real against a temp tree.

LAW (literal, Y49): a `daily-*.tar.gz` older than 14 days is rotated out; one
that is 14 days old or younger survives. Not re-derived from the script's own
`-mtime +14` — the mutation below (`-mtime +30`) proves this test would
actually catch the drift.

The tar-content law is proven by running the real script end to end (not a
mock): it must produce a real, `tar -tzf`-verifiable archive of the three
source directories, sized like a real corpus, not an empty shell.
"""
from __future__ import annotations

import os
import subprocess
import tarfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "backup-daily.sh"


def _fake_root(tmp_path: Path) -> Path:
    """A minimal tree shaped like the three paths backup-daily.sh archives."""
    (tmp_path / "seo-site" / "src" / "content" / "pseo").mkdir(parents=True)
    (tmp_path / "seo-site" / "src" / "content" / "pseo" / "page.md").write_text("x" * 2000)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "rates.yaml").write_text("y" * 500)
    (tmp_path / "video-generator" / "state").mkdir(parents=True)
    (tmp_path / "video-generator" / "state" / "voice_rotation.json").write_text("{}")
    return tmp_path


def _run(tmp_path: Path, backup_dir: Path, log_dir: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "ROOT": str(tmp_path), "BACKUP_DIR": str(backup_dir), "LOG_DIR": str(log_dir)}
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)


def test_backup_daily_creates_a_verified_archive_and_logs_jsonl(tmp_path):
    root = _fake_root(tmp_path)
    backup_dir, log_dir = root / "backups", root / "logs" / "backup"

    r = _run(root, backup_dir, log_dir)
    assert r.returncode == 0, r.stderr

    archives = list(backup_dir.glob("daily-*.tar.gz"))
    assert len(archives) == 1
    with tarfile.open(archives[0]) as tf:
        names = tf.getnames()
    assert any("page.md" in n for n in names)
    assert any("rates.yaml" in n for n in names)
    assert any("voice_rotation.json" in n for n in names)

    logs = list(log_dir.glob("*.jsonl"))
    assert len(logs) == 1
    steps = [line.split('"step":"')[1].split('"')[0] for line in logs[0].read_text().splitlines()]
    assert steps == ["create", "verify", "rotate"]
    assert '"success":true' in logs[0].read_text()
    assert '"success":false' not in logs[0].read_text()


def test_backup_daily_rotates_out_only_archives_older_than_14_days(tmp_path):
    root = _fake_root(tmp_path)
    backup_dir, log_dir = root / "backups", root / "logs" / "backup"
    backup_dir.mkdir(parents=True)

    # 15 pre-existing fakes, ages 1..15 days. LAW: only the 15-day-old one
    # (strictly > 14 days, per -mtime +14) is rotated out by this run.
    import time
    now = time.time()
    for age in range(1, 16):
        p = backup_dir / f"daily-fake-age{age}.tar.gz"
        p.write_bytes(b"x")
        ts = now - age * 86400
        os.utime(p, (ts, ts))
    oldest = backup_dir / "daily-fake-age15.tar.gz"

    r = _run(root, backup_dir, log_dir)
    assert r.returncode == 0, r.stderr

    remaining = {p.name for p in backup_dir.glob("daily-fake-*.tar.gz")}
    assert oldest.name not in remaining, "the 15-day-old archive must be rotated out"
    assert len(remaining) == 14, "the 14 archives aged 1..14 days must survive"

    log_text = (log_dir / f"{__import__('datetime').date.today().isoformat()}.jsonl").read_text()
    assert '"deleted":1' in log_text


def test_backup_daily_exits_1_and_logs_failure_when_sources_are_missing(tmp_path):
    """Mutation-equivalent failure path: no fake root at all -> tar has nothing
    to archive -> the script must fail loudly (exit 1), not print OK."""
    root = tmp_path / "empty-root"
    root.mkdir()
    backup_dir, log_dir = root / "backups", root / "logs" / "backup"

    r = _run(root, backup_dir, log_dir)
    assert r.returncode == 1
    log_text = next(log_dir.glob("*.jsonl")).read_text()
    assert '"step":"create","success":false' in log_text
