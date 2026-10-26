"""Creative-pattern selector for shorts-analysis pipeline.

Reads the catalog at .claude/skills/shorts-analysis/creative_patterns.json and
samples ONE pattern per creative axis using Beta(alpha, beta) priors. Apply
banned_with mutual exclusions + platform filter. Compose creative tuple.
Pass tuple to write_script.py as system-prompt context.

Phase 0 (first 30 videos/channel): forced uniform sampling — every pattern
gets a turn before Bayesian active.
Phase 1+ (30+ videos/channel): Thompson sampling on research-prior + actual data.

State: video-generator/state/creative_patterns_state.json (gitignored, runtime).
Updated nightly by pipeline/analytics.py from view + signup data.

Per shorts-analysis/SKILL.md §11 + feedback_loop_spec.md.
"""
from __future__ import annotations
import json
import os
import random
from pathlib import Path
from typing import Any

from . import _config


_CATALOG_PATH = (
    _config.root().parent / ".claude" / "skills" / "shorts-analysis" / "creative_patterns.json"
)
_STATE_PATH = _config.root() / "state" / "creative_patterns_state.json"

# Phase 0 = forced exploration for first 30 videos per channel.
PHASE_0_VIDEO_COUNT = 30


def _catalog() -> dict[str, Any]:
    if not _CATALOG_PATH.exists():
        raise FileNotFoundError(
            f"creative_patterns.json missing at {_CATALOG_PATH} — "
            f"shorts-analysis skill not installed"
        )
    return json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))


def _state() -> dict[str, Any]:
    if not _STATE_PATH.exists():
        return {}
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _publish_count(platform: str) -> int:
    """Approximate count of videos already published on this platform.
    Used to gate Phase 0 → Phase 1 transition."""
    state = _state()
    return int(state.get("publish_counts", {}).get(platform, 0))


def _alpha_beta(pattern: dict[str, Any], state: dict[str, Any]) -> tuple[float, float]:
    """Effective (alpha, beta) for a pattern: research prior + actual successes/failures."""
    pid = pattern["id"]
    runtime = state.get("patterns", {}).get(pid, {})
    base_a = float(pattern.get("base_alpha", 5))
    base_b = float(pattern.get("base_beta", 3))
    success = float(runtime.get("success_count", 0))
    failure = float(runtime.get("failure_count", 0))
    return base_a + success, base_b + failure


def _sample_beta(rng: random.Random, alpha: float, beta: float) -> float:
    """Beta sample via Gamma transform (rng.gammavariate)."""
    x = rng.gammavariate(alpha, 1.0)
    y = rng.gammavariate(beta, 1.0)
    return x / (x + y)


def _platform_eligible(pattern: dict[str, Any], platform: str) -> bool:
    """Pattern is eligible if its platform tier is not 'low'."""
    tier = pattern.get("platforms", {}).get(platform, "medium")
    return tier != "low"


def _duration_compatible(pattern: dict[str, Any], band: list[float] | None) -> bool:
    """True if `pattern`'s target_seconds range overlaps the topic's
    duration_band. T-0142-02: the topic's band (e.g. H1 youtube [30, 45]) is
    the ONE length law — a duration_band-axis pattern outside it (e.g.
    duration_long_60_90) must never be selectable, or the writer composes for
    a length the render (Y49: duration = min(max_seconds, tts+0.5)) will
    silently cut down to something else. Patterns on other axes carry no
    `target_seconds` and are always compatible."""
    if band is None:
        return True
    target = pattern.get("target_seconds")
    if not target:
        return True
    t_lo, t_hi = float(target[0]), float(target[1])
    b_lo, b_hi = float(band[0]), float(band[-1])
    return t_lo <= b_hi and b_lo <= t_hi


def _check_mutual_exclusions(
    selected: dict[str, str], pattern_id: str, banned_with: list[str]
) -> bool:
    """Return True if pattern_id is compatible with already-selected patterns."""
    return not any(other in banned_with for other in selected.values())


