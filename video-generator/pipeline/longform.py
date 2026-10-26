"""Y52 — long-form pilot: >3 min videos built from proven pSEO pages.

WHY THIS FORMAT EXISTS
A Shorts description renders its URL as a plain text node with no
`navigationEndpoint` — the viewer cannot click it. That is a platform
property, not a bug of ours, and it is why 213 Shorts produced zero measurable
sessions. Above three minutes YouTube serves a normal watch page and the
description link works. This is the only format on the channel where a viewer
can actually reach defycard.com.

WHY THE SCRIPT IS ASSEMBLED, NOT WRITTEN
The five pilot pages already carry 767–909 words of verified, human-reviewed
material in their frontmatter (intro, snapshot, long context, what-to-watch,
6 FAQ, bottom line). Sending that to a model to have it rewritten would add
hallucination risk on YMYL facts and cost tokens to produce something we
already have. So the narration is composed deterministically from the page.
The model comes back (Haiku, variant A) only if the pilot proves the format
and we need to scale past the pages that already exist.

HARD RULES (both are locks on the same failure — a longform silently becoming
a Short with an unclickable link):
  - a short script is NEVER accepted; the run fails loudly
  - the rendered mp4 is measured with ffprobe and must land in
    [185, 260] s — 185 keeps five seconds of clearance from the 180 s
    Shorts boundary

CLI:
  python -m pipeline.longform --slug=is-redotpay-legit [--dry-run]
  python -m pipeline.longform --all [--dry-run]
"""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import (_config, captions, compliance, fetch_music, fetch_visuals,
               hashtag_strategy, main as pipeline_main, render, tts_edge)
from . import dedup
from .publish import youtube as pub_yt

# The pilot set: pages that GA4 shows converting, per CCH_R2_PLAN §P2-1.
# Title of the video = the search query the page already ranks for.
PILOT_SLUGS: tuple[str, ...] = (
    "is-redotpay-legit",
    "who-can-get-ether-fi-cash",
    "best-crypto-card-turkey",
    "best-crypto-card-india",
    "crypto-cashback-vs-traditional-cashback",
)

# Duration band and gate. The band is what we aim for; the gate is what the
# artifact must satisfy. 180 s is the platform's Shorts boundary exactly —
# rendering at 180.5 s would be one rounding away from shipping an unclickable
# link, so the floor sits 5 s above it.
TARGET_BAND_SECONDS = (200.0, 250.0)
GATE_MIN_SECONDS = 185.0
GATE_MAX_SECONDS = 260.0

# Narration budget and the rate it is derived from.
#
# The Shorts figure (1.96 words/s, Y49) does NOT apply here: it was measured on
# punchy one-idea-per-sentence copy, where inter-sentence pauses are a large
# share of the runtime. Continuous article prose runs faster — measured on this
# voice, over all five pilot pages, 2.04–2.36 words/s (mean 2.16). At 470 words
# the five narrations measure 197.1, 219.6, 224.4, 228.5 and 232.4 s.
#
# The voice is PINNED rather than rotated: rotation spans 15 voices whose rates
# differ by ~11 %, which is most of the width of the gate below. Five pilot
# videos do not need voice variety; they need a predictable length.
NARRATION_WORDS_PER_SECOND = 2.16
TARGET_WORDS = 470
PILOT_VOICE = "en-US-GuyNeural"

# A page thinner than this is excluded from the pilot rather than padded out:
# padding is exactly how the 38 s floor of Y49 produced voiceless b-roll.
MIN_FRONTMATTER_WORDS = 400

# YouTube requires the first chapter at 0:00, at least three chapters, and no
# chapter shorter than 10 s — otherwise it renders none of them.
MIN_CHAPTERS = 3
MIN_CHAPTER_GAP_SECONDS = 10.0

# id space: finance topics are 1–35, viral 110–225, news 1_000_000+.
LONGFORM_ID_BASE = 90

_PSEO_DIR = _config.root().parent / "seo-site" / "src" / "content" / "pseo"

