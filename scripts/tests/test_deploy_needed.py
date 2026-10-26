"""G42 — deploy-needed.sh reads site INPUTS, not dist/.

T-0143: the old gate compared two of a dozen inputs against
`seo-site/dist/index.html`. Any `pnpm build` run on the side made dist newer
than every input and muted the next tick; a red deploy gate rebuilds dist too
and muted itself. This gate instead diffs a literal input list against a
success stamp that only moves when scripts/deploy.sh actually finishes.

THE THIRD ASSERT IS THE MUTATION LAW (Y49): a file that exists only under
`seo-site/dist/` must NOT trigger a deploy. If someone puts `dist` back into
the find list, this test goes red — it does not read the list out of the
script under test, it drives the real script against a real tmp tree.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "deploy-needed.sh"


def _run(root: Path, stamp: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "ROOT": str(root), "STAMP": str(stamp)}
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)


def test_no_stamp_means_deploy(tmp_path):
    """Never deployed before -> rc 0 (deploy)."""
    stamp = tmp_path / "logs" / "deploy" / "last-success.stamp"
    r = _run(tmp_path, stamp)
    assert r.returncode == 0


def test_input_newer_than_stamp_triggers_deploy(tmp_path):
    stamp = tmp_path / "logs" / "deploy" / "last-success.stamp"
    stamp.parent.mkdir(parents=True)
    stamp.write_text("")

    public = tmp_path / "seo-site" / "public"
    public.mkdir(parents=True)
    changed = public / "x.json"
    changed.write_text("{}")
    _age(changed, seconds_after=stamp)

    r = _run(tmp_path, stamp)
    assert r.returncode == 0


def test_stamp_newer_than_every_input_means_nothing_to_deploy(tmp_path):
    public = tmp_path / "seo-site" / "public"
    public.mkdir(parents=True)
    old = public / "x.json"
    old.write_text("{}")

    stamp = tmp_path / "logs" / "deploy" / "last-success.stamp"
    stamp.parent.mkdir(parents=True)
    stamp.write_text("")
    _age(stamp, seconds_after=old)

    r = _run(tmp_path, stamp)
    assert r.returncode == 1


def test_dist_only_change_does_not_trigger_deploy(tmp_path):
    """Mutation law: dist/ is a build ARTIFACT, not an input. A build run on
    the side (or a red gate rebuilding dist) must never look like new content."""
    stamp = tmp_path / "logs" / "deploy" / "last-success.stamp"
    stamp.parent.mkdir(parents=True)
    stamp.write_text("")

    dist = tmp_path / "seo-site" / "dist"
    dist.mkdir(parents=True)
    built = dist / "index.html"
    built.write_text("<html></html>")
    _age(built, seconds_after=stamp)

    r = _run(tmp_path, stamp)
    assert r.returncode == 1, "dist/ must never be read as a site input"


def _age(path: Path, *, seconds_after: Path) -> None:
    """Force `path`'s mtime to be strictly newer than `seconds_after`'s."""
    base = seconds_after.stat().st_mtime
    newer = base + 5
    os.utime(path, (newer, newer))
