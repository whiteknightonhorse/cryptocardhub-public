"""Topic picker — the pSEO catalog as backbone, static pools as the floor.

Sources, in the order pick() tries them:
  - seo-site/src/content/pseo/news-*.md — fresh items from scripts/news-ingest.py
    (perishable, ~0.05/day, used the day it arrives or not at all)
  - .claude/skills/shorts-analysis/topic_pool_viral.json — non-finance viral,
    15% of slots, exploration input for the Thompson sampler
  - seo-site/src/content/pseo/*.md — 541 evergreen pages, each with a search
    query it already ranks for (Y53 P1-1; this is where conversions come from)
  - templates/topic_pool.json — 35 static finance topics, the floor under
    everything when the catalog is unreachable or fully cooled down

Bias mix per platform (TOFU/MOFU/BOFU) for the finance pool defined in
topic_pool.json.platform_mix. Non-finance viral pool uses category-level
filtering per topic_pool_viral.json.platform_mix_for_viral_pool.

Applies the 21-day topic dedup ledger per channel via dedup.py (Y49 P1-3).
"""
from __future__ import annotations
import hashlib
import json
import os
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from . import _config, compliance, dedup

_POOL_PATH = _config.root() / "templates" / "topic_pool.json"

# shorts-analysis is a skill in .claude/skills (parent of project root)
_VIRAL_POOL_PATH = (
    _config.root().parent / ".claude" / "skills" / "shorts-analysis" / "topic_pool_viral.json"
)

# Strategy split per shorts-analysis/SKILL.md §1.
# Override via VIRAL_POOL_RATIO env if operator wants to adjust.
#
# 2026-05-11: lowered 0.70 -> 0.30 after niche-dilution incident
# (Y44 in youtube1/SKILL.md). The original 70%-viral mix bet on broad
# hook-reach with crypto-card CTA bridge, but for a small channel
# (<1000 subs) it fragmented the YouTube niche-classification signal,
# causing view-count to collapse from avg=20/video (May 7) to 0
# (May 10) over four days.
#
# 2026-08-11 (Y53, per CCH_R2_PLAN §P1-1): lowered again 0.30 -> 0.15, and the
# standing promise of this comment — "once the channel reaches ~1000 subs the
# ratio can step back to 0.50" — is WITHDRAWN, not merely postponed. R2 measured
# where the money actually comes from: every conversion on this property arrives
# on search intent, and the pSEO catalog below is now the backbone of the topic
# list. Stepping viral back up would dilute the exact signal that converts.
# Re-raising it needs a new measurement, not this sentence.
#
# It is not 0.0 either: a viral branch that only runs on pool exhaustion would
# never run at all (the catalog carries ~4.5x the needed capacity), and a branch
# that exists but never executes is the shape that misfired in Y49 and Y52.
# 0.15 is ~1 slot in 6 a day — a live, measurable exploration input for the
# Thompson sampler, which the operator can zero via VIRAL_POOL_RATIO without code.
_VIRAL_RATIO_DEFAULT = 0.15

# --- Y49 P1-3: news as a topic source ---------------------------------------
# scripts/news-ingest.py already runs 2x/day and writes bucket=news articles
# into the Astro content collection. Fresh, non-repeating material is exactly
# what the static 35+22 pool cannot give (213 publications over 65 topics).
# Supply is thin — measured 0.05 published items/day over the last 21 days —
# so news is an opportunistic source: taken when present, silently skipped
# when not. The ratio is the *attempt* rate, not a quota.
#
# One Astro collection holds both kinds of page: `news-*.md` written by
# news-ingest, and the 541 evergreen pSEO pages read by the source below. Hence
# one constant — under two names a test could redirect half the catalog (Y53).
_CONTENT_DIR = _config.root().parent / "seo-site" / "src" / "content" / "pseo"
_NEWS_RATIO_DEFAULT = 0.5
_NEWS_FRESHNESS_HOURS_DEFAULT = 168  # 7 days
_NEWS_ID_BASE = 1_000_000           # keeps news ids clear of both pool id spaces

# --- Y53 P1-1: the pSEO catalog as the backbone of the topic list ------------
# news-ingest supplies ~0.05 topics/day by design (3 regulator feeds, hard AND
# gate) — it is an overlay, never the backbone. The catalog is: 541 non-news
# pages, each already carrying a title, a search query it ranks for and vetted
# facts. At a 21-day cooldown that is ~26 topics/day of capacity against a need
# of 2.
_PSEO_ID_BASE = 2_000_000           # clear of finance (1-35), viral, news

