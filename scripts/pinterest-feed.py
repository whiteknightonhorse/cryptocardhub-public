#!/usr/bin/env python3
"""pinterest-feed.py — CCH-PIN-01. One vertical pin image + one manifest entry per article, for life.

Selection: pages with datePublished >= FEED_SINCE are "new" (all, every run); of the older
ones not yet in the manifest, the BACKFILL_PER_DAY newest by datePublished are added.
The date is schema_article.datePublished, never generated_at (it moves on every edit).
No LLM, no network: text is the page's own frontmatter title. Run via venv python.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

CONTENT = ROOT / "seo-site" / "src" / "content" / "pseo"
PUBLIC = ROOT / "seo-site" / "public"
PINS = PUBLIC / "assets" / "pins"
MANIFEST = ROOT / "seo-site" / "src" / "data" / "pinterest-manifest.json"
LOG = ROOT / "logs" / "pinterest" / "feed.jsonl"
FONT = ROOT / "video-generator" / "assets" / "fonts" / "Anton-Regular.ttf"
SITE = "https://defycard.com"

FEED_SINCE = "2026-10-19"
BACKFILL_PER_DAY = 3
EXCLUDED_BUCKETS = {"news"}
PIN_SIZE = (1000, 1500)
HERO_H = 900

BASE = (0x1E, 0x1E, 0x2E)
BLUE = (0x89, 0xB4, 0xFA)
TEXT = (0xCD, 0xD6, 0xF4)


def _frontmatter(md: Path) -> dict:
    parts = md.read_text(encoding="utf-8").split("---", 2)
    return yaml.safe_load(parts[1]) or {}


def _log(row: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _wrap(draw, text, font, width, max_lines=5):
    lines, cur = [], ""
    for w in text.split():
        t = f"{cur} {w}".strip()
        if cur and draw.textlength(t, font=font) > width:
            lines.append(cur)
            cur = w
        else:
            cur = t
    lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > width:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
    return lines


def render(hero: Path, bucket: str, title: str, out: Path) -> int:
    img = Image.new("RGB", PIN_SIZE, BASE)
    with Image.open(hero) as h:
        h = h.convert("RGB")
        scale = max(PIN_SIZE[0] / h.width, HERO_H / h.height)
        h = h.resize((round(h.width * scale), round(h.height * scale)), Image.LANCZOS)
        left, top = (h.width - PIN_SIZE[0]) // 2, (h.height - HERO_H) // 2
        img.paste(h.crop((left, top, left + PIN_SIZE[0], top + HERO_H)), (0, 0))
    d = ImageDraw.Draw(img)
    pad, w = 60, PIN_SIZE[0] - 120
    d.text((pad, HERO_H + 40), bucket.upper(), font=ImageFont.truetype(str(FONT), 36), fill=BLUE)
    tf = ImageFont.truetype(str(FONT), 64)
    y = HERO_H + 100
    for line in _wrap(d, title, tf, w):
        d.text((pad, y), line, font=tf, fill=TEXT)
        y += 80
    d.text((pad, PIN_SIZE[1] - 40 - 50), "defycard.com", font=ImageFont.truetype(str(FONT), 40), fill=BLUE)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    img.save(tmp, "JPEG", quality=85)
    os.replace(tmp, out)
    return out.stat().st_size


def run() -> dict:
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    today = date.today().isoformat()
    st = dict(date=today, candidates_new=0, candidates_backfill=0, rendered=0, rerendered=0,
              removed=0, image_failed=0, skipped_no_hero=0, skipped_no_date=0, skipped_news=0)
    pages = {md.stem: _frontmatter(md) for md in sorted(CONTENT.glob("*.md"))}

    for slug in [s for s in manifest if s not in pages]:
        del manifest[slug]
        st["removed"] += 1

    cands = []  # (datePublished, slug, fm)
    for slug, fm in pages.items():
        if fm.get("bucket") in EXCLUDED_BUCKETS:
            st["skipped_news"] += 1
            continue
        dp = (fm.get("schema_article") or {}).get("datePublished")
        if not dp:
            st["skipped_no_date"] += 1
            continue
        imgs = fm.get("images") or []
        if not imgs or not imgs[0].get("src") or not (PUBLIC / imgs[0]["src"].lstrip("/")).is_file():
            st["skipped_no_hero"] += 1
            continue
        cands.append((str(dp), slug, fm))

    def pin(slug, fm):
        try:
            hero = PUBLIC / fm["images"][0]["src"].lstrip("/")
            return render(hero, fm["bucket"], fm["title"], PINS / f"{slug}.jpg")
        except Exception as e:  # one bad image must not stop the run
            _log({"event": "image_failed", "slug": slug, "cause": f"{type(e).__name__}: {str(e)[:200]}"})
            st["image_failed"] += 1
            return None

    # title drift → re-render, keep `added`
    for dp, slug, fm in cands:
        e = manifest.get(slug)
        if e and e["title"] != fm["title"]:
            size = pin(slug, fm)
            if size:
                e.update(title=fm["title"], bytes=size)
                st["rerendered"] += 1

    fresh = [c for c in cands if c[1] not in manifest]
    new = [c for c in fresh if c[0] >= FEED_SINCE]
    old = sorted((c for c in fresh if c[0] < FEED_SINCE), key=lambda c: c[1])
    old.sort(key=lambda c: c[0], reverse=True)  # stable: newest first, slug asc within a date
    back = old[:BACKFILL_PER_DAY]
    st["candidates_new"], st["candidates_backfill"] = len(new), len(back)
    for dp, slug, fm in new + back:
        size = pin(slug, fm)
        if size:
            manifest[slug] = dict(url=f"{SITE}/{fm['bucket']}/{slug}/", bucket=fm["bucket"],
                                  title=fm["title"], description=fm.get("description", ""),
                                  image=f"/assets/pins/{slug}.jpg", bytes=size, added=today)
            st["rendered"] += 1

    st["manifest_total"] = len(manifest)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, MANIFEST)
    _log({"event": "run", **st})
    return st


def main() -> int:
    try:
        print(json.dumps(run()))
        return 0
    except Exception as e:
        import telegram_lib
        telegram_lib.alert(f"[pinterest][FAILED] {type(e).__name__}: {str(e)[:200]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
