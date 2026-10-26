"""Pipeline orchestrator — 7 steps in CLAUDE.md-mandated order.

  fetch_topic → write_script → tts_edge → fetch_visuals → fetch_music → render → publish

CLI:
  python -m pipeline.main --platform=youtube|instagram|tiktok [--dry-run]
                          [--check-slot] [--topic-id=N] [--seed=K]

--dry-run: no external API calls; uses placeholders/mocks; produces a valid mp4 anyway
--check-slot: only proceed if a due slot is found in state/schedule.json (cron use)
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

from . import (_config, captions, compliance, dedup, fetch_music, fetch_topic,
               fetch_visuals, hashtag_strategy, render, schedule,
               telegram_preview, tts_edge, tts_eleven, write_script)
from .publish import youtube as pub_yt
from .publish import instagram as pub_ig
from .publish import tiktok as pub_tt


def _out_dir(platform: str, topic_id: int) -> Path:
    root = _config.root()
    stamp = date.today().isoformat()
    return root / "output" / platform / stamp / f"topic_{topic_id:02d}"


def _today_seed() -> str:
    return date.today().isoformat()


def video_slug(platform: str, topic_id: int, day: str | None = None) -> str:
    """Stable per-video id used as `utm_content`.

    NOT the YouTube video id: the description is built BEFORE upload, so the
    platform id does not exist yet, and editing the description afterwards
    would be a write to YouTube. This id is ours, deterministic, and enough to
    answer "which clip produced this session" in GA4.
    """
    return f"{platform[:2]}-{(day or date.today().isoformat()).replace('-', '')}-{topic_id:02d}"


def tracked_url(platform: str, topic_id: int, path: str = "/", day: str | None = None,
                medium: str | None = None) -> str:
    """defycard.com URL carrying the campaign markup for one video.

    Y50 P0-3: until now every video pointed at a bare https://defycard.com/,
    so all 213 publications were indistinguishable from direct traffic and the
    channel's contribution was unmeasurable — which is why it read as zero.
    `utm_content` lands in GA4's STANDARD `sessionManualAdContent` dimension
    (verified live 2026-08-11); `customEvent:utm_content` returns 400 because
    it is an event-scoped custom dimension nobody registered. Do not use it.

    `medium` overrides the format label — the long-form pilot (Y52) passes
    "longform" so its sessions are separable from Shorts in GA4.
    """
    medium = medium or ("shorts" if platform in ("youtube", "tiktok") else "reels")
    return (f"https://defycard.com{path}?utm_source={platform}"
            f"&utm_medium={medium}&utm_content={video_slug(platform, topic_id, day)}")


def topic_path(topic: dict[str, Any]) -> str:
    """Where a video about this topic should send the viewer.

    A pSEO topic points at its own page, not the homepage. Y50 made the partner
    a property of the PAGE (`primary_partner` + config/availability.yaml), so
    for "best crypto card india" the page routes to a card that can actually be
    issued there while the homepage still shows ether.fi. Sending that viewer
    home would re-open the money bug Y50 closed, one channel over. Same rule as
    longform.page_url (Y52) — stated once, here, for both formats.
    """
    slug, bucket = topic.get("target_pseo_slug"), topic.get("bucket")
    return f"/{bucket}/{slug}/" if slug and bucket else "/"


def cta_text(path: str) -> str:
    """The URL as a HUMAN reads it off the CTA card — no scheme, no UTM.

    Nobody retypes a tracking string from a screen, and every character spent
    on one shrinks the font. The QR beside it carries the markup instead.
    """
    return ("defycard.com" + path).rstrip("/")


def qr_url(platform: str, path: str) -> str:
    """The URL a SCANNER gets: full scheme, source and medium, nothing else.

    `utm_content` (the per-video slug) is deliberately dropped — it is ~28
    characters that push the QR two versions denser, and the page path already
    says which topic the scan came from. `utm_medium=qr` is what makes this
    route separable from `shorts` in GA4, which is the whole point.

    KILL CRITERION, pre-registered 2026-08-11 so it cannot be reinterpreted
    afterwards: 30 days after the first published video carrying a QR, compare
    human sessions (R2 definition: non-direct + engaged-direct) with
    `utm_medium=qr` against `utm_medium=shorts`. If qr < 5% of shorts, the
    overlay is REMOVED — a viewer who must fetch a second phone and aim it at a
    vertical feed for 6 seconds is a hypothesis, not a channel, and Y52 already
    shipped long-form as the format where the link is simply clickable.
    """
    return f"https://defycard.com{path}?utm_source={platform}&utm_medium=qr"


def _build_caption(platform: str, script: dict[str, Any], hashtags: Any,
                   topic_id: int = 0, path: str = "/") -> str:
    if platform == "youtube":
        # Channel VERIFIED 2026-06-18 → website link goes DIRECTLY in the description (no more
        # pinned-comment workaround). SHORT description + MAX 3 hashtags (operator rule 2026-06-18).
        hook = script.get("thumbnail_hook", "").replace(" | ", " — ").strip()
        visible = " ".join(hashtags["visible"][:3])  # MAX 3 hashtags in the description
        return (
            f"{hook}\n\n"
            f"👉 Get the card: {tracked_url('youtube', topic_id, path)}\n\n"
            f"{visible}\n\n"
            f"Affiliate link — we may earn commission. Not financial advice."
        )
    elif platform == "instagram":
        body_lead = ". ".join(script.get("body", "").split(". ")[:2])
        tags = " ".join(hashtags)
        return (
            f"👉 Get the card: defycard.com\n"
            f"🔍 Compare 11 crypto cards: defycard.com/compare/\n\n"
            f"{body_lead}\n\n"
            f"⚡ {script.get('cta', '')}\n\n"
            f"Affiliate links — we may earn commission. Not financial advice.\n\n"
            f".\n.\n"
            f"{tags}"
        )
    else:  # tiktok
        body_lead = script.get("body", "").split(". ")[0]
        tags = " ".join(hashtags)
        return (
            f"👉 Get the card: dfy.cc/card\n"
            f"🔍 Compare 11 cards: dfy.cc/compare\n\n"
            f"{body_lead}\n"
            f"{script.get('cta', '')}\n\n"
            f"Affiliate links — we may earn commission. Not financial advice.\n\n"
            f"{tags}"
        )


_PINNED_DEFAULT_PATH = "/hub/best-crypto-card-2026/"


def _build_pinned_yt(script: dict[str, Any], topic_id: int = 0,
                     path: str = "/") -> str:
    """Pinned comment: EXACTLY ONE url.

    Y50 P0-3: the previous three-link block reads as comment spam (it sat on
    200 of the channel's 203 comments), splits whatever click intent exists
    across three destinations, and none of the three carried campaign markup.
    One tracked link.

    Y53: when the video is about a specific page, that page is the one
    destination — the same one the description names, so the two do not split
    the click intent Y50 just finished consolidating. A topic with no page of
    its own keeps the comparison hub, which is Y50's behaviour unchanged.
    """
    if path == "/":
        path = _PINNED_DEFAULT_PATH
    return (
        f"👉 Get the card: {tracked_url('youtube', topic_id, path)}\n\n"
        "Affiliate link — we may earn commission. Not financial advice."
    )


# Was 60 — too tight once both halves of the hook are joined (Y35).
_TITLE_MAX_CHARS = 90


def _build_title(script: dict[str, Any], topic: dict[str, Any]) -> str:
    """YT title: emoji prefix per topic class + full two-line hook joined.

    `thumbnail_hook` has shape "LINE_1 | LINE_2" (≤30 chars per line) for
    a two-line custom thumbnail render. The YT TITLE wants the full hook
    sentence — taking only LINE_1 (the previous behavior) drops the payoff
    and reads as a truncated teaser ("HOW MONEY", "MOVIE SECRET", …) with
    catastrophic CTR. Rejoin halves with em-dash for a complete hook.
    Documented incident 2026-05-08; see youtube1/SKILL.md Y35.

    Y53 P1-1 — a topic that came from the pSEO catalog is titled with the search
    query FIRST, because that is the whole point of taking it from there:
    YouTube weighs leading tokens when matching a search, and the query is what
    the page already ranks for. The hook follows as the payoff, and Y35 applies
    in its general form — a hook is appended whole or not at all. When the
    query plus the hook will not fit, the hook is dropped entirely rather than
    cut; a half hook is precisely the incident. No emoji prefix here either: it
    would spend the most valuable position in the string on decoration.
    """
    hook = (script.get("thumbnail_hook") or topic.get("title") or "").strip()
    if not hook:
        hook = topic.get("title", "Untitled")
    if " | " in hook:
        hook = hook.replace(" | ", " — ", 1)

    if topic.get("pool") == "pseo" and topic.get("primary"):
        query = str(topic["primary"]).strip()
        joined = f"{query} — {hook}"
        return joined if hook and len(joined) <= _TITLE_MAX_CHARS else query[:_TITLE_MAX_CHARS]

    cls = topic.get("class", "MOFU")
    prefix_map = {"TOFU": "📈", "MOFU": "🔍", "BOFU": "💳"}
    head = hook[:4]
    if not any(c.isdigit() for c in head) and not _is_emoji(head[0]):
        hook = f"{prefix_map.get(cls, '💳')} {hook}"
    return hook[:_TITLE_MAX_CHARS]


def _is_emoji(c: str) -> bool:
    return ord(c) > 0x2300  # crude — covers most emoji ranges


def run_pipeline(platform: str, *, dry_run: bool = False,
                 topic_id: int | None = None, seed: int | None = None) -> dict[str, Any]:
    cfg = _config.cfg()

    # 1. fetch_topic
    if topic_id is not None:
        from json import loads
        # Search both pools — finance (templates/topic_pool.json) + viral (shorts-analysis/topic_pool_viral.json)
        finance_pool = loads(_config.root().joinpath("templates/topic_pool.json").read_text())["topics"]
        viral_path = _config.root().parent / ".claude" / "skills" / "shorts-analysis" / "topic_pool_viral.json"
        viral_pool = (loads(viral_path.read_text())["topics"] if viral_path.exists() else [])
        t = next((x for x in finance_pool if x["id"] == topic_id), None)
        if t is not None:
            topic = fetch_topic._enrich_finance(t, platform)  # type: ignore[attr-defined]
        else:
            t = next((x for x in viral_pool if x["id"] == topic_id), None)
            if t is None:
                raise SystemExit(f"topic_id={topic_id} not found in finance or viral pool")
            topic = fetch_topic._enrich_viral(t, platform)  # type: ignore[attr-defined]
    else:
        topic = fetch_topic.pick(platform, seed=seed)
    print(f"[main] platform={platform} topic_id={topic['id']} class={topic['class']} target_sec={topic['target_seconds']}")

    out_dir = _out_dir(platform, topic["id"])
    out_dir.mkdir(parents=True, exist_ok=True)

    # Обёртка стоит ЗДЕСЬ, а не у обработчика алерта в main(): там тема уже неизвестна — исключение
    # приходит из глубины и `topic_id` в него не входит. Ловим ЛЮБОЙ отказ ниже по конвейеру, а не
    # только визуальный: Fable прямо назвал TTS, compliance и рендер — селектор обязан узнавать о
    # провале по любой причине, иначе следующий затор просто придёт с другой стороны.
    try:
        return _run_after_topic(platform, topic, out_dir, dry_run=dry_run)
    except Exception as exc:                                    # noqa: BLE001
        try:
            # T-0140: a NoVisualsError whose cause is a page property (the query
            # is fixed by the page's own frontmatter, so a retry in 36h repeats
            # the same paid TTS synth for the same zero) cools for 14 days
            # instead of 36h — see dedup.record_topic_failure/_PAGE_PROPERTY_TTL_HOURS.
            page_property = getattr(exc, "cause", None) in fetch_visuals.PAGE_PROPERTY_CAUSES
            dedup.record_topic_failure(topic["id"], platform,
                                       f"{type(exc).__name__}: {exc}",
                                       page_property=page_property)
            print(f"[main] тема {topic['id']} помечена упавшей — "
                  f"следующий слот её пропустит ({_FAILURE_NOTE})")
        except Exception as inner:                              # noqa: BLE001
            # Отказ учёта не имеет права проглотить настоящую причину.
            print(f"[main] ВНИМАНИЕ: провал не зарегистрирован: {type(inner).__name__}: {inner}")
        raise


_FAILURE_NOTE = "кулдаун провала 36 ч, счётчик копится"


def _run_after_topic(platform: str, topic: dict[str, Any], out_dir: Path, *,
                     dry_run: bool = False) -> dict[str, Any]:

    # 2. write_script
    script = write_script.write(topic, dry_run=dry_run)
    (out_dir / "script.json").write_text(json.dumps(script, indent=2), encoding="utf-8")

    # 2b. G35 fact-gate — VG_MODE=h1 only (D7's step order: script -> gate ->
    # voice). Other pools are not written from facts.yaml, so nothing to check.
    if topic.get("pool") == "h1":
        write_script.check_facts_gate(script)

    # 3. tts_edge / tts_eleven — TTS_PROVIDER selects the backend (E-19)
    voice_path = out_dir / "voice.mp3"
    _tts_mod = tts_eleven if _config.tts_provider() == "elevenlabs" else tts_edge
    voice_meta = _tts_mod.synthesize(
        script["voiceover"], platform, voice_path, dry_run=dry_run)

    # 3b. G39 (T-0142-02): render() silently clamps to max_seconds (Y49:
    # duration = min(max_seconds, tts+0.5)) — a script whose narration already
    # overruns the Shorts cap must refuse HERE, before fetch_visuals/
    # fetch_music/render spend real cost on a video that will just get cut
    # down to a different length than the one it was written for. Third gate
    # on the same law: pattern_selector.select() filters the creative tuple by
    # the topic's duration_band, write_script.max_words ceilings the script —
    # this one measures the real TTS artifact ffprobe already trusts render()
    # to measure, so no gate here is fooled by a formula that agrees with
    # itself.
    tts_seconds = render.probe_duration(voice_path)
    if tts_seconds + 0.5 > render.SHORTS_MAX_SECONDS:
        raise RuntimeError(
            f"topic {topic['id']}: narration is {tts_seconds:.1f}s — tts+0.5s "
            f"exceeds the {render.SHORTS_MAX_SECONDS:.0f}s Shorts cap. render() "
            f"would silently truncate it; refusing instead of publishing a "
            f"video shorter than its own script."
        )

    # 4. fetch_visuals
    media = fetch_visuals.fetch(
        topic.get("primary", topic["title"]),
        want=4, out_dir=out_dir / "media", dry_run=dry_run,
        platform=platform, topic_id=topic.get("id"),
    )

    # 5. fetch_music
    music_path = out_dir / "music.mp3"
    music_meta = fetch_music.fetch(
        music_path, target_seconds=int(topic["target_seconds"]),
        dry_run=dry_run, day_seed=_today_seed())

    # 6. captions + render
    words = captions.align(voice_path, script["voiceover"])
    ftc_overlay = _config.root() / "assets" / "disclaimers" / "ftc_overlay.png"
    final_path = out_dir / "final.mp4"
    # Skip the burned-in captions ONLY where step 6b below actually puts them
    # back. The condition is stated here, next to the compensation, instead of
    # inside render.py — where it silently applied to every caller, including
    # ones with no step 6b at all (Y52).
    _html_overlay = (os.environ.get("DEFY_NEW_DESIGN") == "1"
                     and platform == "youtube")
    # One destination per video, computed ONCE and read by all four producers
    # of a link: the on-screen text, the QR beside it, the description and the
    # pinned comment. Y53 fixed the last two; the CTA card still burned the
    # bare homepage into the frame of a video about India (Y54).
    link_path = topic_path(topic)
    _render_kw = dict(
        burn_captions=not _html_overlay,
        cta_url=cta_text(link_path),
        qr_payload=qr_url(platform, link_path),
        voiceover_path=voice_path,
        music_path=music_path,
        clips=media,
        words=words,
        ftc_overlay_path=ftc_overlay if ftc_overlay.exists() else None,
        target_seconds=topic["target_seconds"],
        out_path=final_path,
        platform=platform,
    )
    render.render(**_render_kw)

    # G39 (T-0142-02): law measured on the real rendered artifact via ffprobe,
    # not the formula render() itself computes from — a formula-only check
    # cannot catch a bug that lives exactly where the formula lives (Y49).
    _final_duration = render.probe_duration(final_path)
    _expected_duration = min(render.SHORTS_MAX_SECONDS, tts_seconds + 0.5)
    if abs(_final_duration - _expected_duration) > 0.1:
        raise RuntimeError(
            f"topic {topic['id']}: rendered {_final_duration:.2f}s, expected "
            f"{_expected_duration:.2f}s (min({render.SHORTS_MAX_SECONDS:.0f}, "
            f"tts+0.5)) — Y49 duration invariant broken"
        )

    # 6b. DefyCard NEW DESIGN — HTML caption-overlay (50 rotating styles) over the
    #     captionless render + a rotating custom preview thumbnail. Best-effort.
    defy_thumb = None
    import os as _os, subprocess as _sp, json as _json
    if _html_overlay:  # same flag that suppressed the burned captions above
        try:
            _dd = _config.root() / "defy_design"
            _wj = out_dir / "_words.json"
            _wj.write_text(_json.dumps(words))
            _ovl = _sp.run(["node", str(_dd / "render_overlay.mjs"), "--words", str(_wj),
                     "--style", "auto", "--fps", "30", "--outdir", str(out_dir / "_ovl"),
                     "--captions-only", "--video", str(final_path), "--out", str(final_path)],
                    timeout=480, check=False)
            # Y58: the compensation may fail, but then its PRECONDITION fails too.
            # `burn_captions=False` above is only safe because this overlay puts
            # them back; when the overlay does not land, the video must be rebuilt
            # WITH burned captions rather than published bare. This exact step was
            # returning non-zero on every run (missing chromium after a Playwright
            # bump) while `check=False` let two captionless videos reach YouTube.
            if _ovl.returncode != 0:
                print(f"[main] caption overlay failed (rc={_ovl.returncode}) — "
                      f"re-rendering with burned captions")
                render.render(**{**_render_kw, "burn_captions": True})
            _thumb = out_dir / "thumbnail.png"
            _hook = (script.get("thumbnail_hook") or script.get("hook") or "SPEND CRYPTO ANYWHERE").replace(" | ", " ").strip()
            _sp.run(["node", str(_dd / "render_preview.mjs"), "--style", "auto",
                     "--hook", _hook, "--out", str(_thumb)], timeout=120, check=False)
            if _thumb.exists():
                defy_thumb = _thumb
        except Exception as _e:
            print(f"[main] DefyCard new-design overlay/preview failed (non-fatal): {_e}")

    # Post-generation compliance
    transcript = " ".join(w.get("word", "") for w in words)
    caption_text = " ".join(w.get("word", "") for w in words)
    compliance.scan_final(transcript, caption_text, platform=platform)

    # 7. publish (or TG preview in dry-run)
    hashtags = (hashtag_strategy.for_youtube(topic["id"], _today_seed())
                if platform == "youtube" else
                hashtag_strategy.for_instagram(topic["id"], _today_seed())
                if platform == "instagram" else
                hashtag_strategy.for_tiktok(topic["id"], _today_seed()))
    caption = _build_caption(platform, script, hashtags, topic["id"], link_path)
    title = _build_title(script, topic)

    # Pre-publish: emit SRT caption track from word-timestamps so YT can
    # index spoken content + auto-translate to other languages (Y43).
    captions_srt_path = out_dir / "captions.en.srt"
    try:
        from .captions_srt import emit_srt_from_words
        emit_srt_from_words(words, captions_srt_path)
    except Exception as e:
        print(f"[main] caption SRT generation failed (non-fatal): {e}")
        captions_srt_path = None  # type: ignore[assignment]

    publish_result: dict[str, Any]
    if platform == "youtube":
        publish_result = pub_yt.upload(
            video_path=final_path, title=title, description=caption,
            tags=hashtags["api"], privacy="public",
            pinned_comment=_build_pinned_yt(script, topic["id"], link_path),
            captions_srt_path=captions_srt_path,
            thumbnail_path=defy_thumb,
            dry_run=dry_run,
        )
    elif platform == "instagram":
        # In dry-run, public_url is mock; in prod, pipeline uploads to bucket first
        public_url = f"https://placeholder/{final_path.name}"
        publish_result = pub_ig.upload(
            public_url=public_url, title=title, caption=caption, dry_run=dry_run)
    else:
        public_url = f"https://placeholder/{final_path.name}"
        publish_result = pub_tt.upload(
            public_url=public_url, title=title, caption=caption, dry_run=dry_run)

    # Telegram notification — post-publish, NOT a gate. Operator sees the
    # rendered video + the live URL after it's already on the platform.
    # Include the YouTube URL when available so the operator can click through.
    publish_url = publish_result.get("url") if isinstance(publish_result, dict) else None
    tg_caption = f"{title}\n\n"
    if publish_url:
        tg_caption += f"🔗 {publish_url}\n\n"
    tg_caption += caption[:480]
    telegram_preview.send(final_path, caption=tg_caption, dry_run=dry_run)

    if not dry_run:
        fetch_topic.mark_used(topic["id"], platform)
        dedup.record_title_prefix(title, platform)
        external_id = publish_result.get("video_id") or str(publish_result)
        dedup.log_publish(
            channel=platform, topic_id=topic["id"],
            transport="direct" if platform == "youtube" else "dlvrit",
            external_id=external_id,
            status="ok" if "error" not in str(publish_result).lower() else "partial",
            notes="h1" if topic.get("pool") == "h1" else "",
        )

        # Pattern-selector → publish-count + per-video pattern attribution
        # for the auto-learning feedback loop (analytics.py reads this).
        try:
            from . import pattern_selector, analytics as analytics_mod
            patterns_used = script.get("_creative_patterns") or {}
            if patterns_used:
                pattern_selector.record_publish(platform)
                analytics_mod.append_video_log(
                    video_id=external_id,
                    platform=platform,
                    topic_id=topic["id"],
                    topic_class=topic.get("class", "MOFU"),
                    patterns_used=patterns_used,
                    transport="direct" if platform == "youtube" else "dlvrit",
                )
        except Exception as e:
            # Non-fatal — publish already succeeded. Log + alert.
            telegram_preview.alert(
                f"[main] pattern_log_failed for {external_id}: {e}",
                dry_run=False,
            )

    summary = {
        "platform": platform, "topic": topic, "script_chars": len(script.get("voiceover", "")),
        "voice": voice_meta, "music": music_meta,
        "media_count": len(media), "final_path": str(final_path),
        "title": title, "publish": publish_result,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=["youtube", "instagram", "tiktok"], required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check-slot", action="store_true",
                    help="Only run if a due slot exists in state/schedule.json")
    ap.add_argument("--topic-id", type=int, default=None)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    if args.check_slot:
        slot = schedule.due_slot(args.platform)
        if slot is None:
            print(f"[main] no due slot for {args.platform} — skip")
            return 0
        print(f"[main] firing slot {slot['slot_id']}")
        try:
            run_pipeline(args.platform, dry_run=args.dry_run, seed=args.seed)
            schedule.mark_fired(slot["slot_id"])
        except Exception as e:
            reason = f"{type(e).__name__}: {str(e)[:200]}"
            # T-0144: one alert per (slot, cause) — the slot retries every tick
            # by design (T-0140 Q2), the operator must not be paged every tick.
            # ponytail: mutes consecutive identical causes only; A/B/A alternation still alerts.
            if schedule.mark_failed(slot["slot_id"], reason):
                telegram_preview.alert(
                    f"[publish-failure] {args.platform}: {e}\n"
                    f"slot {slot['slot_id']} retries every tick; identical repeats muted",
                    dry_run=args.dry_run)
            else:
                print(f"[main] {slot['slot_id']}: same failure as last tick — alert muted")
            raise
        return 0

    summary = run_pipeline(args.platform, dry_run=args.dry_run,
                           topic_id=args.topic_id, seed=args.seed)
    print(json.dumps({
        "platform": summary["platform"],
        "topic_id": summary["topic"]["id"],
        "title": summary["title"],
        "final_path": summary["final_path"],
        "publish": summary["publish"],
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