# Section order and the share of the word budget each one gets. The shares are
# what keeps a 909-word page and a 767-word page landing in the same duration
# band without either being padded or cut mid-sentence.
_SECTION_WEIGHTS: tuple[tuple[str, str, float], ...] = (
    ("Quick answer", "intro_paragraph", 0.18),
    ("At a glance", "snapshot_bullets", 0.10),
    ("The full picture", "long_context_paragraph", 0.24),
    ("What to watch", "what_to_watch", 0.12),
    ("Questions people ask", "faq", 0.24),
    ("The bottom line", "bottom_line", 0.12),
)

_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _speakable(text: Any) -> str:
    """Strip Markdown the TTS would read out loud (link targets, bold stars)."""
    t = _LINK_RE.sub(r"\1", str(text))
    t = t.replace("**", "").replace("*", "")
    return re.sub(r"\s+", " ", t).strip()


def page(slug: str) -> dict[str, Any]:
    """Frontmatter of one pSEO page. Raises if the page is missing."""
    path = _PSEO_DIR / f"{slug}.md"
    if not path.is_file():
        raise FileNotFoundError(f"pSEO page not found: {path}")
    parts = path.read_text(encoding="utf-8").split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"{path} has no frontmatter block")
    fm = yaml.safe_load(parts[1])
    if not isinstance(fm, dict):
        raise ValueError(f"{path} frontmatter is not a mapping")
    return fm


def _units(fm: dict[str, Any], field: str) -> list[str]:
    """Speakable units of one frontmatter field — bullets, FAQ pairs, or the
    sentences of a paragraph. Trimming happens on unit boundaries only."""
    raw = fm.get(field)
    if raw is None:
        return []
    if field == "faq":
        return [f"{_speakable(f.get('q'))} {_speakable(f.get('a'))}".strip()
                for f in raw if isinstance(f, dict)]
    if isinstance(raw, list):
        return [_speakable(b) for b in raw if _speakable(b)]
    return [s for s in (_speakable(x) for x in _SENTENCE_RE.split(str(raw))) if s]


def frontmatter_words(fm: dict[str, Any]) -> int:
    """Total speakable words available on the page, before any trimming."""
    return sum(len(u.split()) for _lbl, field, _w in _SECTION_WEIGHTS
               for u in _units(fm, field))


def build_sections(fm: dict[str, Any],
                   budget: int = TARGET_WORDS) -> list[dict[str, Any]]:
    """Narration sections, each trimmed to its share of the word budget.

    Every section keeps at least its first unit — a chapter with no content is
    worse than a slightly long video.
    """
    out: list[dict[str, Any]] = []
    for label, field, weight in _SECTION_WEIGHTS:
        units = _units(fm, field)
        if not units:
            continue
        allowance = budget * weight
        taken: list[str] = []
        used = 0
        for u in units:
            n = len(u.split())
            # Closest fit, not "stop before overrunning": stopping short at
            # every one of six sections compounds into a video a full minute
            # under the band.
            if taken and abs(used + n - allowance) > abs(used - allowance):
                break
            taken.append(u)
            used += n
        text = " ".join(taken)
        out.append({"label": label, "text": text, "words": len(text.split())})
    return out


def build_script(fm: dict[str, Any]) -> dict[str, Any]:
    """Deterministic narration for one page. No model call, no padding."""
    available = frontmatter_words(fm)
    if available < MIN_FRONTMATTER_WORDS:
        raise ValueError(
            f"page {fm.get('slug')!r} carries only {available} speakable words "
            f"(< {MIN_FRONTMATTER_WORDS}) — excluded from the long-form pilot "
            f"rather than padded out"
        )
    sections = build_sections(fm)
    cta = ("The full breakdown, with the numbers and the sources, is on "
           "defycard dot com. The link is in the description.")
    sections[-1]["text"] = f"{sections[-1]['text']} {cta}"
    sections[-1]["words"] = len(sections[-1]["text"].split())
    voiceover = " ".join(s["text"] for s in sections)
    script = {
        "hook": _speakable(fm.get("h1") or fm.get("title")),
        "body": " ".join(s["text"] for s in sections[:-1]),
        "cta": cta,
        "voiceover": voiceover,
        "sections": [{"label": s["label"], "words": s["words"]} for s in sections],
        "word_count": len(voiceover.split()),
        "source_slug": fm.get("slug"),
        "primary_keyword": fm.get("primary_keyword"),
    }
    compliance.scan_script(script, platform="youtube")
    return script


