"""Config loader — single source of truth for runtime values.

Reads ../config.yaml and exposes typed accessors. Also handles ${VAR} env
substitution so dlvr.it routes / API keys are never hardcoded.
"""
from __future__ import annotations
import os
import re
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_CFG_PATH = _ROOT / "config.yaml"

_ENV_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _expand_env(obj: Any) -> Any:
    if isinstance(obj, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), obj)
    if isinstance(obj, dict):
        return {k: _expand_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env(v) for v in obj]
    return obj


_cache: dict[str, Any] | None = None


def cfg() -> dict[str, Any]:
    """Load + cache config.yaml with ${VAR} env expansion."""
    global _cache
    if _cache is None:
        with _CFG_PATH.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        _cache = _expand_env(raw)
    return _cache


def root() -> Path:
    """Project root (the video-generator/ dir)."""
    return _ROOT