# Buckets first in line, per CCH_R2_PLAN §P1-1: these answer a question someone
# typed, which is the intent that converts here. 190 of the 541 pages.
_PSEO_PRIORITY_BUCKETS = ("reviews", "faq", "geo", "help")

# Every pSEO URL that has ever produced a register_click, read live from GA4 on
# 2026-08-11 — the entire measured revenue surface of the site.
#
# This is a deliberate SECOND copy of `FROZEN_SLUGS` in scripts/pseo-generate.py,
# not an import: the two lists serve different laws (there it freezes a page
# against rewriting, here it puts a page first in the video topic list), and the
# frozen list's own contract says it must stay a literal in that file, edited by
# a human. tests/test_pseo_topics.py compares the two sets and goes red on drift,
# so an unfreeze cannot silently change what the channel films.
_CONVERTING_SLUGS: frozenset[str] = frozenset({
    "best-crypto-card-india",
    "best-crypto-card-turkey",
    "best-crypto-card-germany",
    "best-crypto-card-for-usdt",
    "crypto-cashback-vs-traditional-cashback",
    "crypto-debit-vs-credit-card",
    "how-do-crypto-debit-cards-work",
    "what-is-a-defi-card",
    "what-is-the-daily-limit-on-crypto-com-card",
    "crypto-card-for-asia-travel",
    "ether-fi-card-not-arriving",
    "how-to-apply-for-a-crypto-debit-card",
    "how-to-connect-wallet-to-ether-fi-cash",
    "crypto-card-with-cashback",
    "defi-crypto-card",
    "ether-fi-cash-review",
    "gnosis-pay-review",
    "is-ether-fi-cash-safe",
    "is-redotpay-legit",
    "uphold-card-review",
})


def _news_ratio() -> float:
    return _env_ratio("NEWS_POOL_RATIO", _NEWS_RATIO_DEFAULT)


def _env_ratio(name: str, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(os.environ.get(name, str(default)))))
    except ValueError:
        return default


def _viral_ratio() -> float:
    raw = os.environ.get("VIRAL_POOL_RATIO", str(_VIRAL_RATIO_DEFAULT))
    try:
        v = float(raw)
        return max(0.0, min(1.0, v))
    except ValueError:
        return _VIRAL_RATIO_DEFAULT


def _pool() -> dict[str, Any]:
    return json.loads(_POOL_PATH.read_text(encoding="utf-8"))


