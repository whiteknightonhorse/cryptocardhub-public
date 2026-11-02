"""CCH-DEPLOY-01 — every `pnpm build` in seo-site/ runs under the one build lock.

The lock block is extracted from the real script text and executed, so the test
cannot pass on a copy. The deploy script itself is never run here (it deploys).
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
DEPLOY = SCRIPTS / "deploy.sh"
MONTHLY = SCRIPTS / "monthly-rebuild.sh"


def _lock_block(path: Path) -> str:
    s = path.read_text(encoding="utf-8")
    start = s.index("SEO_BUILD_LOCK=")
    end = s.index("\n\n", start)
    return s[start:end]


def _run(block: str, lock: Path, wait: str):
    script = f'{block}\necho GOT_LOCK\n'
    return subprocess.run(
        ["bash", "-c", script],
        env={"PATH": "/usr/bin:/bin", "HOME": str(lock.parent),
             "SEO_BUILD_LOCK": str(lock), "SEO_BUILD_LOCK_WAIT": wait},
        capture_output=True, text=True, timeout=30,
    )


def test_second_builder_refuses_while_lock_held(tmp_path):
    lock = tmp_path / "seo-build.lock"
    holder = subprocess.Popen(["flock", str(lock), "sleep", "20"])
    try:
        time.sleep(0.5)
        for path in (DEPLOY, MONTHLY):
            r = _run(_lock_block(path), lock, "1")
            assert r.returncode == 9, (path.name, r.stdout, r.stderr)
            assert "GOT_LOCK" not in r.stdout
            assert "refusing to build" in r.stderr
    finally:
        holder.kill()
        holder.wait()


def test_builder_proceeds_when_lock_free(tmp_path):
    lock = tmp_path / "sub" / "seo-build.lock"
    for path in (DEPLOY, MONTHLY):
        r = _run(_lock_block(path), lock, "1")
        assert r.returncode == 0 and "GOT_LOCK" in r.stdout, (path.name, r.stderr)


def test_lock_precedes_every_build():
    for path in (DEPLOY, MONTHLY):
        s = path.read_text(encoding="utf-8")
        assert s.count("\npnpm build 2>&1") == 1, path.name
        assert s.index("flock -w") < s.index("\npnpm build 2>&1"), path.name
