"""Claude Haiku script writer — PAS / FAB / AIDA per templates/script_system_prompt.md.

Cost gate: assert "haiku" in WRITER_MODEL at module load.
Subprocess invariants per polymarket vg5 hard rule #5:
  - --fallback-model claude-haiku-4-5-20251001
  - --no-session-persistence
  - log (stderr OR stdout OR "no output") on result

Dry-run mode: returns a deterministic mock script shaped to the same JSON
schema so the rest of the pipeline can run. (claude --print uses Max plan
auth from ~/.claude/.credentials.json; ANTHROPIC_API_KEY is unset in pSEO
cron; presence-of-key alone does NOT gate dry-run any more.)

Creative-pattern selection: per shorts-analysis/SKILL.md, pattern_selector.py
chooses one pattern per creative axis (hook / format / pacing / etc.) via
Thompson sampling and the writer composes around that tuple.
"""
from __future__ import annotations
import hashlib
import json
import math
import os
import subprocess
from pathlib import Path
from typing import Any

from . import _config, compliance, pattern_selector

_ROOT = _config.root()
_SYS_PROMPT = (_ROOT / "templates" / "script_system_prompt.md").read_text(encoding="utf-8")

WRITER_MODEL = os.environ.get("WRITER_MODEL", "claude-haiku-4-5-20251001")
assert "haiku" in WRITER_MODEL, "Writer must be Haiku class for cost (CLAUDE.md §0.2)"

# --- Y49 P1-2: script-length floor -------------------------------------------
# Measured, not assumed: over the 38 real renders on disk (script.json vs
# voice.mp3) edge-tts speaks 1.96 words/s — median; p10 1.78, p90 2.20. The
# topic enricher's 2.4 words/s is 22% optimistic.
# Render length is tts + 0.5 s since Y49 P1-1, so to fill the pool's OWN
# minimum duration band a script needs (band_lo - 0.5) * 1.96 words.
# YouTube band_lo = 30 s -> ceil(29.5 * 1.96) = 58 words.
# Below the floor the script is regenerated exactly ONCE and then accepted as
# is: a live cron may never loop on a model that keeps returning short copy.
_WORDS_PER_SECOND = 1.96
_RENDER_TAIL_SECONDS = 0.5


def min_words(topic: dict[str, Any]) -> int:
    """Lower word bound for `topic`, derived from its duration band."""
    band = topic.get("duration_band") or [topic.get("target_seconds", 0.0)]
    return math.ceil(max(0.0, float(band[0]) - _RENDER_TAIL_SECONDS) * _WORDS_PER_SECOND)


def _word_count(script: dict[str, Any]) -> int:
    return len(str(script.get("voiceover", "")).split())


def _build_user_prompt(topic: dict[str, Any], creative: dict[str, Any] | None = None) -> str:
    """Build the user message for the Claude script-writer.

    Includes:
      - topic metadata (id, class, primary keyword, target length)
      - country gate advice (per etherfi/SKILL.md restricted regions)
      - creative-pattern tuple (per shorts-analysis/SKILL.md §11) — the
        hook / format / audio / pacing / duration / visual / cta_bridge
        Claude must compose around.
    """
    region_advice = compliance.country_gate_advice(topic.get("region"))
    payload = {
        "topic_id": topic["id"],
        "topic_class": topic.get("class", "MOFU"),
        "topic_pool": topic.get("pool", "finance"),
        "platform": topic["platform"],
        "title_seed": topic["title"],
        "primary_keyword": topic.get("primary", ""),
        "target_seconds": topic["target_seconds"],
        "target_words": topic["target_words"],
        "country_gate": region_advice,
    }
    payload_json = json.dumps(payload, ensure_ascii=False, indent=2)
    if creative is None:
        return payload_json
    creative_block = pattern_selector.format_for_prompt(creative)
    return f"{payload_json}\n\n{creative_block}\n"


def _mock_script(topic: dict[str, Any]) -> dict[str, Any]:
    """Deterministic mock for --dry-run / no-API-key path.

    Schema-faithful to templates/script_system_prompt.md §Output schema.
    Hash-seeded so repeated dry-runs produce stable output.
    """
    seed = hashlib.sha256(f"{topic['id']}:{topic['platform']}".encode()).hexdigest()[:8]
    target = topic["target_words"]
    voice_lead = (
        f"Up to three percent cashback on this card. {topic['title']} "
        "I tracked every coffee, every tap, for thirty days. "
        "Cashback in ETH, sitting in my wallet, still earning yield. "
        "No selling, no tax event, no foreign fees on USD or EUR. "
        "Apple Pay works. Tap to pay works. The dashboard shows the math."
    )
    cta = "See the full breakdown at defycard.com."
    voiceover = f"{voice_lead} {cta}"
    # trim to target words
    words = voiceover.split()
    if len(words) > target:
        voiceover = " ".join(words[:target]) + "."
    return {
        "hook": f"3% cashback. 30 days. Card #{topic['id']}.",
        "body": voice_lead,
        "cta": cta,
        "voiceover": voiceover,
        "visual_cues": [
            {"t": 0.0, "scene": "wallet_dashboard"},
            {"t": 4.0, "scene": "coffee_shop"},
            {"t": 10.0, "scene": "merchant_pos"},
            {"t": 18.0, "scene": "phone_reveal"},
            {"t": int(topic["target_seconds"] - 5), "scene": "wallet_dashboard"},
        ],
        "hashtags": {"youtube_visible": [], "youtube_api": [], "instagram": [], "tiktok": []},
        "thumbnail_hook": f"3% CASHBACK | {topic['title'][:24].upper()}",
        "_mock": True,
        "_seed": seed,
    }