def chapters(sections: list[dict[str, Any]], words: list[dict[str, Any]],
             duration: float) -> list[tuple[float, str]]:
    """Chapter start times, from the TTS word timestamps when available.

    Falls back to a proportional split when alignment is missing (dry-run has
    no word boundaries). Chapters closer together than MIN_CHAPTER_GAP_SECONDS
    are dropped — YouTube renders none of them if one is too short.
    """
    total = sum(s["words"] for s in sections) or 1
    out: list[tuple[float, str]] = []
    cursor = 0
    for s in sections:
        if words and cursor < len(words):
            t = float(words[cursor]["start"])
        else:
            t = duration * cursor / total
        if not out:
            t = 0.0
        if out and t - out[-1][0] < MIN_CHAPTER_GAP_SECONDS:
            cursor += s["words"]
            continue
        out.append((t, s["label"]))
        cursor += s["words"]
    return out


def _mmss(t: float) -> str:
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def format_chapters(chs: list[tuple[float, str]]) -> str:
    return "\n".join(f"{_mmss(t)} {label}" for t, label in chs)


NEW_ID_BASE = 100_000
IDS_PATH = _config.root() / "state" / "longform_ids.json"


def _load_ids() -> dict[str, Any]:
    if IDS_PATH.is_file():
        try:
            reg = json.loads(IDS_PATH.read_text(encoding="utf-8"))
            reg.setdefault("ids", {})
            reg.setdefault("next_id", NEW_ID_BASE)
            return reg
        except Exception:  # noqa: BLE001 — a corrupt registry must not mint duplicates
            raise RuntimeError(f"{IDS_PATH} is unreadable — refusing to mint ids blind")
    return {"ids": {}, "next_id": NEW_ID_BASE}


def video_id_for(slug: str) -> int:
    """Stable per-slug id, the single source of truth for `utm_content`.

    Was `LONGFORM_ID_BASE + PILOT_SLUGS.index(slug)`, which raised for any topic
    outside the hardcoded pilot tuple — so the format could never leave its five
    pages. Pilots keep 90..94 so the utm_content of already-rendered files stays
    continuous; everything new is minted from 100000, a free range below the
    viral (110+) and news (1000000+) bands.
    """
    reg = _load_ids()
    known = reg["ids"].get(slug)
    if known is not None:
        return int(known)
    if slug in PILOT_SLUGS:
        vid = LONGFORM_ID_BASE + PILOT_SLUGS.index(slug)
    else:
        vid = int(reg["next_id"])
        reg["next_id"] = vid + 1
    reg["ids"][slug] = vid
    IDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    IDS_PATH.write_text(json.dumps(reg, indent=2, sort_keys=True), encoding="utf-8")
    return vid


def page_url(fm: dict[str, Any], day: str | None = None) -> str:
    """Tracked link to the page the video answers.

    Pointing at the source page rather than the homepage is what keeps the
    partner correct: Y50 made `primary_partner` a property of the page, so a
    Turkey or India viewer lands on Crypto.com and not on a card that cannot
    be issued to them.
    """
    path = f"/{fm['bucket']}/{fm['slug']}/"
    return pipeline_main.tracked_url(
        "youtube", video_id_for(fm["slug"]), path, day=day, medium="longform")


def build_description(fm: dict[str, Any], chs: list[tuple[float, str]],
                      hashtags: list[str], day: str | None = None) -> str:
    """Exactly one URL, carrying the campaign markup; chapters; disclosure."""
    return (
        f"{_speakable(fm.get('description') or fm.get('h1'))}\n\n"
        f"👉 Full guide: {page_url(fm, day=day)}\n\n"
        f"{format_chapters(chs)}\n\n"
        f"{' '.join(hashtags[:3])}\n\n"
        f"Affiliate link — we may earn commission. Not financial advice."
    )


