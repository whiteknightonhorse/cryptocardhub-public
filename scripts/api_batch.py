"""API-CREDITS-1108/3b: the batch lane, shared by page-updates, intel-ingest and the G37 judge.

One request per batch; `submit()` returns False on ANY refusal and the caller then runs its
subscription path unchanged. After one refusal this process never tries again (no second
attempt in the same run). Opt-in per job: a job name on its own line in ~/api-batch/jobs
(delete the file = kill-switch). The wrapper (~/bin/api-credits/api-batch) owns the key; this
module never reads it. State holds batch ids, custom_ids and small refs, never prompt text.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BIN = os.environ.get("API_BATCH_BIN") or str(Path.home() / "bin" / "api-credits" / "api-batch")
LANE = "batch"
_refused = False


class Deferred(Exception):
    """The item is on the batch lane; skip it this run, the collector finishes it."""


def _dir() -> Path:
    return Path(os.environ.get("API_BATCH_DIR") or Path.home() / "api-batch")


def disable() -> None:
    """--dry-run: nothing may be submitted."""
    global _refused
    _refused = True


def enabled(job: str) -> bool:
    if _refused:
        return False
    try:
        return job in (_dir() / "jobs").read_text().split()
    except OSError:
        return False


def cid(prefix: str, *parts: str) -> str:
    """custom_id from the item's own identity; [A-Za-z0-9_-]{1,64} is the wrapper's rule."""
    return prefix + "-" + hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _run(args: list[str]) -> tuple[int, str, str]:
    p = subprocess.run([BIN, *args], capture_output=True, text=True, timeout=300)
    return p.returncode, p.stdout, p.stderr


def _state(job: str) -> dict:
    try:
        return json.loads((_dir() / job / "state.json").read_text())
    except (OSError, ValueError):
        return {"batches": {}}


def _save(job: str, st: dict) -> None:
    d = _dir() / job
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"state.json.tmp{os.getpid()}"
    tmp.write_text(json.dumps(st, sort_keys=True))
    os.replace(tmp, d / "state.json")


def submit(job: str, item_id: str, ref: dict, system: str, user: str, max_tokens: int, model: str) -> bool:
    """True = the item is on the batch lane (submitted now, or already pending).
    False = refused: the caller runs its subscription path."""
    global _refused
    st = _state(job)
    if any(item_id in b["items"] for b in st["batches"].values()):
        return True
    d = _dir() / job
    d.mkdir(parents=True, exist_ok=True)
    path = d / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{item_id}.jsonl")
    req = {"custom_id": item_id, "params": {"model": model, "max_tokens": max_tokens, "system": system,
                                            "messages": [{"role": "user", "content": user}]}}
    path.write_text(json.dumps(req, ensure_ascii=False) + "\n")
    try:
        for sub in ("estimate", "submit"):   # never submit without the estimate step
            rc, out, err = _run([sub, "--lane", LANE, "--model", model, "--jobs", str(path)])
            if rc:
                _refused = True
                lines = err.strip().splitlines()
                print("batch refused: " + (lines[-1][:200] if lines else f"{sub} exit {rc}"), file=sys.stderr)
                return False
    finally:
        path.unlink(missing_ok=True)
    st = _state(job)
    st["batches"][out.strip()] = {"model": model, "items": {item_id: ref}}
    _save(job, st)
    return True


def collect(job: str, handle) -> int:
    """For every ended batch: handle({custom_id: (ref, text-or-None)}), then clear its state entry.
    A handler that raises leaves the entry for the next tick. Returns the number of batches handled."""
    n = 0
    for bid, b in list(_state(job)["batches"].items()):
        common = ["--lane", LANE, "--model", b["model"], "--batch-id", bid]
        rc, out, _ = _run(["poll", *common])
        if rc or out.strip() != "ended":
            continue
        res = _dir() / job / f"{bid}.results.jsonl"
        if _run(["collect", *common, "--out", str(res)])[0]:
            continue
        got: dict[str, str] = {}
        for line in res.read_text().splitlines():
            try:
                o = json.loads(line)
                r = o["result"]
                if r["type"] == "succeeded":
                    got[o["custom_id"]] = "".join(c.get("text", "") for c in r["message"]["content"]
                                                  if c.get("type") == "text")
            except (ValueError, KeyError, TypeError):
                continue
        handle({i: (ref, got.get(i)) for i, ref in b["items"].items()})
        st = _state(job)   # reload: the handler may have submitted new items
        st["batches"].pop(bid, None)
        _save(job, st)
        res.unlink(missing_ok=True)
        n += 1
    return n