def select(platform: str, topic_class: str | None = None,
           seed: int | None = None,
           duration_band: list[float] | None = None) -> dict[str, Any]:
    """Pick one pattern per creative axis. Returns a dict mapping axis -> pattern_id
    plus a summary of why each was picked.

    Args:
        platform: "youtube" | "instagram" | "tiktok"
        topic_class: optional topic class hint ("TOFU" | "MOFU" | "BOFU" | "VIRAL")
        seed: optional rng seed for reproducible selection
        duration_band: topic's [lo, hi] seconds (fetch_topic's duration_band).
                        Drops duration_band-axis patterns outside this range
                        (T-0142-02) — the writer must never be handed a length
                        the topic didn't ask for.

    Returns:
        {
          "tuple": {"hook_type": "hook_pov_first_person", ...},
          "phase": "0_forced_exploration" | "1_bayesian",
          "details": {axis: {"pattern": <pattern_dict>, "why": "..."}, ...}
        }
    """
    rng = random.Random(seed)
    cat = _catalog()
    st = _state()
    publishes = _publish_count(platform)
    phase = "0_forced_exploration" if publishes < PHASE_0_VIDEO_COUNT else "1_bayesian"

    selected: dict[str, str] = {}
    details: dict[str, Any] = {}

    for axis in cat["axes"]:
        patterns = cat["categories"].get(axis, [])
        # Filter by platform eligibility + duration compatibility + mutual
        # exclusion + not disabled
        eligible = [
            p for p in patterns
            if _platform_eligible(p, platform)
            and _duration_compatible(p, duration_band)
            and not p.get("disabled", False)
            and _check_mutual_exclusions(selected, p["id"], p.get("banned_with", []))
        ]
        if not eligible:
            # Defensive: ALL patterns banned — relax mutual exclusion this round
            eligible = [p for p in patterns if _platform_eligible(p, platform)
                        and _duration_compatible(p, duration_band)
                        and not p.get("disabled", False)]
        if not eligible:
            # Worst case: take any pattern
            eligible = patterns

        if phase == "0_forced_exploration":
            # Uniform random — ensures every pattern gets tried at least once
            pick = rng.choice(eligible)
            why = f"phase=0 uniform random (publish #{publishes + 1})"
        else:
            # Thompson sampling: sample Beta(alpha, beta), pick highest
            scored = []
            for p in eligible:
                a, b = _alpha_beta(p, st)
                score = _sample_beta(rng, a, b)
                scored.append((score, p, a, b))
            scored.sort(key=lambda x: x[0], reverse=True)
            pick, a, b = scored[0][1], scored[0][2], scored[0][3]
            why = f"thompson sample, alpha={a:.1f} beta={b:.1f}, mean={a/(a+b):.2f}"

        selected[axis] = pick["id"]
        details[axis] = {"pattern": pick, "why": why}

    return {
        "tuple": selected,
        "phase": phase,
        "platform": platform,
        "topic_class": topic_class,
        "details": details,
    }


def format_for_prompt(selection: dict[str, Any]) -> str:
    """Render a selection into a system-prompt-friendly Markdown block.

    write_script.py injects this into the user message so Claude knows
    which creative pattern to compose for THIS video.
    """
    lines = [
        "## CREATIVE PATTERN SELECTION (shorts-analysis catalog)",
        "",
        f"Phase: {selection['phase']}. Platform: {selection['platform']}.",
        "",
        "Compose this video around the following creative tuple:",
        "",
    ]
    for axis, pattern_id in selection["tuple"].items():
        info = selection["details"][axis]
        p = info["pattern"]
        lines.append(f"- **{axis}** = `{pattern_id}`")
        lines.append(f"  - {p.get('name', '')} — {p.get('description', '')}")
        if p.get("examples"):
            lines.append(f"  - Examples: {' / '.join(p['examples'][:2])}")
    lines.append("")
    lines.append(
        "All other creative decisions (specific opening line, exact b-roll,"
        " caption animations) follow from this tuple. Stay strictly within the"
        " selected pattern combination — do NOT switch hook types or formats"
        " mid-video. Apply compliance bans per .claude/skills/shorts-analysis/"
        "compliance_bans.json."
    )
    return "\n".join(lines)


def record_publish(platform: str) -> None:
    """Increment publish count for the platform in state. Called by main.py
    after a successful publish (so Phase 0 → Phase 1 transition works)."""
    state = _state()
    counts = state.setdefault("publish_counts", {})
    counts[platform] = counts.get(platform, 0) + 1
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(_STATE_PATH)


def record_video_outcome(
    platform: str, video_id: str, patterns_used: dict[str, str],
    success: bool,
) -> None:
    """Update Beta(alpha, beta) state for each pattern in the tuple.
    Called by analytics.py after view-count + signup data settles (24h+ post-publish).
    """
    state = _state()
    log = state.setdefault("video_log", [])
    # One video = one outcome, ever. Without this a re-score loop charges the
    # same video's patterns again every night (6 242 failures vs 25 successes
    # over 208 real videos before the P0-1 fix drowned the signal in noise).
    if any(v.get("video_id") == video_id for v in log):
        return
    pat_state = state.setdefault("patterns", {})
    for axis, pid in patterns_used.items():
        runtime = pat_state.setdefault(pid, {"success_count": 0, "failure_count": 0})
        if success:
            runtime["success_count"] = runtime.get("success_count", 0) + 1
        else:
            runtime["failure_count"] = runtime.get("failure_count", 0) + 1
    # Append to video-pattern log for attribution
    log.append({
        "video_id": video_id, "platform": platform,
        "patterns_used": patterns_used, "success": success,
    })
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(_STATE_PATH)