def _viral_pool() -> dict[str, Any] | None:
    if not _VIRAL_POOL_PATH.exists():
        return None
    try:
        return json.loads(_VIRAL_POOL_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _weighted_class(platform: str, rng: random.Random) -> str:
    mix = _pool()["platform_mix"].get(platform, {"TOFU": 0.33, "MOFU": 0.33, "BOFU": 0.34})
    classes = list(mix.keys())
    weights = [mix[c] for c in classes]
    return rng.choices(classes, weights=weights, k=1)[0]


def _pick_finance(platform: str, rng: random.Random) -> dict[str, Any] | None:
    """Pick from the finance-niched pool. Returns None if no compliant topic available."""
    pool = _pool()["topics"]
    target_class = _weighted_class(platform, rng)
    candidates = [t for t in pool if t.get("class") == target_class]
    rng.shuffle(candidates)

    # First pass: target-class candidates not used in 7d
    for t in candidates:
        if dedup.topic_used_recently(t["id"], platform):
            continue
        # T-0140 (ruling-1): same cooldown as _pick_pseo:370 — a failed slot cools off.
        if dedup.topic_failed_recently(t["id"], platform):
            continue
        if not compliance.topic_safe(t):
            continue
        return _enrich_finance(t, platform)

    # Fallback: any class, not used in 7d
    for t in rng.sample(pool, len(pool)):
        if dedup.topic_used_recently(t["id"], platform):
            continue
        # T-0140 (ruling-1): same cooldown as _pick_pseo:370 — a failed slot cools off.
        if dedup.topic_failed_recently(t["id"], platform):
            continue
        if not compliance.topic_safe(t):
            continue
        return _enrich_finance(t, platform)
    return None


def _pick_viral(platform: str, rng: random.Random) -> dict[str, Any] | None:
    """Pick from the non-finance viral pool. Returns None if pool unavailable
    or no compliant topic."""
    vp = _viral_pool()
    if not vp:
        return None
    topics = vp.get("topics", [])
    mix_filter = vp.get("platform_mix_for_viral_pool", {}).get(platform, {})
    max_difficulty = mix_filter.get("cta_bridge_difficulty_max", 5)
    viral_filter = mix_filter.get("viral_potential_filter", "medium+")

    # Tier filter: medium+, high+, high
    def _tier_ok(potential: str) -> bool:
        order = {"low": 0, "medium": 1, "high": 2, "very_high": 3}
        if viral_filter == "high":
            return potential in ("high", "very_high")
        if viral_filter == "high+":
            return order.get(potential, 0) >= 2
        if viral_filter == "medium+":
            return order.get(potential, 0) >= 1
        return True

    candidates = [
        t for t in topics
        if platform in t.get("platforms", [])
        and t.get("cta_bridge_difficulty", 5) <= max_difficulty
        and _tier_ok(t.get("viral_potential", "medium"))
        and not t.get("disabled", False)
    ]
    rng.shuffle(candidates)

    for t in candidates:
        if dedup.topic_used_recently(t["id"], platform):
            continue
        # T-0140 (ruling-1): same cooldown as _pick_pseo:370 — a failed slot cools off.
        if dedup.topic_failed_recently(t["id"], platform):
            continue
        if not compliance.topic_safe(t):
            continue
        return _enrich_viral(t, platform)
    return None


def _news_id(slug: str) -> int:
    """Stable integer id for a news slug — the dedup ledger keys on ints."""
    return _NEWS_ID_BASE + int(hashlib.sha256(slug.encode()).hexdigest()[:12], 16) % 1_000_000


def _news_articles(max_age_hours: float | None = None) -> list[dict[str, Any]]:
    """Fresh bucket=news articles, newest first. Empty list if none/unreadable."""
    if max_age_hours is None:
        max_age_hours = float(os.environ.get("NEWS_FRESHNESS_HOURS",
                                             _NEWS_FRESHNESS_HOURS_DEFAULT))
    if not _CONTENT_DIR.is_dir():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    out: list[dict[str, Any]] = []
    for path in _CONTENT_DIR.glob("news-*.md"):
        fm: dict[str, Any] = {}
        try:
            head = path.read_text(encoding="utf-8").split("---", 2)
            if len(head) < 3:
                continue
            for line in head[1].splitlines():
                m = re.match(r"^([A-Za-z_]+): (.*)$", line)
                if m:
                    try:
                        fm[m.group(1)] = json.loads(m.group(2))
                    except json.JSONDecodeError:
                        fm[m.group(1)] = m.group(2)
        except OSError:
            continue
        stamp = fm.get("news_published_at") or fm.get("generated_at")
        title = fm.get("title")
        if not (isinstance(stamp, str) and isinstance(title, str) and title.strip()):
            continue
        try:
            published = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        if published < cutoff:
            continue
        slug = str(fm.get("slug") or path.stem)
        out.append({
            "id": _news_id(slug),
            "title": title.strip(),
            "primary": str(fm.get("primary_keyword") or title).strip(),
            "slug": slug,
            "source_name": fm.get("source_name", ""),
            "published_at": published,
        })
    out.sort(key=lambda a: a["published_at"], reverse=True)
    return out


def _pick_news(platform: str, rng: random.Random) -> dict[str, Any] | None:
    """Pick the freshest news topic not used within the cooldown. None if the
    news catalog is empty — supply is thin by design, this is not an error."""
    for a in _news_articles():
        if dedup.topic_used_recently(a["id"], platform):
            continue
        # T-0140 (ruling-1): same cooldown as _pick_pseo:370 — a failed slot cools off.
        if dedup.topic_failed_recently(a["id"], platform):
            continue
        if not compliance.topic_safe(a):
            continue
        return _enrich_news(a, platform)
    return None


def _pseo_id(slug: str) -> int:
    """Stable integer id for a pSEO slug — the dedup ledger keys on ints."""
    return _PSEO_ID_BASE + int(hashlib.sha256(slug.encode()).hexdigest()[:12], 16) % 1_000_000


def _pseo_pages() -> list[dict[str, Any]]:
    """Evergreen catalog pages, most promising first.

    Order is a priority, not a ranking: pages proven to convert, then the
    buckets that answer a typed question, then the rest. Everything after the
    sort is still reachable — the tail is what gives the 21-day cooldown its
    room. Empty list when the catalog is missing or unreadable, which lets the
    caller fall back to the static pools instead of stopping the slot.
    """
    if not _CONTENT_DIR.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(_CONTENT_DIR.glob("*.md")):
        if path.name.startswith("news-"):
            continue  # owned by _pick_news, on a freshness rule of its own
        fm: dict[str, Any] = {}
        try:
            head = path.read_text(encoding="utf-8").split("---", 2)
            if len(head) < 3:
                continue
            for line in head[1].splitlines():
                m = re.match(r"^([A-Za-z_]+): (.*)$", line)
                if m:
                    try:
                        fm[m.group(1)] = json.loads(m.group(2))
                    except json.JSONDecodeError:
                        fm[m.group(1)] = m.group(2)
        except OSError:
            continue
        slug = str(fm.get("slug") or path.stem).strip()
        keyword = str(fm.get("primary_keyword") or "").strip()
        title = str(fm.get("h1") or fm.get("title") or "").strip()
        bucket = str(fm.get("bucket") or "").strip()
        if not (slug and keyword and title):
            continue  # a page without a query is not a topic
        out.append({
            "id": _pseo_id(slug),
            "title": title,
            "primary": keyword,
            "slug": slug,
            "bucket": bucket,
            "target_pseo_slug": slug,
            "converting": slug in _CONVERTING_SLUGS,
        })
    out.sort(key=lambda p: (
        not p["converting"],
        p["bucket"] not in _PSEO_PRIORITY_BUCKETS,
        p["slug"],
    ))
    return out


def _pick_pseo(platform: str, rng: random.Random) -> dict[str, Any] | None:
    """Pick the highest-priority catalog page not used within the cooldown.

    None when the catalog is absent or fully cooled down — the caller then uses
    the static pools. This must never be the branch that raises: exhaustion is
    reported once, by pick(), after every source has been tried (Y49).
    """
    for p in _pseo_pages():
        if dedup.topic_used_recently(p["id"], platform):
            continue
        # Провал ТОЖЕ охлаждает. Без этой строки селектор зацикливается по построению: `mark_used`
        # зовётся только после успешной публикации, поэтому тема, падающая на fetch_visuals,
        # никогда не попадала в кулдаун и выдавалась каждый следующий слот. Измерено: 169 отказов
        # с 16 по 24 августа — ОДНА тема (позиция 9 каталога, `what-is-the-daily-limit-on-
        # crypto-com-card`), и публикации встали в ноль, когда исчерпались кулдауны веток
        # news/viral, стоящих в pick() выше pSEO. Постановление Fable 2026-08-24 §6.
        if dedup.topic_failed_recently(p["id"], platform):
            continue
        if not compliance.topic_safe(p):
            continue
        return _enrich_pseo(p, platform)
    return None


def pick(platform: str, seed: int | None = None) -> dict[str, Any]:
    """Pick a topic. Sources in order: news → viral (15%) → pSEO catalog.

    Y53 P1-1 turns the order around: the pSEO catalog is the backbone, because
    it is where the site's measured conversions come from, and the static pools
    are what is left when the catalog cannot answer. News stays first and
    unchanged (Y49) — it is thin but perishable, so it is used the day it
    arrives or not at all.

    Raises RuntimeError if no source produces a compliant topic.
    """
    rng = random.Random(seed)

    # E-20: VG_MODE=h1 replaces the whole selector — the channel's strategy
    # narrows to filmed-from-a-hot-page only, not one more source in the mix.
    if _config.vg_mode() == "h1":
        topic = _pick_h1(platform, rng)
        if topic is None:
            n_pass = len(_h1_candidates(platform))
            why = (
                f"{n_pass} hot pages pass the fetch/partner/no-video filters but every one "
                f"is cooling in the topic_failure ledger or fails compliance — check the "
                f"ledger and the failure that cooled them BEFORE waiting for the cadence"
                if n_pass else
                f"every candidate in logs/aeo/targets-*.json's hot list is below "
                f"{_H1_MIN_VERIFIED} verified fetches, lacks a confirmed primary_partner: "
                f"etherfi, or already has a video. Run aeo-aggregate.py --targets, or wait "
                f"for the cadence"
            )
            raise RuntimeError(f"VG_MODE=h1: no eligible hot etherfi page for {platform} — {why}.")
        return topic

    # Y49 P1-3 — fresh news first when the roll says so and material exists.
    if rng.random() < _news_ratio():
        topic = _pick_news(platform, rng)
        if topic is not None:
            return topic

    use_viral = rng.random() < _viral_ratio()

    if use_viral:
        topic = _pick_viral(platform, rng)
        if topic is None:
            # Viral pool exhausted or unavailable — fall back to the catalog
            topic = _pick_pseo(platform, rng)
    else:
        topic = _pick_pseo(platform, rng)

    if topic is None:
        # The static finance pool is the floor under both branches: reached
        # when the catalog is missing (a fresh checkout, a broken sync) or
        # fully cooled down.
        topic = _pick_finance(platform, rng)
    if topic is None:
        topic = _pick_viral(platform, rng)

    if topic is None:
        # Y49 P1-3: the old last-resort fallback returned the lowest-id topic
        # while IGNORING the cooldown — it was the hole through which 3.3x
        # topic reuse arrived. Exhaustion is now loud and stops the slot.
        raise RuntimeError(
            f"No topic available for {platform}: every compliant topic in the "
            f"pSEO catalog and the finance, viral and news sources was used "
            f"within the last {dedup._TOPIC_TTL_DAYS} days. Add topics or "
            f"lower the cadence."
        )

    return topic


def _enrich_finance(topic: dict[str, Any], platform: str) -> dict[str, Any]:
    """Enrich a finance topic with platform/duration metadata."""
    p = _pool()
    band = p["duration_band_seconds"][platform][topic["class"]]
    target_sec = (band[0] + band[1]) / 2
    target_words = int(target_sec * 2.4)  # ~2.4 words/s for English narration
    return {
        **topic,
        "pool": "finance",
        "platform": platform,
        "target_seconds": target_sec,
        "target_words": target_words,
        "duration_band": band,
    }


def _enrich_viral(topic: dict[str, Any], platform: str) -> dict[str, Any]:
    """Enrich a viral topic. Default duration band per platform from
    creative_patterns.json's duration_band axis (15-45s sweet spot)."""
    # Defaults derived from shorts-analysis SKILL.md §4
    platform_default_seconds = {
        "youtube": (30, 45),
        "instagram": (15, 30),
        "tiktok": (21, 34),
    }
    band = platform_default_seconds.get(platform, (20, 35))
    target_sec = (band[0] + band[1]) / 2
    target_words = int(target_sec * 2.4)
    return {
        **topic,
        "pool": "viral",
        "platform": platform,
        "class": "VIRAL",
        "target_seconds": target_sec,
        "target_words": target_words,
        "duration_band": list(band),
    }


def _enrich_pseo(page: dict[str, Any], platform: str) -> dict[str, Any]:
    """A catalog page rides the finance MOFU band — same register, same length.

    `target_pseo_slug` and `primary` travel with the topic so the writer can
    stay on the page's own query and the video can link back to it.
    """
    enriched = _enrich_finance({**page, "class": "MOFU"}, platform)
    enriched["pool"] = "pseo"
    return enriched


def _enrich_news(article: dict[str, Any], platform: str) -> dict[str, Any]:
    """News rides the finance duration band (MOFU) — same register, same length."""
    art = {k: v for k, v in article.items() if k != "published_at"}  # keep it JSON-safe
    art["published_at_iso"] = article["published_at"].isoformat()
    enriched = _enrich_finance({**art, "class": "MOFU"}, platform)
    enriched["pool"] = "news"
    return enriched


# --- E-20 (VG_MODE=h1): hot etherfi pSEO pages as the video topic ----------
# Source is the AEO instrument's own weekly output (logs/aeo/targets-<week>.json,
# `hot` list — verified agent fetches per published page, aeo-aggregate.py
# --targets), not a second measurement. Filters, in order: verified fetches
# floor, resolved partner == etherfi (the SAME three-rule law the page itself
# renders under — see `_h1_primary_partner` — not a stricter frontmatter-only
# re-derivation), and no video already made for that page (permanent,
# dedup.topic_ever_published — unlike the 21-day rotation every other source
# in this file uses).
_H1_MIN_VERIFIED = 5
_AEO_DIR = _config.root().parent / "logs" / "aeo"
_AVAILABILITY_PATH = _config.root().parent / "config" / "availability.yaml"


def _slug_for_path(path: str) -> str | None:
    slug = path.strip("/").split("/")[-1] if path else ""
    return slug or None


def _h1_targets_path() -> Path | None:
    """Latest `logs/aeo/targets-*.json`, or None if the AEO instrument has
    never run --targets."""
    if not _AEO_DIR.is_dir():
        return None
    files = sorted(_AEO_DIR.glob("targets-*.json"))
    return files[-1] if files else None


def _h1_primary_partner(slug: str) -> str | None:
    """The partner `slug`'s page actually resolves its CTAs to — the SAME
    three-rule law `affiliates.mjs:primaryPartnerFor` and
    `pseo-generate.py:primary_partner_for` already implement (Y50), transplanted
    here rather than re-derived: explicit `primary_partner` frontmatter wins;
    else a geo slug the issuer can't serve (`unavailable_slugs` in
    availability.yaml) routes to the fallback partner; else the default is
    etherfi. Returns None only if the page itself doesn't exist (not a real
    topic, excluded regardless of partner).

    A frontmatter-only reading (declared-or-unconfirmed) is a STRICTER law
    than the one the page renders under, and on this corpus that emptied the
    H1 queue from 6 real candidates to 2 (w4-20.ruling-2) — only 36 of 574
    pages declare `primary_partner` explicitly; the rest render as etherfi via
    the default and must be counted as etherfi here too."""
    path = _CONTENT_DIR / f"{slug}.md"
    if not path.is_file():
        return None
    try:
        head = path.read_text(encoding="utf-8").split("---", 2)
    except OSError:
        return None
    declared: str | None = None
    if len(head) >= 3:
        for line in head[1].splitlines():
            m = re.match(r"^primary_partner: (.*)$", line)
            if m:
                try:
                    declared = json.loads(m.group(1))
                except json.JSONDecodeError:
                    declared = None
                break
    if declared:
        return declared
    avail = yaml.safe_load(_AVAILABILITY_PATH.read_text(encoding="utf-8"))
    if slug in set(avail.get("unavailable_slugs") or []):
        return avail["etherfi"]["fallback_partner"]
    return "etherfi"


def _h1_candidates(platform: str) -> list[dict[str, Any]]:
    """Ordered H1 topic candidates, most agent-fetched first."""
    path = _h1_targets_path()
    if path is None:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    hot = sorted(
        (h for h in (data.get("hot") or []) if isinstance(h, dict)),
        key=lambda h: (-int(h.get("fetches", 0) or 0), str(h.get("path", ""))),
    )
    pages_by_slug = {p["slug"]: p for p in _pseo_pages()}
    out: list[dict[str, Any]] = []
    for h in hot:
        fetches = int(h.get("fetches", 0) or 0)
        if fetches < _H1_MIN_VERIFIED:
            continue
        slug = _slug_for_path(str(h.get("path") or ""))
        if not slug or _h1_primary_partner(slug) != "etherfi":
            continue
        page = pages_by_slug.get(slug)
        if page is None:
            continue  # frontmatter unreadable / not a real topic (Y53)
        topic_id = _pseo_id(slug)
        if dedup.topic_ever_published(topic_id, platform):
            continue
        out.append({**page, "id": topic_id, "fetches": fetches})
    return out


def _pick_h1(platform: str, rng: random.Random) -> dict[str, Any] | None:
    for p in _h1_candidates(platform):
        # T-0140 (ruling-1): the same cooldown _pick_pseo:370 already applies —
        # a failed slot must cool off or the selector re-offers it every tick.
        if dedup.topic_failed_recently(p["id"], platform):
            continue
        if not compliance.topic_safe(p):
            continue
        return _enrich_h1(p, platform)
    return None


def _enrich_h1(page: dict[str, Any], platform: str) -> dict[str, Any]:
    """A hot page rides the finance MOFU band, same as a regular catalog
    topic — H1 is a different SOURCE, not a different render shape."""
    enriched = _enrich_finance({**page, "class": "MOFU"}, platform)
    enriched["pool"] = "h1"
    return enriched


def mark_used(topic_id: int, platform: str) -> None:
    """Call AFTER successful publish (or after TG-preview-approved)."""
    dedup.record_topic(topic_id, platform)
