"""Pexels Videos primary, Pexels Photos + Unsplash fallback.

Cloudflare-safe download per polymarket vg4 iter-17.2/17.3:
  - User-Agent: Mozilla/5.0 defycard-video/1.0
  - Referer: https://www.pexels.com/

7-day dedup ledger via dedup.py.

Y58 (2026-08-12) — why this module refuses instead of padding:

`Xm2-vLgwrc8` shipped publicly as four solid-blue slides. Nothing had failed:
every provider answered 200, the keys were live, the VPN was never in this
path. The dedup ledger had simply been eaten — the long-form pilots burned 144
media URLs on 2026-08-11 against a normal ~8/day, and search depth here was a
fixed top-20/top-20/top-15 with no pagination. By the time the india slot ran,
its whole result page sat inside the 7-day `media_used_recently` window, the
chain returned 0, and a top-up loop quietly padded the shortfall with dry-run
placeholders "so render can finish".

So: placeholders exist ONLY for dry runs, a live run that cannot reach
MIN_REAL raises, and pagination cures the starvation that made the refusal
necessary in the first place. Every zero now carries its cause in JSONL —
"nothing found", "everything deduped", "key rejected" and "network down" were
one indistinguishable empty list before.
"""
from __future__ import annotations
import json
import os
import time
import urllib.parse as up
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from . import dedup

_UA = "Mozilla/5.0 defycard-video/1.0"
_PEXELS = "https://api.pexels.com"
_UNSPLASH = "https://api.unsplash.com"

# A live slot needs at least this many REAL assets. render loops what it gets,
# so two is enough to avoid a slideshow of one frame; zero or one is a defect,
# never a thing to pad over.
MIN_REAL = 2
# Pagination depth. The starvation above was a depth problem, not a supply
# problem: the photo pools for these queries are orders of magnitude deeper
# than one page.
_MAX_PAGES = 5


class NoVisualsError(RuntimeError):
    """Fewer than MIN_REAL real assets were found, and the cause (`_zero_cause`)
    says it will happen again on retry — `.cause` lets a caller (T-0140) tell a
    page property (query structurally unfindable) apart from a machine failure
    (`all visual providers failed`, still a bare RuntimeError, never this class)."""

    def __init__(self, message: str, cause: str | None) -> None:
        super().__init__(message)
        self.cause = cause


# T-0140: only `empty_results` is a page property — every provider answered,
# and the query is fixed by the page's own frontmatter, so retrying it changes
# nothing. `all_deduped` (supply, cures itself in <=7d) and the machine causes
# (`provider_error`, `non_200`, `downloads_failed`, `unknown`) are transient.
PAGE_PROPERTY_CAUSES = frozenset({"empty_results"})


def _pexels_key() -> str | None:
    return os.environ.get("PEXELS_API_KEY")


def _unsplash_key() -> str | None:
    return os.environ.get("UNSPLASH_ACCESS_KEY")


def _http_get(url: str, headers: dict[str, str] | None = None,
              params: dict[str, Any] | None = None, timeout: int = 20) -> requests.Response:
    h = {"User-Agent": _UA, "Referer": "https://www.pexels.com/", **(headers or {})}
    return requests.get(url, headers=h, params=params, timeout=timeout)


def _log_path() -> Path:
    d = Path(os.environ.get("VG_LOG_DIR", "/home/cryptocardhub/logs/video"))
    return d / f"{datetime.now(timezone.utc).date().isoformat()}.jsonl"


def _jsonl(record: dict[str, Any]) -> None:
    """Append one measurement line. Never raises: a blind logger must not be
    able to take down the run it is describing."""
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("VG_LOG_DIR"):
        return  # a test must not write into the production measurement log
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        print(f"[fetch_visuals] jsonl write failed (non-fatal): {e}")