def probe_duration(path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format",
         str(path)], timeout=30,
    )
    return float(json.loads(out)["format"]["duration"])


def out_dir(slug: str, day: str | None = None) -> Path:
    """Long-form output lives apart from output/<platform>/ on purpose — the
    slot watcher publishes from the Shorts tree and must never pick up a
    three-minute file and upload it as a Short."""
    return (_config.root() / "output" / "longform"
            / (day or date.today().isoformat()) / slug)


def render_one(slug: str, *, dry_run: bool = False,
               day: str | None = None) -> dict[str, Any]:
    """Render one long-form video. Does NOT publish — publication is paused."""
    fm = page(slug)
    script = build_script(fm)
    day = day or date.today().isoformat()
    d = out_dir(slug, day)
    d.mkdir(parents=True, exist_ok=True)
    (d / "script.json").write_text(json.dumps(script, indent=2), encoding="utf-8")

    voice_path = d / "voice.mp3"
    # Always Edge, never TTS_PROVIDER (T-0139 Q4): NARRATION_WORDS_PER_SECOND
    # and the [185, 260]s gate above were measured on this exact Edge voice —
    # E-19's ElevenLabs swap was never re-measured against them, and the
    # shared ElevenLabs quota (41502 chars/mo) has no room for long-form's
    # ~2800 chars/video next to the Shorts pipeline it already carries.
    voice_meta = tts_edge.synthesize(
        script["voiceover"], "youtube", voice_path, voice=PILOT_VOICE,
        dry_run=dry_run,
        dry_run_seconds=script["word_count"] / NARRATION_WORDS_PER_SECOND,
    )
    tts_seconds = probe_duration(voice_path)
    if not (GATE_MIN_SECONDS <= tts_seconds + 0.5 <= GATE_MAX_SECONDS):
        raise RuntimeError(
            f"{slug}: narration is {tts_seconds:.1f}s — outside the long-form "
            f"gate [{GATE_MIN_SECONDS}, {GATE_MAX_SECONDS}]s. A short long-form "
            f"is a Short with an unclickable link; not accepted."
        )

    # One b-roll change every ~20 s: four clips stretched over three minutes
    # is a slideshow.
    want = max(6, min(14, round(tts_seconds / 20)))
    media = fetch_visuals.fetch(script["primary_keyword"] or fm["h1"],
                                want=want, out_dir=d / "media", dry_run=dry_run,
                                platform="longform", topic_id=video_id_for(fm["slug"]))
    music_path = d / "music.mp3"
    music_meta = fetch_music.fetch(music_path, target_seconds=int(tts_seconds) + 5,
                                   dry_run=dry_run, day_seed=day)

    words = captions.align(voice_path, script["voiceover"])
    ftc = _config.root() / "assets" / "disclaimers" / "ftc_overlay.png"
    final_path = d / "final.mp4"
    render.render(
        voiceover_path=voice_path, music_path=music_path, clips=media,
        words=words, ftc_overlay_path=ftc if ftc.exists() else None,
        target_seconds=tts_seconds, out_path=final_path, platform="youtube",
        max_seconds=GATE_MAX_SECONDS,
        # Same page the description links to, from the same path (Y54). Long
        # form already has a clickable link, so here the QR is redundancy for a
        # viewer on a TV or a second screen rather than the only way out — but a
        # CTA card naming the bare homepage would be wrong in either format.
        cta_url=pipeline_main.cta_text(f"/{fm['bucket']}/{fm['slug']}/"),
        qr_payload=pipeline_main.qr_url("youtube", f"/{fm['bucket']}/{fm['slug']}/"),
    )
    duration = probe_duration(final_path)
    if not (GATE_MIN_SECONDS <= duration <= GATE_MAX_SECONDS):
        raise RuntimeError(
            f"{slug}: rendered {duration:.2f}s — outside "
            f"[{GATE_MIN_SECONDS}, {GATE_MAX_SECONDS}]s"
        )

    chs = chapters(script["sections"], words, duration)
    if len(chs) < MIN_CHAPTERS:
        raise RuntimeError(f"{slug}: only {len(chs)} chapters — YouTube needs "
                           f"{MIN_CHAPTERS}")
    # Y59 B4 — the two assets a watch page needs and a Short never did.
    # Both non-fatal, exactly like the Shorts path: a missing thumbnail costs
    # CTR, a missing caption track costs accessibility, neither is worth
    # refusing a good render over.
    srt_path = d / "captions.en.srt"
    try:
        from .captions_srt import emit_srt_from_words
        emit_srt_from_words(words, srt_path)
    except Exception as e:  # noqa: BLE001
        print(f"[longform] SRT emit failed (non-fatal): {e}")
    try:
        _dd = _config.root() / "defy_design"
        _hook = _speakable(fm.get("h1") or fm.get("title") or slug)[:60]
        subprocess.run(["node", str(_dd / "render_preview.mjs"), "--style", "auto",
                        "--hook", _hook, "--out", str(d / "thumbnail.png")],
                       timeout=120, check=False)
    except Exception as e:  # noqa: BLE001
        print(f"[longform] thumbnail render failed (non-fatal): {e}")

    hashtags = hashtag_strategy.for_youtube(video_id_for(slug), day,
                                            shorts=False)["visible"]
    description = build_description(fm, chs, hashtags, day=day)
    title = _speakable(fm.get("title"))[:100]

    summary = {
        "slug": slug, "title": title, "duration": duration,
        "tts_seconds": tts_seconds, "word_count": script["word_count"],
        "chapters": [[t, l] for t, l in chs], "clips": len(media),
        "voice": voice_meta, "music": music_meta,
        "final_path": str(final_path), "description": description,
        "url": page_url(fm, day=day), "published": False,
    }
    (d / "summary.json").write_text(json.dumps(summary, indent=2, default=str),
                                    encoding="utf-8")
    (d / "description.txt").write_text(description, encoding="utf-8")
    return summary


