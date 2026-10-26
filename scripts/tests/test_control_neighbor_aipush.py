"""The neighbor-site control's path allowlist must admit aipush.app's real
pages and reject the scanner-probe paths actually seen on the SHARED log
this control reads (measured 2026-09-01: /app, /backend, /root, /home are
real hits on this box, not aipush content) — a generic single-segment-slug
regex would have admitted every one of them.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ctrl = _load("control_neighbor_aipush", "scripts/control-neighbor-aipush.py")


def test_real_aipush_pages_are_admitted():
    for path in ("/", "/analyzer", "/analysis", "/how-to-rank-in-chatgpt",
                 "/ai-seo-agency", "/es/analysis", "/zh/llm-seo"):
        assert ctrl.is_aipush_content_path(path), path


def test_scanner_probe_paths_seen_on_the_shared_log_are_rejected():
    """These are real single-segment paths measured on the same shared log
    the same day — a bare `[a-z0-9-]+` slug regex would have admitted them."""
    for path in ("/app", "/backend", "/root", "/home", "/config", "/aws",
                 "/.aws", "/.github", "/api"):
        assert not ctrl.is_aipush_content_path(path), path


def test_unknown_slug_is_rejected_even_if_it_looks_like_a_real_page():
    """The allowlist is a literal set of MEASURED real pages, not a shape
    heuristic — a plausible-looking but unlisted slug does not get in."""
    assert not ctrl.is_aipush_content_path("/some-new-blog-post-nobody-listed")


def test_i18n_prefix_alone_is_not_enough():
    assert not ctrl.is_aipush_content_path("/es/not-a-real-page")
    assert not ctrl.is_aipush_content_path("/es")


def test_weeks_are_exactly_the_two_recoverable_ones():
    """Widening this silently would compare windows defycard's own
    re-verification could not recover, breaking the like-for-like promise."""
    assert ctrl.WEEKS == ("2026-W34", "2026-W35")
