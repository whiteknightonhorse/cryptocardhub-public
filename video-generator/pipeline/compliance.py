"""3-stage moderation — banned vocab + topic + country gate.

Per ai-debates/SKILL.md §10 + write-article §1.2 + etherfi §3.1.

Stages:
  1. topic_safe(topic) — pre-generation
  2. scan_script(script_dict) — real-time, after Claude returns
  3. scan_final(transcript_path, ass_caption_path) — post-generation, on actual outputs
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any

from . import _config

_ROOT = _config.root()
_BANNED = json.loads((_ROOT / "templates/banned_words.json").read_text(encoding="utf-8"))


def _all_banned_phrases() -> list[str]:
    out: list[str] = []
    for cat in ("marketing_absolutes", "investment_advice_red_flags",
                "gambling_vocab", "regulatory_compliance_traps"):
        out.extend(_BANNED["banned_phrases"].get(cat, []))
    return out


_BANNED_RE = re.compile(
    r"\b(" + "|".join(re.escape(p) for p in _all_banned_phrases()) + r")\b",
    re.IGNORECASE,
)

_PLATFORM_BANS = {
    "tiktok": _BANNED.get("tiktok_specific_bans", []),
    "youtube": _BANNED.get("youtube_specific_bans", []),
    "instagram": [],
}

# ether.fi restricted regions per etherfi/SKILL.md §3.1
RESTRICTED_COUNTRIES = {
    "Belarus", "Bangladesh", "China", "Cuba", "Estonia", "Finland", "Hungary",
    "India", "Iraq", "Israel", "Nepal", "Netherlands", "North Korea",
    "Philippines", "Russia", "Syria", "Turkey", "Ukraine", "Venezuela", "Vietnam",
}
RESTRICTED_US_STATES = {
    "AZ", "DE", "GA", "ID", "LA", "MD", "MS", "MO", "MT", "NV", "NM",
    "ND", "OH", "OR", "RI", "SD", "TN", "VT", "WA", "WI",
}


class ComplianceError(Exception):
    """Raised when content fails any moderation stage."""


def topic_safe(topic: dict[str, Any]) -> bool:
    """Stage 1 — pre-generation. Returns True if topic passes filter."""
    title = topic.get("title", "")
    primary = topic.get("primary", "")
    haystack = f"{title} {primary}".lower()

    # Check for restricted-region collisions in topic title (if topic explicitly
    # targets a region, the script-writer must use alternatives, not block).
    # Block only if topic mentions a banned topic bucket like 'medical_advice'.
    for banned_topic in _BANNED.get("banned_topics", []):
        if banned_topic.replace("_", " ") in haystack:
            return False
    return True


def _scan_text(text: str, platform: str | None = None) -> list[str]:
    """Returns a list of banned phrase hits found in `text`."""
    hits = [m.group(1).lower() for m in _BANNED_RE.finditer(text)]
    if platform:
        for ban in _PLATFORM_BANS.get(platform, []):
            if re.search(rf"\b{re.escape(ban)}\b", text, flags=re.IGNORECASE):
                hits.append(ban.lower())
    return list(dict.fromkeys(hits))


def scan_script(script: dict[str, Any], platform: str | None = None) -> None:
    """Stage 2 — real-time. Raises ComplianceError on any banned phrase."""
    blob = " ".join(str(script.get(k, "")) for k in ("hook", "body", "cta", "voiceover"))
    hits = _scan_text(blob, platform=platform)
    if hits:
        raise ComplianceError(f"Banned phrases in script: {hits}")


def scan_final(transcript_text: str, caption_text: str = "",
               platform: str | None = None) -> None:
    """Stage 3 — post-generation. Re-scan the actual TTS output + caption.

    Claude can drift between the script JSON and the rendered voice; this
    catches that. Also catches caption-only banned text.
    """
    hits = _scan_text(transcript_text, platform=platform)
    hits += _scan_text(caption_text, platform=platform)
    hits = list(dict.fromkeys(hits))
    if hits:
        raise ComplianceError(f"Banned phrases in final assets: {hits}")


def country_gate_advice(region: str | None) -> dict[str, Any]:
    """Returns advice for the script writer about ether.fi restricted regions.

    Output:
      {recommend_etherfi: bool, alternatives: [str]}
    """
    if not region:
        return {"recommend_etherfi": True, "alternatives": []}
    region = region.strip()
    if region in RESTRICTED_COUNTRIES or region in RESTRICTED_US_STATES:
        return {
            "recommend_etherfi": False,
            "alternatives": ["Crypto.com Card", "Bybit Card", "RedotPay"],
        }
    return {"recommend_etherfi": True, "alternatives": []}
