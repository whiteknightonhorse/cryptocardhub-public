#!/usr/bin/env python3
"""
migrate-images-local.py — one-off migration: rewrite all existing pSEO
articles to reference LOCAL images instead of Unsplash CDN URLs.

For each .md under seo-site/src/content/pseo/:
  1. Parse YAML frontmatter — find `images: [...]` array.
  2. For each entry whose `src` starts with https://images.unsplash.com/,
     download the JPG into public/assets/pseo/<slug>-<idx>.jpg.
  3. Rewrite the `src` field to /assets/pseo/<slug>-<idx>.jpg.
  4. Also rewrite any inline `<img src="https://images.unsplash.com/...">`
     anchors in the body markdown to the same local path.
  5. Save the .md back atomically.

Idempotent: existing local files are reused; already-local src is left alone.
Run from repo root:
    venv/bin/python scripts/migrate-images-local.py
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PSEO_DIR = ROOT / "seo-site" / "src" / "content" / "pseo"
ASSETS_DIR = ROOT / "seo-site" / "public" / "assets" / "pseo"
ASSETS_URL = "/assets/pseo"

UA = "DefyCardBot/1.0 (+https://defycard.com; image-fetch)"


def download(url: str, slug: str, idx: int) -> str | None:
    if not url.startswith("https://images.unsplash.com/"):
        return None
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{slug}-{idx}.jpg"
    out = ASSETS_DIR / fname
    if out.exists() and out.stat().st_size > 1024:
        return f"{ASSETS_URL}/{fname}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as resp:
            ctype = resp.headers.get("content-type", "")
            if not ctype.startswith("image/"):
                return None
            data = resp.read()
        if len(data) < 1024:
            return None
        out.write_bytes(data)
        return f"{ASSETS_URL}/{fname}"
    except Exception as e:
        print(f"  ! download {url[:80]}: {e}", file=sys.stderr)
        return None


def split_frontmatter(text: str) -> tuple[str, str]:
    """Returns (frontmatter_yaml_block_only, body_after_second_---)."""
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end < 0:
        return "", text
    return text[4:end], text[end + 5:]


def migrate_one(path: Path) -> tuple[int, int]:
    """Returns (downloaded, rewritten) counts for this article."""
    text = path.read_text(encoding="utf-8")
    fm_yaml, body = split_frontmatter(text)
    if not fm_yaml:
        return (0, 0)

    # Find slug: line `slug: "best-crypto-card-2026"`
    m = re.search(r'^slug:\s*"([^"]+)"', fm_yaml, re.MULTILINE)
    if not m:
        print(f"  ? no slug in {path.name}", file=sys.stderr)
        return (0, 0)
    slug = m.group(1)

    # Find the images: line — JSON array on same line per pseo-generate.py output
    img_match = re.search(r'^images:\s*(\[.*?\])\s*$', fm_yaml, re.MULTILINE | re.DOTALL)
    if not img_match:
        return (0, 0)
    try:
        images = json.loads(img_match.group(1))
    except json.JSONDecodeError as e:
        print(f"  ! parse images in {path.name}: {e}", file=sys.stderr)
        return (0, 0)

    downloaded = 0
    url_remap: dict[str, str] = {}
    new_images: list[dict] = []
    for idx, img in enumerate(images):
        src = (img or {}).get("src", "")
        if src.startswith("https://images.unsplash.com/"):
            local = download(src, slug, idx)
            if local:
                url_remap[src] = local
                downloaded += 1
                img = {**img, "src": local}
        new_images.append(img)

    body_has_remote = "https://images.unsplash.com" in body
    if not url_remap and not body_has_remote:
        return (0, 0)

    # Rewrite frontmatter `images:` line
    new_images_json = json.dumps(new_images, ensure_ascii=False)
    fm_yaml_new = re.sub(
        r'^images:\s*\[.*?\]\s*$',
        f'images: {new_images_json}',
        fm_yaml,
        count=1,
        flags=re.MULTILINE | re.DOTALL,
    )

    # Rewrite inline <img src="https://images.unsplash.com/..."> in body.
    # Body URLs are HTML-encoded (&amp;) so direct string replace doesn't
    # match the raw frontmatter URL. Iterate `<img src="...unsplash...">`
    # tags in document order and map each to images[idx] (the body emits
    # imgs in the same order pseo-generate.py wrote IMAGE_0/IMAGE_1/IMAGE_2).
    body_new = body
    rewritten_body = 0
    img_re = re.compile(r'(<img\s+[^>]*?src=")(https://images\.unsplash\.com/[^"]+)(")')
    img_iter = iter(new_images)

    def _replace(m):
        nonlocal rewritten_body
        try:
            target_img = next(img_iter)
        except StopIteration:
            return m.group(0)
        new_src = (target_img or {}).get("src", "")
        if not new_src.startswith(ASSETS_URL):
            return m.group(0)
        rewritten_body += 1
        return f"{m.group(1)}{new_src}{m.group(3)}"

    body_new = img_re.sub(_replace, body_new)

    new_text = "---\n" + fm_yaml_new + "\n---\n" + body_new
    path.write_text(new_text, encoding="utf-8")
    return (downloaded, len(url_remap) + rewritten_body)


def main() -> int:
    md_files = sorted(PSEO_DIR.glob("*.md"))
    print(f"[migrate] scanning {len(md_files)} files in {PSEO_DIR}")
    total_dl = 0
    total_rw = 0
    touched = 0
    for p in md_files:
        if not p.exists():       # file disappeared mid-run (concurrent edit)
            continue
        try:
            dl, rw = migrate_one(p)
        except Exception as e:
            print(f"  ! {p.name}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        if dl or rw:
            print(f"  ✓ {p.name}: downloaded={dl} rewrites={rw}")
            touched += 1
            total_dl += dl
            total_rw += rw
    print(f"[migrate] done — touched {touched}/{len(md_files)} files; "
          f"downloaded {total_dl}; rewrites {total_rw}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
