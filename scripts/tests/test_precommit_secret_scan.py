"""T-0142-07 (ruling-5): the real pre-commit hook, run through a real `git commit`
in a temp repo — wiring, not a re-implementation of the hook's regex (Y53 rule)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK_PATH = Path(os.environ.get("PRECOMMIT_HOOK_PATH", str(REPO / ".husky" / "pre-commit")))

BLOCKED = [
    ".env",
    ".env.local",
    ".env.enc",
    ".env.bak-pre-videostop-20260901",
    "seo-site/.env",
    ".env.example.bak",
]
PASSES = [".env.example", "seo-site/.env.example", "README.md"]


def _try_commit(tmp_path, relpath):
    if not HOOK_PATH.is_file():
        pytest.fail(f"pre-commit hook not found: {HOOK_PATH}")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    husky_dir = repo / ".husky"
    husky_dir.mkdir()
    dest_hook = husky_dir / "pre-commit"
    shutil.copy(HOOK_PATH, dest_hook)
    dest_hook.chmod(0o755)
    subprocess.run(["git", "config", "core.hooksPath", ".husky"], cwd=repo, check=True)
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("dummy=1\n")
    subprocess.run(["git", "add", "-f", relpath], cwd=repo, check=True)
    return subprocess.run(
        ["git", "commit", "-m", "test commit"],
        cwd=repo,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("relpath", BLOCKED)
def test_env_files_blocked(tmp_path, relpath):
    result = _try_commit(tmp_path, relpath)
    assert result.returncode != 0
    assert "BLOCKED: .env" in result.stderr


@pytest.mark.parametrize("relpath", PASSES)
def test_templates_pass(tmp_path, relpath):
    result = _try_commit(tmp_path, relpath)
    assert result.returncode == 0, result.stderr