def _pexels_videos(query: str, want: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    t0 = time.monotonic()
    key = _pexels_key()
    if not key:
        return [], {"provider": "pexels_video", "status": "no_key", "found": 0, "after_dedup": 0}
    out: list[dict[str, Any]] = []
    found = 0
    status: Any = 200
    for page in range(1, _MAX_PAGES + 1):
        r = _http_get(f"{_PEXELS}/videos/search",
                      headers={"Authorization": key},
                      params={"query": query, "per_page": 20,
                              "orientation": "portrait", "page": page})
        status = r.status_code
        if r.status_code != 200:
            break
        videos = r.json().get("videos", [])
        found += len(videos)
        if not videos:
            break
        for v in videos:
            files = sorted(
                [f for f in v.get("video_files", []) if f.get("quality") == "hd" and f.get("width", 0) >= 720],
                key=lambda f: f.get("width", 0), reverse=True,
            )
            if not files:
                continue
            chosen = files[0]
            url = chosen["link"]
            if dedup.media_used_recently(url):
                continue
            out.append({
                "kind": "video", "url": url,
                "width": chosen.get("width"), "height": chosen.get("height"),
                "duration": v.get("duration"), "source": "pexels_video",
            })
            if len(out) >= want:
                break
        if len(out) >= want:
            break
    return out, {"provider": "pexels_video", "status": status, "found": found,
                 "after_dedup": len(out), "pages": page,
                 "elapsed_ms": int((time.monotonic() - t0) * 1000)}


def _pexels_photos(query: str, want: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    t0 = time.monotonic()
    key = _pexels_key()
    if not key:
        return [], {"provider": "pexels_photo", "status": "no_key", "found": 0, "after_dedup": 0}
    out: list[dict[str, Any]] = []
    found = 0
    status: Any = 200
    for page in range(1, _MAX_PAGES + 1):
        r = _http_get(f"{_PEXELS}/v1/search",
                      headers={"Authorization": key},
                      params={"query": query, "per_page": 20,
                              "orientation": "portrait", "page": page})
        status = r.status_code
        if r.status_code != 200:
            break
        photos = r.json().get("photos", [])
        found += len(photos)
        if not photos:
            break
        for p in photos:
            url = p.get("src", {}).get("large2x") or p.get("src", {}).get("large")
            if not url or dedup.media_used_recently(url):
                continue
            out.append({"kind": "photo", "url": url, "source": "pexels_photo"})
            if len(out) >= want:
                break
        if len(out) >= want:
            break
    return out, {"provider": "pexels_photo", "status": status, "found": found,
                 "after_dedup": len(out), "pages": page,
                 "elapsed_ms": int((time.monotonic() - t0) * 1000)}


def _unsplash_photos(query: str, want: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    t0 = time.monotonic()
    key = _unsplash_key()
    if not key:
        return [], {"provider": "unsplash_photo", "status": "no_key", "found": 0, "after_dedup": 0}
    out: list[dict[str, Any]] = []
    found = 0
    status: Any = 200
    for page in range(1, _MAX_PAGES + 1):
        r = _http_get(f"{_UNSPLASH}/search/photos",
                      headers={"Authorization": f"Client-ID {key}"},
                      params={"query": query, "per_page": 15,
                              "orientation": "portrait", "page": page})
        status = r.status_code
        if r.status_code != 200:
            break
        results = r.json().get("results", [])
        found += len(results)
        if not results:
            break
        for p in results:
            url = p.get("urls", {}).get("regular")
            if not url or dedup.media_used_recently(url):
                continue
            out.append({"kind": "photo", "url": url, "source": "unsplash_photo"})
            if len(out) >= want:
                break
        if len(out) >= want:
            break
    return out, {"provider": "unsplash_photo", "status": status, "found": found,
                 "after_dedup": len(out), "pages": page,
                 "elapsed_ms": int((time.monotonic() - t0) * 1000)}


def _download(url: str, out_path: Path) -> None:
    r = _http_get(url, timeout=60)
    r.raise_for_status()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(r.content)


def _placeholder(kind: str, idx: int, out_dir: Path) -> dict[str, Any]:
    """Solid-color JPEG placeholder. DRY-RUN ONLY — see module docstring.

    Never call this from a live path. A published video made of these is
    indistinguishable, to a viewer and to YouTube's inauthentic-content
    tooling, from a channel that uploads blank slides.
    """
    from PIL import Image  # lazy import; only required for dry-run/placeholder
    palette = ["#1e66f5", "#FF6B35", "#00C49F", "#FFD700", "#FF1744"]
    color = palette[idx % len(palette)]
    p = out_dir / f"placeholder_{idx:02d}.jpg"
    Image.new("RGB", (1080, 1920), color).save(p, "JPEG", quality=85)
    return {"kind": "photo", "url": f"placeholder://{color}", "source": "placeholder", "path": str(p)}


def _chain(query: str, want: int) -> tuple[list[list[dict[str, Any]]], list[dict[str, Any]], int]:
    """Every provider tried in order; a single failure is survivable, total
    failure is not.

    One slow or blocked source must not abort a fetch another source can
    already satisfy — so a provider that raises yields [] and the chain
    continues. Each provider also returns its own measurement so a zero can
    name its cause.
    """
    hits: list[list[dict[str, Any]]] = []
    metas: list[dict[str, Any]] = []
    failed = 0
    for fn in (_pexels_videos, _pexels_photos, _unsplash_photos):
        try:
            res = fn(query, want)
            # Providers return (items, meta); a bare list is accepted so a
            # caller or test that stubs a provider the old way still works.
            items, meta = res if isinstance(res, tuple) else (res, {
                "provider": fn.__name__, "status": "stubbed",
                "found": len(res), "after_dedup": len(res)})
            hits.append(items)
            metas.append(meta)
        except Exception as e:  # noqa: BLE001
            failed += 1
            hits.append([])
            metas.append({"provider": fn.__name__, "status": "exception",
                          "error": type(e).__name__, "found": 0, "after_dedup": 0})
            print(f"[fetch_visuals] {fn.__name__} failed (non-fatal): {e}")
    return hits, metas, failed


def _zero_cause(metas: list[dict[str, Any]], failed: int,
                download_errors: int, real: int, need: int = MIN_REAL) -> str | None:
    """Name the cause of a shortfall. Four different zeros used to look the
    same from outside this module; the report is worthless without the cause."""
    if real >= need:
        return None
    if failed == 3:
        return "provider_error"
    if any(isinstance(m.get("status"), int) and m["status"] != 200 for m in metas):
        return "non_200"
    if sum(m.get("found", 0) for m in metas) == 0:
        return "empty_results"
    if sum(m.get("after_dedup", 0) for m in metas) == 0:
        return "all_deduped"
    if download_errors:
        return "downloads_failed"
    return "unknown"


def fetch(query: str, want: int, out_dir: Path,
          dry_run: bool = False, platform: str | None = None,
          topic_id: Any = None) -> list[dict[str, Any]]:
    """Fetch `want` clips/photos. Falls back through the provider chain.

    Returns metadata list with `path` filled for downloaded files and marks
    each URL in the dedup ledger. Raises when fewer than MIN_REAL real assets
    can be had — a short slot is refused, never padded.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if dry_run:
        return [_placeholder("photo", i, out_dir) for i in range(want)]
    if not (_pexels_key() or _unsplash_key()):
        raise RuntimeError(
            "no visual provider key configured (PEXELS_API_KEY / "
            "UNSPLASH_ACCESS_KEY) — refusing to render from placeholders"
        )

    # A caller asking for ONE asset is satisfied by one: the floor exists to
    # stop a slideshow, not to out-vote the request.
    need = min(MIN_REAL, want)

    chain, metas, failed = _chain(query, want)

    out: list[dict[str, Any]] = []
    download_errors = 0
    for hits in chain:
        for h in hits:
            ext = ".mp4" if h["kind"] == "video" else ".jpg"
            local = out_dir / f"clip_{len(out):02d}{ext}"
            try:
                _download(h["url"], local)
            except Exception as e:  # noqa: BLE001
                download_errors += 1
                print(f"[fetch_visuals] download failed: {type(e).__name__} {h['url'][:80]}")
                continue
            dedup.record_media(h["url"], h["source"])
            out.append({**h, "path": str(local)})
            if len(out) >= want:
                break
        if len(out) >= want:
            break

    base = {"ts": datetime.now(timezone.utc).isoformat(), "platform": platform,
            "topic_id": topic_id, "query": query, "want": want}
    for m in metas:
        _jsonl({**base, **m, "download_errors": download_errors})
    cause = _zero_cause(metas, failed, download_errors, len(out), need)
    _jsonl({**base, "real": len(out), "placeholders": 0, "zero_cause": cause})

    if failed == 3:
        raise RuntimeError(
            "all visual providers failed — refusing to top up with placeholders "
            "and ship a video of solid-colour slides"
        )
    if len(out) < need:
        raise NoVisualsError(
            f"only {len(out)} real asset(s) for {query!r} (need {need}); "
            f"cause={cause}. Refusing the slot — a padded render publishes "
            f"solid-colour slides, which is what Y58 was written to stop.",
            cause,
        )
    return out
