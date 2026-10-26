#!/usr/bin/env python3
"""Generate the DefyCard YouTube channel avatar + banner.

Palette: Catppuccin Mocha, per .claude/skills/design-system/SKILL.md §2.2 —
Base (--surface-background), Blue (--color-indigo, primary brand accent),
Text (--text-primary). No other colors are used.

Glyphs are drawn via a hard-thresholded (non-antialiased) mask so every pixel
in the output is exactly one of the three palette colors — antialiasing would
blend edge pixels into off-palette colors that a palette-conformance test
could never assert against literally.
"""
from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT_PATH = ROOT / "video-generator" / "assets" / "fonts" / "Anton-Regular.ttf"
OUT_DIR = ROOT / "video-generator" / "assets" / "brand"

BASE = (0x1E, 0x1E, 0x2E)   # Mocha Base — --surface-background
BLUE = (0x89, 0xB4, 0xFA)   # Mocha Blue — --color-indigo (primary brand accent)
TEXT = (0xCD, 0xD6, 0xF4)   # Mocha Text — --text-primary

AVATAR_SIZE = (800, 800)
BANNER_SIZE = (2560, 1440)
BANNER_SAFE = (1546, 423)  # YouTube's cross-device-visible centered safe area


def _stamp(canvas: Image.Image, text: str, font: ImageFont.FreeTypeFont,
           color: tuple[int, int, int], center: tuple[int, int]) -> None:
    """Paste `text` onto `canvas` with a hard (non-antialiased) edge."""
    mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(mask).text(center, text, font=font, fill=255, anchor="mm")
    mask = mask.point(lambda p: 255 if p > 127 else 0).convert("1")
    fg = Image.new("RGB", canvas.size, color)
    canvas.paste(fg, mask=mask)


def build_avatar() -> Image.Image:
    img = Image.new("RGB", AVATAR_SIZE, BASE)
    font = ImageFont.truetype(str(FONT_PATH), 420)
    _stamp(img, "DC", font, BLUE, (AVATAR_SIZE[0] // 2, AVATAR_SIZE[1] // 2))
    return img


def build_banner() -> Image.Image:
    img = Image.new("RGB", BANNER_SIZE, BASE)
    cx, cy = BANNER_SIZE[0] // 2, BANNER_SIZE[1] // 2
    safe_top = cy - BANNER_SAFE[1] // 2

    title_font = ImageFont.truetype(str(FONT_PATH), 130)
    sub_font = ImageFont.truetype(str(FONT_PATH), 46)

    _stamp(img, "DefyCard", title_font, BLUE, (cx, safe_top + 110))
    _stamp(img, "COMPARE CRYPTO CARDS. NO HYPE.", sub_font, TEXT, (cx, safe_top + 240))
    _stamp(img, "defycard.com", sub_font, TEXT, (cx, safe_top + 320))
    return img


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    build_avatar().save(OUT_DIR / "avatar.png")
    build_banner().save(OUT_DIR / "banner.png")
    print(f"wrote {OUT_DIR / 'avatar.png'} {AVATAR_SIZE}")
    print(f"wrote {OUT_DIR / 'banner.png'} {BANNER_SIZE}")


if __name__ == "__main__":
    main()
