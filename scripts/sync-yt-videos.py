#!/usr/bin/env python3
"""
sync-yt-videos.py — bridge published.db → seo-site/src/content/videos/.

Runs at the top of `scripts/deploy.sh` (BEFORE `pnpm build`) so every Astro
build picks up newly-published YouTube videos. The output is one JSON file
per video — Astro's content-collections loader globs `**/*.json` and feeds
them to `/videos/`, `/videos/<id>/`, and the custom video sitemap serializer.

Why local files (not a YouTube API call): the script.json from each render
already contains title, voiceover, hashtags, and topic metadata. Going to
the API would burn quota for data we already have on disk.

Topic→pSEO matching:
  - BOFU/MOFU topics with a `target_pseo_slug` hint in topic_pool match by
    explicit slug (preferred for new topics that mention crypto cards by
    name). Otherwise we fall back to keyword overlap with `primary_keyword`
    in the pSEO collection.
  - VIRAL topics rarely match a crypto-card article and are surfaced only on
    /videos/ + /videos/<id>/.

Run manually:
  /home/cryptocardhub/video-generator/venv/bin/python scripts/sync-yt-videos.py
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "video-generator" / "state" / "published.db"
OUTPUT_DIR = ROOT / "video-generator" / "output"
TOPIC_POOL = ROOT / "video-generator" / "templates" / "topic_pool.json"
VIRAL_POOL = ROOT / ".claude" / "skills" / "shorts-analysis" / "topic_pool_viral.json"
VIDEOS_DIR = ROOT / "seo-site" / "src" / "content" / "videos"
PSEO_DIR = ROOT / "seo-site" / "src" / "content" / "pseo"


def _yt_availability(video_ids: list[str]) -> dict[str, dict]:
    """Batch-call YouTube Data API to verify each video is alive AND public.

    Returns a dict keyed by video_id with `{status, snippet, content_details}`
    for every id present in the YT response. IDs missing from the response
    were DELETED on YouTube (operator removal, ToS strike, etc.) — caller
    treats them as ineligible.

    No-op when YT credentials are missing or when the list is empty (returns
    empty dict; caller falls through to "publish all DB rows" mode for tests).

    Cost: 1 quota unit per batch (ids.list joined comma-separated, up to 50
    per call). Negligible against the 10000/day default quota.
    """
    if not video_ids:
        return {}
    refresh = os.environ.get("YOUTUBE_REFRESH_TOKEN", "").strip()
    cid = os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
    csec = os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()
    if not (refresh and cid and csec):
        print(
            "[sync-yt-videos] WARN: YT creds not in env — availability "
            "check skipped (every DB row will be written; deleted videos "
            "may end up in sitemap)",
            file=sys.stderr,
        )
        return {}
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as e:
        print(f"[sync-yt-videos] WARN: google libs missing ({e}); skipping availability check",
              file=sys.stderr)
        return {}
    creds = Credentials(
        token=None, refresh_token=refresh,
        client_id=cid, client_secret=csec,
        token_uri="https://oauth2.googleapis.com/token",
    )
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    out: dict[str, dict] = {}
    # YT API max 50 IDs per call
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        resp = yt.videos().list(
            part="status,snippet,contentDetails",
            id=",".join(batch),
        ).execute()
        for v in resp.get("items", []):
            out[v["id"]] = v
    return out


def _is_publishable(meta: dict | None) -> tuple[bool, str]:
    """Return (eligible, reason). A video is eligible iff:
      - present in YT response (not deleted)
      - privacyStatus == public
      - uploadStatus == processed (not failed/rejected)
      - madeForKids == False (kids content excluded from card affiliate site)
    """
    if meta is None:
        return False, "deleted"
    st = meta.get("status") or {}
    if st.get("privacyStatus") != "public":
        return False, f"privacy={st.get('privacyStatus')}"
    if st.get("uploadStatus") != "processed":
        return False, f"uploadStatus={st.get('uploadStatus')}"
    if st.get("madeForKids") is True:
        return False, "made_for_kids"
    return True, "ok"


def _topic_index() -> dict[int, dict]:
    """Merge BOFU/MOFU/TOFU topic_pool + VIRAL pool by `id`."""
    idx: dict[int, dict] = {}
    for path in (TOPIC_POOL, VIRAL_POOL):
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))

        def walk(node):
            if isinstance(node, dict):
                if "id" in node and isinstance(node["id"], int):
                    idx[node["id"]] = node
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for it in node:
                    walk(it)

        walk(data)
    return idx


def _find_render_dir(channel: str, ts: str, topic_id: int) -> Path | None:
    """Locate output/<channel>/<YYYY-MM-DD>/topic_<id>/ for a publish row.

    Render date may be the day BEFORE the publish ts (publish step runs in a
    later cron tick), so try both. Returns the directory; caller picks
    summary.json (runtime-enriched topic + publish info) and/or script.json
    (raw model output: hook, body, hashtags).
    """
    pub_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    candidates = [pub_dt.date(), (pub_dt - _td(seconds=86400)).date()]
    for d in candidates:
        p = OUTPUT_DIR / channel / d.isoformat() / f"topic_{topic_id}"
        if p.is_dir():
            return p
    return None


def _td(**kw):
    from datetime import timedelta
    return timedelta(**kw)


def _slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    text = re.sub(r"[-\s]+", "-", text)
    return text[:80]


_VIRAL_CATEGORY_FALLBACK = {
    # Each viral topic category → existing pSEO bucket/slug we'd want the
    # video to be embedded on. Keep the mapping conservative (broad parent
    # articles, not narrow long-tail pages) so a viral travel hook lifts
    # /for/crypto-card-for-travel/ rather than a random country page.
    "travel": "for/crypto-card-for-travel",
    "finance": "hub/best-crypto-card-2026",
    "money": "hub/best-crypto-card-2026",
    "lifestyle": "hub/best-crypto-card-2026",
}


def _match_pseo_slug(topic: dict, script: dict) -> str | None:
    """Best-effort pSEO match. Returns slug (relative path under bucket) only
    when confidence is high; otherwise returns None and the video appears
    only on /videos/ pages.
    """
    # 1. Explicit hint wins
    if topic.get("target_pseo_slug"):
        return str(topic["target_pseo_slug"]).strip("/") or None
    # 2. VIRAL: fall back on category map. We still link the page but only
    #    to a hand-curated parent article — never to a narrow long-tail.
    if topic.get("class") == "VIRAL" or topic.get("pool") == "viral":
        cat = (topic.get("category") or "").lower()
        slug = _VIRAL_CATEGORY_FALLBACK.get(cat)
        # Sanity: only return slug when the article actually exists, so we
        # never embed a video on a 404.
        if slug:
            bucket, slug_part = slug.split("/", 1)
            md_candidates = list(PSEO_DIR.glob(f"{slug_part}.md"))
            if md_candidates:
                return slug
        return None
    # 3. Keyword overlap on `primary_keyword` for BOFU/MOFU/TOFU only
    primary = (topic.get("primary") or "").lower()
    if not primary:
        return None
    best: tuple[float, str | None] = (0.0, None)
    for md in PSEO_DIR.glob("*.md"):
        try:
            head = md.read_text(encoding="utf-8")[:2000]
        except OSError:
            continue
        m = re.search(r'^primary_keyword:\s*"([^"]+)"', head, re.MULTILINE)
        if not m:
            continue
        target = m.group(1).lower()
        # cheap overlap: shared word count / max words
        a = set(re.findall(r"\w+", primary))
        b = set(re.findall(r"\w+", target))
        if not a or not b:
            continue
        score = len(a & b) / max(len(a), len(b))
        if score > best[0]:
            slug_match = re.search(r'^slug:\s*"([^"]+)"', head, re.MULTILINE)
            bucket_match = re.search(r'^bucket:\s*"([^"]+)"', head, re.MULTILINE)
            if slug_match and bucket_match:
                best = (score, f"{bucket_match.group(1)}/{slug_match.group(1)}")
    if best[0] >= 0.4:  # arbitrary; tune as the corpus grows
        return best[1]
    return None


def _strip_markdown_emphasis(title: str) -> str:
    """Убрать markdown-выделение из заголовка, пришедшего извне.

    Заголовок берётся из ЖИВОГО снимка YouTube, то есть из поля, которое мы не контролируем.
    2026-08-18 гейт вывода поймал `**ether.fi cash review** — ...`: на сайте читатель видел
    звёздочки как знаки препинания. Правка одного JSON вернулась бы при следующей синхронизации,
    поэтому чистка стоит здесь, на входе, а не в данных.

    Чистится ТОЛЬКО выделение (** и __ парами) — текст заголовка не переписывается: наша
    задача не редактировать чужой заголовок, а не пустить в вёрстку синтаксис, который вёрстка
    не разбирает.
    """
    out = re.sub(r"\*\*(.+?)\*\*", r"\1", title or "")
    out = re.sub(r"__(.+?)__", r"\1", out)
    return out.strip()


def _build_entry(row: dict, topic: dict, script: dict | None, summary: dict | None,
                 yt_meta: dict | None = None) -> dict:
    video_id = row["external_id"]
    # Summary carries the runtime-enriched topic (class + pool added by
    # fetch_topic.py) — prefer it so VIRAL classification flows through.
    if summary and isinstance(summary.get("topic"), dict):
        topic = {**topic, **summary["topic"]}
    # Title priority: LIVE YT snippet (reflects any retitle since publish)
    # → summary.json (what was sent to YT at publish time, may be stale)
    # → script's thumbnail_hook → topic title.
    yt_title = (yt_meta or {}).get("snippet", {}).get("title") if yt_meta else None
    raw_title = (
        _strip_markdown_emphasis(yt_title) if yt_title else yt_title
        or (summary or {}).get("title")
        or (script or {}).get("thumbnail_hook")
        or topic.get("title")
        or topic.get("primary")
        or "Untitled video"
    )
    title = raw_title.strip()[:100]
    body = (script or {}).get("body") or (script or {}).get("voiceover") or ""
    # First sentence of body = description (1-2 sentences). Cap at 2048 (sitemap limit).
    desc = re.split(r"(?<=[.!?])\s+", body.strip(), maxsplit=2)
    description = " ".join(desc[:2])[:2048] if desc else title
    # Hashtags — youtube_visible without #, deduped, max 25
    yt_hashtags = ((script or {}).get("hashtags") or {}).get("youtube_visible") or []
    tags = []
    seen = set()
    for h in yt_hashtags:
        clean = h.lstrip("#").strip()
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            tags.append(clean)
        if len(tags) >= 25:
            break
    return {
        "id": video_id,
        "platform": row["channel"],
        "title": title,
        "description": description,
        "body": body,
        "topic_id": int(row["topic_id"]),
        "topic_primary": topic.get("primary") or "",
        "topic_category": topic.get("category") or topic.get("class") or "general",
        "topic_class": (topic.get("class") or ("VIRAL" if topic.get("pool") == "viral" else "TOFU")).upper(),
        "duration_s": (
            (summary or {}).get("topic", {}).get("target_seconds")
            or (summary or {}).get("voice", {}).get("duration_s")
            or (script or {}).get("voice", {}).get("duration_s")
        ),
        "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "youtube_url": f"https://youtu.be/{video_id}",
        "embed_url": f"https://www.youtube-nocookie.com/embed/{video_id}",
        "published_at": row["ts"],
        "target_pseo_slug": _match_pseo_slug(topic, script or {}),
        "hashtags": tags,
    }


def main() -> int:
    if not DB.exists():
        print(f"[sync-yt-videos] no published.db at {DB} — nothing to sync")
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        return 0

    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

    topics = _topic_index()
    db = sqlite3.connect(str(DB))
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT * FROM publish_log WHERE status='ok' AND external_id IS NOT NULL "
        "ORDER BY ts ASC"
    ).fetchall()

    # Verify each row is still PUBLIC + ALIVE on YT before publishing to the
    # site. Stale rows (operator deleted the video, YT struck it, made it
    # private) must be evicted from the content collection so they vanish
    # from /videos/, sitemap-videos.xml, and pSEO article embeds.
    yt_meta = _yt_availability([r["external_id"] for r in rows])
    # Defence-in-depth: if the API call returned NOTHING (creds missing,
    # google libs missing, network outage), DON'T silently treat every
    # video as deleted — that'd nuke the entire videos collection. Skip
    # the availability filter and trust the DB rows. The verification
    # warning is already printed inside _yt_availability().
    api_check_active = bool(yt_meta)
    skipped_unavailable = 0
    pruned_orphans = 0
    eligible_ids: set[str] = set()

    written = 0
    skipped_missing_topic = 0
    skipped_missing_script = 0
    for r in rows:
        if api_check_active:
            eligible, reason = _is_publishable(yt_meta.get(r["external_id"]))
            if not eligible:
                stale = VIDEOS_DIR / f"{r['external_id']}.json"
                if stale.exists():
                    stale.unlink()
                    pruned_orphans += 1
                skipped_unavailable += 1
                print(f"[sync-yt-videos] skip {r['external_id']}: {reason}")
                continue
        # If api_check is inactive (no creds), trust the DB row exists =
        # was published successfully and treat as eligible.
        eligible_ids.add(r["external_id"])
        row = dict(r)
        topic = topics.get(int(row["topic_id"]))
        if not topic:
            # Topic was removed from the pool (e.g. operator narrowed the
            # viral pool for niche cohesion per Y44). Video stays alive on
            # YT and we keep it in the /videos/ gallery + sitemap — the
            # operator chose to keep the video. But we BLOCK any pSEO
            # embed by ensuring `target_pseo_slug=null` in the JSON, so an
            # off-niche video can't be embedded on a crypto-card pSEO
            # article (would dilute topical-cluster signal).
            stale = VIDEOS_DIR / f"{row['external_id']}.json"
            if stale.exists():
                try:
                    d = json.loads(stale.read_text(encoding="utf-8"))
                    if d.get("target_pseo_slug"):
                        d["target_pseo_slug"] = None
                        stale.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
                        print(f"[sync-yt-videos] off-pool {row['external_id']}: cleared target_pseo_slug (topic_id={row['topic_id']} not in pool per Y44)")
                except (OSError, json.JSONDecodeError):
                    pass
            else:
                # No existing JSON — bootstrap a minimal one from YT snippet
                # so the video appears in gallery + sitemap.
                ymeta = yt_meta.get(row["external_id"]) or {}
                sn = ymeta.get("snippet", {}) or {}
                cd = ymeta.get("contentDetails", {}) or {}
                vid = row["external_id"]
                bootstrap = {
                    "id": vid,
                    "platform": row["channel"],
                    "title": (sn.get("title") or "Untitled video")[:100],
                    "description": (sn.get("description", "") or sn.get("title", ""))[:500],
                    "body": "",
                    "topic_id": int(row["topic_id"]),
                    "topic_primary": "off-pool",
                    "topic_category": "archive",
                    "topic_class": "VIRAL",
                    "duration_s": None,
                    "thumbnail_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                    "youtube_url": f"https://youtu.be/{vid}",
                    "embed_url": f"https://www.youtube-nocookie.com/embed/{vid}",
                    "published_at": row["ts"],
                    "target_pseo_slug": None,
                    "hashtags": [],
                }
                stale.write_text(json.dumps(bootstrap, indent=2, ensure_ascii=False), encoding="utf-8")
                print(f"[sync-yt-videos] off-pool {row['external_id']}: bootstrapped minimal JSON (topic_id={row['topic_id']} not in pool, target_pseo_slug=null per Y44)")
            skipped_missing_topic += 1
            continue
        render_dir = _find_render_dir(row["channel"], row["ts"], int(row["topic_id"]))
        script: dict = {}
        summary: dict = {}
        if render_dir is None:
            skipped_missing_script += 1
        else:
            for name, target in (("script.json", "script"), ("summary.json", "summary")):
                p = render_dir / name
                if not p.exists():
                    continue
                try:
                    val = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as e:
                    print(f"[sync-yt-videos] warn {row['external_id']}: bad {name} ({e})")
                    continue
                if target == "script":
                    script = val
                else:
                    summary = val

        # Pass the runtime-enriched topic (from summary) to the matcher so
        # VIRAL/pool flags actually take effect.
        if summary and isinstance(summary.get("topic"), dict):
            topic = {**topic, **summary["topic"]}
        entry = _build_entry(row, topic, script, summary, yt_meta.get(row["external_id"]))
        out_path = VIDEOS_DIR / f"{entry['id']}.json"
        out_path.write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8")
        written += 1
        link_target = entry.get("target_pseo_slug") or "(none)"
        print(f"[sync-yt-videos] wrote {entry['id']}: '{entry['title'][:50]}' → pseo:{link_target}")

    # Final orphan sweep — any JSON whose video_id is NOT in our publish_log
    # at all (e.g. moved DB, hand-written entry) gets pruned too. Belt + braces.
    db_ids = {r["external_id"] for r in rows}
    for p in VIDEOS_DIR.glob("*.json"):
        vid = p.stem
        if vid not in db_ids and vid not in eligible_ids:
            p.unlink()
            pruned_orphans += 1
            print(f"[sync-yt-videos] prune orphan json: {p.name}")

    print(
        f"[sync-yt-videos] done: {written} written, "
        f"{skipped_unavailable} skipped (deleted/non-public on YT), "
        f"{pruned_orphans} orphan JSONs pruned, "
        f"{skipped_missing_topic} skipped (no topic), "
        f"{skipped_missing_script} fell back to topic-only metadata "
        f"({datetime.now(timezone.utc).isoformat()})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