# ---------------------------------------------------------------------------
# Y59 — publication. render_one deliberately does NOT publish; this does.
#
# The Shorts slot-watcher publishes out of output/<platform>/ and must never
# pick up a three-minute file, so the long-form tree stays separate and gets
# its own entry point. Everything risky is already gated: the duration band is
# measured off the real mp4, validate_format_markup refuses a description whose
# utm_medium disagrees with the runtime, and refuse_placeholder_build (Y58)
# rejects a render made of solid-colour slides before the network.
# ---------------------------------------------------------------------------

PUBLISH_CHANNEL = "youtube"
PUBLISH_NOTE = "longform"
QUEUE_PATH = _config.root() / "state" / "longform_queue.json"
QUEUE_MAX_AGE_DAYS = 45


def privacy() -> str:
    """`unlisted` until the operator has watched the pilots and says otherwise.

    A wrong long-form video cannot be deleted (constitutional ban on deleting
    published videos), so the reversible state is the DEFAULT rather than the
    remedy: a bad render simply stays unlisted forever.
    """
    return os.environ.get("VG_LF_PRIVACY", "unlisted")


def _note(**fields: Any) -> None:
    """One JSONL line per decision. A skip that says nothing is a silent zero."""
    try:
        d = Path(os.environ.get("VG_LOG_DIR", "/home/cryptocardhub/logs/video"))
        d.mkdir(parents=True, exist_ok=True)
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "kind": "longform", **fields}
        with (d / f"{datetime.now(timezone.utc).date().isoformat()}.jsonl").open(
                "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        print(f"[longform] note failed (non-fatal): {e}")


def _published_rows() -> list[tuple[Any, ...]]:
    with dedup._conn() as cx:  # noqa: SLF001 — same package, no public reader exists
        return list(cx.execute(
            "SELECT ts, topic_id, external_id FROM publish_log WHERE notes = ?",
            (PUBLISH_NOTE,)))


def published_today() -> bool:
    today = datetime.now(timezone.utc).date().isoformat()
    return any(str(r[0]).startswith(today) for r in _published_rows())


def slug_published(slug: str) -> bool:
    vid = video_id_for(slug)
    return any(int(r[1]) == vid for r in _published_rows())


def queue_slugs() -> list[str]:
    """Ordered candidates. Falls back to the pilot set while no queue exists.

    A stale queue is refused loudly: a frozen list would keep re-offering pages
    whose ranking moved months ago, and the silence would look like health.
    """
    if not QUEUE_PATH.is_file():
        return [s for s in PILOT_SLUGS if not slug_published(s)]
    blob = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    built = blob.get("built_on")
    if built:
        age = (date.today() - date.fromisoformat(built)).days
        if age > QUEUE_MAX_AGE_DAYS:
            raise RuntimeError(
                f"{QUEUE_PATH.name} was built {age} days ago (max "
                f"{QUEUE_MAX_AGE_DAYS}) — refusing to publish off a stale queue; "
                f"rebuild it with scripts/longform-queue.py"
            )
    return [s for s in blob.get("slugs", []) if not slug_published(s)]


SKILL_FACTS = Path("/home/cryptocardhub/.claude/skills/etherfi/SKILL.md")


def expired_claim_patterns() -> list[tuple[str, str]]:
    """(regex, why) for every claim the fact file marks EXPIRED.

    Read from the file rather than hardcoded, so the guard cannot drift away
    from the facts it is guarding. A promo row carries `expires:` and the word
    EXPIRED once it has lapsed — that is the contract §2.0 states.
    """
    if not SKILL_FACTS.is_file():
        return []
    out = []
    for line in SKILL_FACTS.read_text(encoding="utf-8").splitlines():
        if "EXPIRED" not in line.upper():
            continue
        pct = re.search(r"(\d{1,2})\s?%", line)
        if not pct:
            continue
        n = pct.group(1)
        out.append((rf"\b{n}\s?%[^.]{{0,60}}(?:food|dining|grocer)"
                    rf"|(?:food|dining|grocer)[^.]{{0,60}}\b{n}\s?%",
                    f"{n}% promo marked EXPIRED in SKILL.md"))
    return out


def refuse_expired_claims(d: Path, slug: str) -> None:
    """A voiceover cannot be corrected after upload, and a published video is
    never deleted here — so an expired claim has to be caught before it ships.
    """
    patterns = expired_claim_patterns()
    if not patterns:
        return
    for name in ("script.json", "description.txt"):
        f = d / name
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for rx, why in patterns:
            m = re.search(rx, text, re.I)
            if m:
                raise RuntimeError(
                    f"{slug}: REFUSING to publish — {name} states a claim that "
                    f"has expired ({why}). Found: {m.group(0)[:80]!r}. "
                    f"Re-render from the corrected page instead; a voiceover "
                    f"cannot be edited after upload."
                )


def publish_one(slug: str, *, day: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Publish one already-rendered long-form video."""
    day = day or date.today().isoformat()
    d = out_dir(slug, day)
    summary_path = d / "summary.json"
    if not summary_path.is_file():
        raise RuntimeError(f"{slug}: nothing rendered at {d} — render it first")
    if slug_published(slug):
        raise RuntimeError(
            f"{slug}: already has a long-form video. One page, one long video, "
            f"for good — {len(queue_slugs())} unpublished candidates remain"
        )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    refuse_expired_claims(d, slug)
    final_path = Path(summary["final_path"])
    description = (d / "description.txt").read_text(encoding="utf-8")
    thumb = d / "thumbnail.png"
    srt = d / "captions.en.srt"
    tags = [t.lstrip("#") for t in hashtag_strategy.for_youtube(
        video_id_for(slug), day, shorts=False)["visible"]]

    result = pub_yt.upload(
        video_path=final_path,
        title=summary["title"],
        description=description,
        tags=tags,
        privacy=privacy(),
        thumbnail_path=thumb if thumb.is_file() else None,
        captions_srt_path=srt if srt.is_file() else None,
        dry_run=dry_run,
    )
    video_id = result.get("video_id")
    if video_id:
        dedup.log_publish(PUBLISH_CHANNEL, video_id_for(slug), "direct",
                          video_id, "ok", PUBLISH_NOTE)
        summary["published"] = True
        summary["video_id"] = video_id
        summary["privacy"] = privacy()
        summary_path.write_text(json.dumps(summary, indent=2, default=str),
                                encoding="utf-8")
    _note(action="publish", slug=slug, video_id=video_id,
          privacy=privacy(), dry_run=dry_run)
    return result


# T-0139 ruling-1 Q2: bounds ElevenLabs/render cost when several queued pages
# in a row fail the post-render expired-claim check; pre-render (compliance,
# thin-page) rejections are free and do not count against it.
MAX_RENDERS_PER_RUN = 3


def publish_next(*, dry_run: bool = False) -> dict[str, Any]:
    """Publish the next queued slug that clears its own gates, at most one per
    UTC day.

    A candidate can fail two page-level gates — compliance (before any
    synthesis, free) or an expired claim (after render, real cost already
    spent) — and neither is a reason to kill the run; try the next candidate
    instead (T-0139 ruling-1 Q2). Any other failure (TTS, render, upload,
    network) is the machine's problem, not the page's, and still stops the
    run as before.
    """
    if published_today():
        _note(action="skip", reason="already_published_today")
        return {"skipped": "already_published_today"}
    candidates = queue_slugs()
    if not candidates:
        _note(action="refuse", reason="queue_exhausted")
        raise RuntimeError(
            "no unpublished long-form candidates left — rebuild the queue "
            "with scripts/longform-queue.py; a silent no-op here would look "
            "exactly like a healthy quiet day"
        )
    day = date.today().isoformat()
    renders_used = 0
    skipped: list[tuple[str, str]] = []
    for slug in candidates:
        d = out_dir(slug, day)
        if not (d / "summary.json").is_file():
            if renders_used >= MAX_RENDERS_PER_RUN:
                break
            try:
                render_one(slug, dry_run=dry_run, day=day)
            except (compliance.ComplianceError, ValueError) as e:
                _note(action="skip", slug=slug, reason=type(e).__name__,
                      detail=str(e)[:200])
                skipped.append((slug, type(e).__name__))
                continue
            renders_used += 1
        try:
            refuse_expired_claims(d, slug)
        except RuntimeError as e:
            _note(action="skip", slug=slug, reason="refuse_expired_claims",
                  detail=str(e)[:200])
            skipped.append((slug, "refuse_expired_claims"))
            continue
        return publish_one(slug, day=day, dry_run=dry_run)
    raise RuntimeError(
        f"no candidate published after {len(skipped)} skip(s): {skipped}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--publish-next", action="store_true",
                    help="render if needed and publish the next queued slug")
    ap.add_argument("--publish", action="store_true",
                    help="publish an already-rendered --slug")
    ap.add_argument("--day", default=None,
                    help="render day of the directory to publish (default today)")
    args = ap.parse_args()

    if args.publish_next:
        out = publish_next(dry_run=args.dry_run)
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.publish:
        if not args.slug:
            ap.error("--publish needs --slug=<pseo-slug>")
        print(json.dumps(publish_one(args.slug, day=args.day, dry_run=args.dry_run),
                         indent=2, default=str))
        return 0
    slugs = PILOT_SLUGS if args.all else ((args.slug,) if args.slug else ())
    if not slugs:
        ap.error("pass --slug=<pseo-slug> or --all")
    rc = 0
    for s in slugs:
        try:
            r = render_one(s, dry_run=args.dry_run)
            print(f"[longform] {s}: {r['duration']:.2f}s, {r['word_count']}w, "
                  f"{len(r['chapters'])} chapters -> {r['final_path']}")
        except Exception as e:
            print(f"[longform] {s}: FAILED — {type(e).__name__}: {e}",
                  file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