def write(topic: dict[str, Any], dry_run: bool = False,
          creative: dict[str, Any] | None = None) -> dict[str, Any]:
    """Returns a script dict matching the system-prompt schema.

    Args:
        topic: enriched topic dict from fetch_topic.pick(...)
        dry_run: if True, return a deterministic mock instead of calling Claude
        creative: optional creative-pattern tuple from pattern_selector.select(...).
                  If None, the writer composes without a pattern hint (legacy mode).
    """
    if dry_run:
        script = _mock_script(topic)
    else:
        # Auto-select a creative pattern if caller didn't pre-select one.
        if creative is None:
            try:
                creative = pattern_selector.select(
                    platform=topic["platform"],
                    topic_class=topic.get("class"),
                )
            except FileNotFoundError:
                # shorts-analysis catalog not installed — proceed without
                creative = None
        script = _call_claude(topic, creative)
        floor = min_words(topic)
        if _word_count(script) < floor:
            print(f"[write_script] voiceover {_word_count(script)}w < floor {floor}w "
                  f"— regenerating once", flush=True)
            retry = _call_claude(topic, creative, note=(
                f"The previous draft was too short. The voiceover MUST contain at "
                f"least {floor} words (target {topic.get('target_words', floor)}). "
                f"Write the full length — do not pad with filler, add substance."
            ))
            if _word_count(retry) >= _word_count(script):
                script = retry
            if _word_count(script) < floor:
                print(f"[write_script] still {_word_count(script)}w < {floor}w after "
                      f"one retry — accepting the short script", flush=True)
        if creative is not None:
            script["_creative_patterns"] = creative["tuple"]
            script["_creative_phase"] = creative["phase"]
    compliance.scan_script(script, platform=topic["platform"])
    return script


def _call_claude(topic: dict[str, Any], creative: dict[str, Any] | None,
                 note: str | None = None) -> dict[str, Any]:
    """Invoke `claude --print` per polymarket vg5 hard rule #5 invariants.

    `note` is appended to the user message on the single length retry (Y49 P1-2).
    """
    user_msg = _build_user_prompt(topic, creative)
    if note:
        user_msg = f"{user_msg}\n\n{note}\n"
    sys_prompt_path = _ROOT / "templates" / "script_system_prompt.md"
    # Note: do NOT pass `--fallback-model` when main is Haiku; Claude CLI
    # rejects identical main+fallback. Polymarket vg5 hard rule #5 originated
    # in a Sonnet+Haiku context where the two differ. Our cost-gate forces
    # main=Haiku so fallback is omitted entirely (a transient API error just
    # causes a retry on the next cron tick — same effective behavior).
    cmd = [
        "claude", "--print",
        "--append-system-prompt-file", str(sys_prompt_path),
        "--no-session-persistence",
        "--model", WRITER_MODEL,
    ]
    # Timeout 240s — system prompt is ~10KB (Editorial law + Creative pattern
    # selection block + Compliance LAW + Output schema + 2 examples), so
    # response generation can take 60-180s under normal conditions. Polymarket
    # vg5 noted argv-bloat could push to 15min; we use --append-system-prompt-file
    # + stdin so we're not in that bucket, but headroom matters.
    proc = subprocess.run(cmd, input=user_msg, capture_output=True, text=True, timeout=240)
    out = (proc.stdout or "") + (proc.stderr or "") or "no output"
    # log via standard channels — caller handles write to logs/
    print(f"[write_script] claude exit={proc.returncode} chars={len(out)}", flush=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"claude --print failed: {out[:400]}")
    raw = proc.stdout.strip()
    # Strip Markdown code fences if present — Claude sometimes wraps JSON in
    # ```json ... ``` despite the system prompt's "no Markdown" instruction.
    if raw.startswith("```"):
        # Remove opening fence (with or without 'json' tag)
        first_nl = raw.find("\n")
        if first_nl != -1:
            raw = raw[first_nl + 1:]
        # Remove closing fence
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3].rstrip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"claude returned non-JSON ({len(raw)}c): {raw[:400]}"
        ) from exc
