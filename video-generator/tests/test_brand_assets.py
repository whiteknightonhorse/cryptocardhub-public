"""E-21 gate — channel avatar/banner match the design-system palette and YouTube's spec.

Y49's rule restated: the law is a LITERAL here, not imported from
scripts/generate-brand-assets.py — if the test read its expected colors or
sizes from the generator under test, a mutation that breaks the generator
would move both sides of the comparison and never go red. Palette values are
copied by hand from .claude/skills/design-system/SKILL.md §2.2 (Catppuccin
Mocha: Base, Blue/--color-indigo, Text/--text-primary).
"""
from __future__ import annotations
from pathlib import Path
from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
AVATAR = ROOT / "video-generator" / "assets" / "brand" / "avatar.png"
BANNER = ROOT / "video-generator" / "assets" / "brand" / "banner.png"

# Literal law, not read from the generator.
MOCHA_BASE = (0x1E, 0x1E, 0x2E)
MOCHA_BLUE = (0x89, 0xB4, 0xFA)
MOCHA_TEXT = (0xCD, 0xD6, 0xF4)
ALLOWED_COLORS = {MOCHA_BASE, MOCHA_BLUE, MOCHA_TEXT}

AVATAR_SIZE = (800, 800)
BANNER_SIZE = (2560, 1440)
BANNER_SAFE = (1546, 423)  # YouTube's cross-device-visible centered safe area


def _colors(path: Path) -> set[tuple[int, int, int]]:
    img = Image.open(path).convert("RGB")
    return {rgb for _count, rgb in img.getcolors(maxcolors=img.width * img.height)}


def test_avatar_exists_at_youtube_size():
    assert Image.open(AVATAR).size == AVATAR_SIZE


def test_banner_exists_at_youtube_size():
    assert Image.open(BANNER).size == BANNER_SIZE


def test_avatar_uses_only_design_system_tokens():
    assert _colors(AVATAR) <= ALLOWED_COLORS


def test_banner_uses_only_design_system_tokens():
    assert _colors(BANNER) <= ALLOWED_COLORS


def test_banner_content_stays_inside_safe_area():
    """Every non-background pixel must sit inside the centered 1546x423 safe box.

    A mutation that draws (or drifts) any brand element outside that box is
    exactly the failure this gate exists to catch — text there is invisible
    on mobile/TV crops of the banner.
    """
    img = Image.open(BANNER).convert("RGB")
    w, h = img.size
    sw, sh = BANNER_SAFE
    left, top = (w - sw) // 2, (h - sh) // 2
    right, bottom = left + sw, top + sh

    px = img.load()
    offenders = []
    for y in range(h):
        if top <= y < bottom:
            continue
        for x in range(w):
            if px[x, y] != MOCHA_BASE:
                offenders.append((x, y))
                if len(offenders) > 5:
                    break
        if len(offenders) > 5:
            break
    assert not offenders, f"content outside YouTube safe area at {offenders}"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
