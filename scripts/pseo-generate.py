#!/usr/bin/env python3
"""
pseo-generate.py — DefyCard programmatic SEO page generator.

Pulls keyword clusters from .claude/skills/keywords/SKILL.md, generates Astro
content collection entries via Claude Haiku 4.5 (with prompt caching), fetches
2-3 Unsplash images per article, writes to seo-site/src/content/pseo/<slug>.md.

Modes:
  --dry-run                  parse + queue; no API calls, no file writes
  --probabilistic            cron-tick gate (random distribution across 24h)
  (default = bulk)           process up to RATE pages serially

See .claude/skills/{pseo-pages,write-article}/SKILL.md for architecture,
quality contract, and phased rollout.
"""
from __future__ import annotations

import argparse
import json
import datetime as _dt
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

try:
    import urllib.request
    import urllib.parse
except ImportError:  # pragma: no cover
    urllib = None  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from slug_lib import slugify, retired_slugs  # noqa: E402 -- LAW #ONE-PLACE canonical slugify
import availability_lib  # noqa: E402 -- LAW #ONE-PLACE canonical unavailable_for
from flags import pseo_enabled  # noqa: E402 -- LAW #ONE-PLACE kill-switch
import telegram_lib  # noqa: E402
import no_progress_guard  # noqa: E402
import money_gate  # noqa: E402 -- G36: a number in money copy must come from a fact
import facts_lib  # noqa: E402 -- competitor facts block in the writer prompt
import humanize_gate  # noqa: E402 -- G37: copy-edit AI tells before G36 sees the text
SKILL_KEYWORDS = ROOT / ".claude" / "skills" / "keywords" / "SKILL.md"
SKILL_ETHERFI = ROOT / ".claude" / "skills" / "etherfi" / "SKILL.md"
SKILL_DESIGN = ROOT / ".claude" / "skills" / "design-system" / "SKILL.md"
SKILL_COMPETITOR = ROOT / ".claude" / "skills" / "competitor-analysis" / "SKILL.md"
SKILL_WRITE_ARTICLE = ROOT / ".claude" / "skills" / "write-article" / "SKILL.md"
CONTENT_DIR = ROOT / "seo-site" / "src" / "content" / "pseo"
LOG_DIR = ROOT / "logs" / "pseo"
STATE_DIR = ROOT / "video-generator" / "state"
RATE_FILE_DEFAULT = ROOT / "config" / "pseo-rate.txt"

FLEET_PAUSED_UNTIL_PATH = Path.home() / ".fleet-paused-until"


def fleet_paused_until_epoch() -> Optional[int]:
    """T-0125: this script's call_claude_cli() hits /usr/bin/claude by an
    ABSOLUTE path, from a cron line that also invokes /usr/bin/python3 by
    absolute path -- neither ever goes through PATH, so the ~/bin/claude
    PATH-guard (T-0121) cannot see this call at all. Ported verbatim from
    apibase's own content-machine/scripts/pseo-generate.py (T-15):
    mirrors ~/bin/claude's own pause-wrapper parsing exactly (first line,
    strip everything but digits) so this reads the same file the same way --
    a missing/empty/malformed file means "not paused", never a crash."""
    if not FLEET_PAUSED_UNTIL_PATH.exists():
        return None
    try:
        first_line = FLEET_PAUSED_UNTIL_PATH.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return None
    digits = re.sub(r"[^0-9]", "", first_line)
    return int(digits) if digits else None


def fleet_pause_reason_by() -> tuple[str, str]:
    """Lines 2/3 of ~/.fleet-paused-until ('reason: ...' / 'by: ...'), or
    'absent' for either that the setter didn't write (T-0142-03).

    Before this, `skip: fleet_paused` named only the deadline -- a human
    reading the log for eight days (Y53's family: a value existed, nothing
    read it at the boundary) never learned who paused the fleet or why.
    """
    try:
        lines = FLEET_PAUSED_UNTIL_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "absent", "absent"
    reason = by = "absent"
    for line in lines[1:]:
        if line.startswith("reason:"):
            reason = line.split(":", 1)[1].strip() or "absent"
        elif line.startswith("by:"):
            by = line.split(":", 1)[1].strip() or "absent"
    return reason, by


DEFAULT_MODEL = os.environ.get("WRITER_MODEL", "claude-haiku-5-5")
UNSPLASH_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "").strip()

# Probabilistic-gate parameters (per write-article skill §4.3)
GATE_PROBABILITY = float(os.environ.get("PSEO_GATE_PROBABILITY", "0.30"))
COOLDOWN_MINUTES = int(os.environ.get("PSEO_COOLDOWN_MINUTES", "30"))

# Compliance gate — never let Opus / Sonnet leak into bulk generation by accident.
assert "haiku" in DEFAULT_MODEL.lower(), (
    f"WRITER_MODEL must be Haiku class for cost; got {DEFAULT_MODEL!r}"
)


# =============================================================================
# Keyword bank parser
# =============================================================================

BUCKET_HEADER_RE = re.compile(
    r"^## \d+\. Bucket ([A-L]) — (.+?)\s*\(", re.MULTILINE
)
TABLE_ROW_RE = re.compile(r"^\|\s*([A-L]\d+)\s*\|\s*(.+?)\s*\|", re.MULTILINE)

# Map bucket letter -> (URL/frontmatter segment, page-pattern hint per keywords §15)
# The first element is BOTH the URL prefix and the `bucket` frontmatter field
# consumed by Astro's [bucket]/[slug].astro dynamic route.
BUCKET_META = {
    "A": ("hub", "P-Hub"),
    "B": ("compare", "P-Comp"),
    "C": ("reviews", "P-Review"),
    "D": ("geo", "P-Geo"),
    "E": ("tools", "P-Calc"),
    "F": ("how-to", "P-HowTo"),
    "G": ("for", "P-CompUseCase"),
    "H": ("learn", "P-Education"),
    "I": ("learn", "P-Education"),
    "J": ("help", "P-Problem"),
    # "K": NEWS bucket is owned by scripts/news-ingest.py since 2026-05-07.
    # Static-keyword generator must NOT produce news articles — they were
    # evergreen explainers labeled "news" and Google's helpful-content
    # classifier flags that pattern. Real news comes from primary-source
    # RSS pipeline (regulators + issuer blogs).
    "L": ("faq", "P-FAQ"),
}


@dataclass
class KeywordRow:
    code: str          # "B7"
    bucket: str        # "B"
    bucket_name: str   # "Comparison"
    keyword: str       # "redotpay vs nexo card"
    supporting_only: bool = False   # Notes cell says `supporting-only`: never a page of its own


@dataclass
class Cluster:
    """One page = primary + 2 supporting keywords."""
    primary: KeywordRow
    supporting: list[KeywordRow]
    pattern: str       # "P-Comp" etc
    url_segment: str   # path under defycard.com


def parse_keyword_bank(path: Path) -> list[KeywordRow]:
    """Parse the markdown skill file and extract one row per keyword."""
    text = path.read_text(encoding="utf-8")
    rows: list[KeywordRow] = []

    # Split by bucket header; each chunk = one bucket section
    matches = list(BUCKET_HEADER_RE.finditer(text))
    for i, m in enumerate(matches):
        bucket_letter = m.group(1)
        bucket_name = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = text[start:end]

        for row_match in TABLE_ROW_RE.finditer(section):
            code = row_match.group(1)
            keyword = row_match.group(2).strip()
            # Skip table-header echoes / empty placeholders
            if not keyword or keyword.lower() in {"keyword", "host", "type"}:
                continue
            # Skip header letter (e.g. literal "A" or "B" in the # column)
            if not re.match(rf"^{bucket_letter}\d+$", code):
                continue
            eol = section.find("\n", row_match.end())
            line = section[row_match.start():eol if eol != -1 else len(section)]
            rows.append(KeywordRow(
                code=code,
                bucket=bucket_letter,
                bucket_name=bucket_name,
                keyword=keyword,
                supporting_only="supporting-only" in line,
            ))
    return rows


# =============================================================================
# Probabilistic gate (random 24h distribution — write-article skill §4)
# =============================================================================


def _state_file(name: str) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / name


def daily_count_path() -> Path:
    today = datetime.now(timezone.utc).date().isoformat()
    return _state_file(f"pseo-publish-count-{today}.txt")


def last_publish_ts_path() -> Path:
    return _state_file("pseo-last-publish-ts.txt")


def read_daily_count() -> int:
    p = daily_count_path()
    if not p.exists():
        return 0
    try:
        return int(p.read_text().strip())
    except Exception:
        return 0


def increment_daily_count() -> None:
    p = daily_count_path()
    p.write_text(str(read_daily_count() + 1))


def _decrement_daily_count() -> None:
    """Roll back a claimed slot when generation fails. Called under no lock —
    risk: if another tick increments concurrently we can race. Acceptable
    because the LOCK_NB gate has already returned for this tick window."""
    p = daily_count_path()
    cur = read_daily_count()
    if cur > 0:
        p.write_text(str(cur - 1))


def read_last_publish_ts() -> Optional[datetime]:
    p = last_publish_ts_path()
    if not p.exists():
        return None
    try:
        return datetime.fromisoformat(p.read_text().strip())
    except Exception:
        return None


def update_last_publish_ts() -> None:
    last_publish_ts_path().write_text(datetime.now(timezone.utc).isoformat())


# Slot accounting, at module level so the entry point's `finally` can see it.
# A local flag cannot be read by the wrapper whose whole job is to guarantee
# the refund.
_SLOT = {"claimed": False, "refundable": False}


def _refund_slot_if_unspent() -> None:
    """Give the day's slot back unless it bought a published page."""
    if _SLOT["claimed"] and _SLOT["refundable"]:
        _decrement_daily_count()
        _SLOT["claimed"] = False
        print("[pseo-tick] slot returned (nothing was published this tick)")


def probabilistic_gate(daily_cap: int) -> tuple[bool, str]:
    """Return (allowed, reason). True = generate one article this tick.

    Wrapped in fcntl.flock on a sentinel file so two overlapping cron ticks
    cannot both pass the cap check (race-fix per councilreview 2026-05-05).
    """
    import fcntl
    lock_path = _state_file("pseo-gate.lock")
    lock_fh = open(lock_path, "a+")
    try:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False, "gate_locked (another tick in progress)"

        count = read_daily_count()
        if count >= daily_cap:
            return False, f"daily_cap_reached ({count}/{daily_cap})"
        last = read_last_publish_ts()
        if last is not None:
            age_min = (datetime.now(timezone.utc) - last).total_seconds() / 60
            if age_min < COOLDOWN_MINUTES:
                return False, f"cooldown_active ({age_min:.0f}/{COOLDOWN_MINUTES}min)"
        roll = random.random()
        if roll >= GATE_PROBABILITY:
            return False, f"gate_skip (roll={roll:.3f} >= p={GATE_PROBABILITY})"
        # Reserve our slot now (claim the increment under the lock) so a
        # parallel tick can't also pass the cap check.
        increment_daily_count()
        update_last_publish_ts()
        return True, f"fire (roll={roll:.3f} < p={GATE_PROBABILITY}, count={count + 1})"
    finally:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock_fh.close()


# =============================================================================
# Unsplash image fetcher (write-article skill §3)
# =============================================================================


@dataclass
class UnsplashImage:
    src: str        # full URL with crop params (Layer A)
    alt: str
    credit_html: str
    photographer_name: str
    photographer_url: str
    query: str


def unsplash_search(query: str, count: int = 2) -> list[UnsplashImage]:
    """Fetch landscape images via Unsplash API. Empty list on any error (fail-soft).

    All API-sourced strings are html.escape()'d before being woven into HTML.
    src is asserted to be on the Unsplash CDN — third-party hosts are dropped.
    """
    import html as _html
    if not UNSPLASH_KEY or not query.strip():
        return []
    try:
        params = urllib.parse.urlencode({
            "query": query.strip()[:40],
            "orientation": "landscape",
            "content_filter": "high",
            "per_page": max(count, 3),
        })
        req = urllib.request.Request(
            f"https://api.unsplash.com/search/photos?{params}",
            headers={
                "Accept-Version": "v1",
                "Authorization": f"Client-ID {UNSPLASH_KEY}",
            },
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = data.get("results", [])
    except Exception:
        return []

    out: list[UnsplashImage] = []
    for r in results[:count]:
        raw = (r.get("urls") or {}).get("raw") or ""
        if not raw or not raw.startswith("https://images.unsplash.com/"):
            continue
        # Layer A — force 16:9 + face-crop at CDN level
        sep = "&" if "?" in raw else "?"
        src = f"{raw}{sep}w=1280&h=720&fit=crop&crop=faces,entropy&q=80&fm=jpg"
        alt_raw = (r.get("alt_description") or query).strip()[:120]
        photog = (r.get("user") or {})
        name_raw = (photog.get("name") or "Photographer").strip()[:80]
        username_raw = (photog.get("username") or "").strip()[:80]
        # Whitelist usernames to a safe alphabet to prevent URL injection
        username = re.sub(r"[^A-Za-z0-9_-]", "", username_raw)
        utm = "?utm_source=defycard&utm_medium=referral"
        prof_url = (
            f"https://unsplash.com/@{username}{utm}" if username
            else f"https://unsplash.com/{utm}"
        )
        unsplash_url = f"https://unsplash.com/{utm}"
        # Pre-escape all components woven into credit_html
        name_esc = _html.escape(name_raw, quote=True)
        prof_url_esc = _html.escape(prof_url, quote=True)
        unsplash_url_esc = _html.escape(unsplash_url, quote=True)
        credit = (
            f'Photo by <a href="{prof_url_esc}" rel="noopener">{name_esc}</a> on '
            f'<a href="{unsplash_url_esc}" rel="noopener">Unsplash</a>'
        )
        out.append(UnsplashImage(
            src=src,
            alt=alt_raw,                     # alt is escaped at HTML emission time
            credit_html=credit,              # already escaped here
            photographer_name=name_raw,
            photographer_url=prof_url,
            query=query,
        ))
    return out


# =============================================================================
# Placeholder substitution (image + affiliate-button markers)
# =============================================================================

IMG_PLACEHOLDER_RE = re.compile(r"<<IMAGE_(\d+)>>")
AFFILIATE_BUTTON_PLACEHOLDER = "<<AFFILIATE_BUTTON>>"
AFFILIATE_URL_PLACEHOLDER = "<<AFFILIATE_URL>>"

AVAILABILITY_PATH = Path(__file__).resolve().parent.parent / "config" / "availability.yaml"


def primary_partner_for(slug: str) -> str:
    """Affiliate program a page's generic CTAs should point at.

    Y50 P0-1: geo pages for jurisdictions where ether.fi is NOT available were
    still emitting ether.fi as their only external card link — the reader
    cannot be issued that card, so the click is a guaranteed dead end. The
    jurisdiction list is config/availability.yaml (verified against the
    issuer's help centre), never against our own pages.
    """
    import yaml
    avail = yaml.safe_load(AVAILABILITY_PATH.read_text(encoding="utf-8"))
    if slug in set(avail.get("unavailable_slugs") or []):
        return avail["etherfi"]["fallback_partner"]
    return "etherfi"


# =============================================================================
# AEO-4 — where the extractable answer sits, and what must never be rewritten
# =============================================================================

# Every page currently opens with a legal sentence, because the schema below
# demands "FTC-disclosure sentence first". That is correct compliance and it
# also means the first thing any extractor sees on 544 pages is boilerplate
# about our commissions rather than an answer to the question that was asked.
#
# Variant B moves the answer in front of the disclosure. The disclosure stays
# in the SAME opening paragraph, above the fold, which is what 16 CFR 255
# requires — it does not require being the first sentence. This is an
# experiment, not a belief: Perplexity documents scoring sub-document units
# individually, OpenAI documents nothing, so the split is 50/50 and the two
# cohorts are compared on fetches-per-page after eight weeks.
INTRO_VARIANTS = ("disclosure_first", "answer_first")

DISCLOSURE_SENTENCE = (
    "DefyCard publishes affiliate-linked reviews; we may earn a commission "
    "when you sign up through our links."
)
# Substring the gate greps for. Short enough to survive the writer rephrasing
# around it, specific enough that no ordinary sentence contains it.
DISCLOSURE_MARKER = "may earn a commission"

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def pick_intro_variant(slug: str) -> str:
    """Deterministic 50/50 by slug.

    Deterministic rather than random so that regenerating a page keeps it in
    the same cohort — otherwise the eight-week comparison is between two
    populations that quietly swap members.
    """
    import hashlib
    h = hashlib.sha256(slug.encode("utf-8")).digest()[0]
    return INTRO_VARIANTS[h & 1]


def apply_intro_variant(intro: str, variant: str) -> str:
    """Return the intro with the disclosure guaranteed present and correctly placed.

    The writer is a language model and will sometimes drop or move the
    disclosure. Compliance is not left to it: this function repairs the text,
    and `validate_intro` then refuses anything it could not repair.
    """
    intro = (intro or "").strip()
    if not intro:
        return intro
    sentences = _SENTENCE_SPLIT_RE.split(intro)
    has = any(DISCLOSURE_MARKER in s for s in sentences)

    if variant == "disclosure_first":
        if has and DISCLOSURE_MARKER in sentences[0]:
            return intro
        rest = [s for s in sentences if DISCLOSURE_MARKER not in s]
        return " ".join([DISCLOSURE_SENTENCE] + rest).strip()

    # answer_first: the answer leads, the disclosure follows immediately.
    rest = [s for s in sentences if DISCLOSURE_MARKER not in s]
    if not rest:
        # Nothing but the disclosure — there is no answer to put first.
        return intro
    return " ".join([rest[0], DISCLOSURE_SENTENCE] + rest[1:]).strip()


class IntroComplianceError(RuntimeError):
    """The opening paragraph would ship without a usable FTC disclosure."""


def validate_intro(intro: str, variant: str) -> None:
    """Both variants: the disclosure is in the opening paragraph. Variant B
    additionally requires that it is NOT the first sentence (otherwise the
    experiment is not running, it is just relabelled)."""
    if DISCLOSURE_MARKER not in (intro or ""):
        raise IntroComplianceError(
            f"intro_variant={variant}: no FTC disclosure in the opening paragraph")
    sentences = _SENTENCE_SPLIT_RE.split(intro.strip())
    first_has = DISCLOSURE_MARKER in sentences[0]
    if variant == "disclosure_first" and not first_has:
        raise IntroComplianceError("intro_variant=disclosure_first: disclosure is not first")
    if variant == "answer_first" and first_has:
        raise IntroComplianceError(
            "intro_variant=answer_first: disclosure is still the first sentence")


# ---------------------------------------------------------------------------
# Frozen pages
# ---------------------------------------------------------------------------
# Every pSEO URL that has ever produced a register_click, read live from GA4 on
# 2026-08-11 (21 converting URLs, of which `/` is the homepage and is not
# generated here). These 20 are the entire measured revenue surface of the
# site. No regeneration touches them — not by --full-rebuild, not by
# --upgrade-thin, not by a future migration. A rewrite that improves a metric
# on 524 pages and breaks one of these is a net loss, and we would not find
# out for weeks.
#
# Unfreezing is a deliberate edit to this literal list, by a human, with a
# reason. That is the point of it being a literal list.
FROZEN_SLUGS = frozenset({
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


class FrozenPageError(RuntimeError):
    """Attempt to regenerate a page that is known to convert."""


def assert_not_frozen(slug: str) -> None:
    if slug in FROZEN_SLUGS:
        raise FrozenPageError(
            f"{slug!r} is a converting page (GA4 register_click, measured "
            f"2026-08-11) and is frozen against regeneration. Remove it from "
            f"FROZEN_SLUGS by hand if you really mean to rewrite it.")


_BUTTON_AS_TARGET = re.compile(r"\]\(\s*<<AFFILIATE_BUTTON>>\s*\)")
_BUTTON_BARE = re.compile(r"<<AFFILIATE_BUTTON>>")


def normalize_placeholders(md: str) -> tuple[str, int]:
    """A block placeholder used as a link target becomes the URL placeholder.

    Runs BEFORE substitute_placeholders, because that one expands the button
    into HTML with a blind `str.replace` and would otherwise plant a
    `<p class="article-cta">` block inside `](...)` — which is precisely the
    artifact the operator found on the first page of the FAQ stream.
    """
    md, n = _BUTTON_AS_TARGET.subn("](<<AFFILIATE_URL>>)", md)
    return md, n


def normalize_field(text: str) -> tuple[str, int]:
    """Frontmatter fields get no <<IMAGE>>/<<BUTTON>> substitution at all, so a
    button placeholder there reaches the reader raw. Measured: 424 built pages
    served a literal `<<AFFILIATE_BUTTON>>`."""
    if not isinstance(text, str) or "<<AFFILIATE_BUTTON>>" not in text:
        return text, 0
    text, n1 = _BUTTON_AS_TARGET.subn("](<<AFFILIATE_URL>>)", text)
    # A link inside a link is not a link. If the placeholder stands inside an
    # unclosed `[`, emit plain text instead — 19 pages shipped as literal
    # characters because this substitution ran inside a label.
    def _bare(m):
        before = text[:m.start()]
        inside_label = before.count("[") > before.count("]")
        return "Get your DefyCard" if inside_label else \
            "[Get your DefyCard](<<AFFILIATE_URL>>)"

    text, n2 = _BUTTON_BARE.subn(_bare, text)
    return text, n1 + n2


def substitute_placeholders(body_md: str, images: list[UnsplashImage]) -> str:
    """Replace <<IMAGE_N>> + <<AFFILIATE_BUTTON>> with final HTML.

    NOTE per write-article skill §1.5 + councilreview 2026-05-05:
    `<<AFFILIATE_URL>>` is intentionally LEFT IN PLACE. A post-build sed pass
    in scripts/deploy.sh substitutes the placeholder so the affiliate URL
    stays a single env-var instead of baked into 3,200 .md files.

    rel="sponsored noopener" is also intentionally NOT added in Python — the
    Astro layout's AffiliateButton component owns rel attribution; doubling
    leaks "rel sponsored noopener sponsored noopener" through Astro renders.
    """
    import html as _html

    def img_repl(m: re.Match) -> str:
        idx = int(m.group(1))
        if idx >= len(images):
            return ""
        img = images[idx]
        # Validate src — only Unsplash CDN allowed (defense vs API abuse)
        if not img.src.startswith("https://images.unsplash.com/"):
            return ""
        # html.escape() every API-sourced string. NO <figcaption> with photo
        # credit. Per operator policy 2026-05-07: visible "Photo by X on
        # Unsplash" links bleed traffic off-site. Unsplash license does not
        # require visible attribution — the UTM-tagged source URL satisfies
        # the referral-tracking ToS without exposing an exit link to readers.
        return (
            f'\n<figure class="article-inline-image">\n'
            f'  <img src="{_html.escape(img.src, quote=True)}" '
            f'alt="{_html.escape(img.alt, quote=True)}" '
            f'loading="lazy" decoding="async" />\n'
            f'</figure>\n'
        )

    body_md = IMG_PLACEHOLDER_RE.sub(img_repl, body_md)

    # Affiliate button — placeholder stays in body; deploy.sh sed replaces.
    # rel="sponsored noopener" is REQUIRED on inline body anchors. The
    # AffiliateButton.astro component (separate from generator output) emits
    # rel itself, but body-markdown anchors are rendered raw by Astro's
    # <Content /> — they get no automatic rel injection. Per Google link-scheme
    # policy, missing rel="sponsored" on paid links risks manual-action
    # penalties (council round 2 finding).
    # TWO newlines each side: an HTML block in CommonMark ends at a BLANK
    # line, and with a single newline it kept swallowing the markdown that
    # followed the button — emitting it to the reader as raw `**bold**` and
    # `[text](url)`.
    affiliate_button_html = (
        '\n\n<p class="article-cta">\n'
        '  <a class="cta cta--primary" href="<<AFFILIATE_URL>>" '
        'rel="sponsored noopener" target="_blank">Get your DefyCard →</a>\n'
        '</p>\n\n'
    )
    body_md = body_md.replace(AFFILIATE_BUTTON_PLACEHOLDER, affiliate_button_html)
    # ...and if the writer left the sentence running on the same line as the
    # placeholder, the closing </p> still needs its blank line or the HTML
    # block keeps swallowing what follows.
    body_md = re.sub(r"(</p>)\s+(?=\S)", r"\1\n\n", body_md)

    # <<AFFILIATE_URL>> intentionally NOT replaced here — see docstring above.
    return body_md


# =============================================================================
# AEO-3 — generation weights, measured rather than assumed
# =============================================================================
#
# nginx logs over 2026-07-28..08-11 record how many times answer-time AI agents
# actually fetched a page of each bucket, per page in that bucket:
#
#   hub 12.1 · reviews 10.8 · faq 8.0 · help 5.6 · news 4.8 · how-to 3.9
#   geo 2.9 · compare 2.9 · learn 2.8 · tools 1.7 · for 0.6
#
# `for` is simultaneously the least-read bucket and the second largest (79
# pages). `tools` is a calculator shell nobody cites. Both go to weight 0:
# generation stops, published pages stay. Everything else is produced in
# rough proportion to how much assistants read it.
#
# Weights are per URL SEGMENT, not per keyword-bank letter: buckets H and I
# both write into /learn/, and weighting them separately would silently give
# /learn/ double its share.
BUCKET_WEIGHTS = {
    "reviews": 4,
    "faq": 3,
    "hub": 2,
    "help": 2,
    "geo": 2,
    "how-to": 1,
    "learn": 1,
    "compare": 1,
    "tools": 0,
    "for": 0,
}

# The geo bucket's own priority. An honest "this card is not available where
# you live, here is what you can get instead" page is the most-cited and
# highest-converting object this site has produced (6 of 10 assistant-driven
# clicks landed on the India and Turkey pages). Jurisdictions the issuer does
# not list therefore jump the queue ahead of the ones where everything already
# works.
def _blocked_slugs() -> set[str]:
    """Slugs whose jurisdiction cannot get the card.

    The issuer moved to an allow-list, so there is no finite list of blocked
    countries to enumerate any more — the question is answered per slug,
    through the YAML's own slug->jurisdiction map. A slug with no mapping is
    NOT promoted: unknown is not the same as blocked, and guessing here would
    reorder the whole print queue on a slugify near-miss.
    """
    cfg = availability_lib.load(AVAILABILITY_PATH)
    return {slug for slug, j in availability_lib.slug_jurisdictions(cfg).items()
            if availability_lib.unavailable_for(j["name"], cfg)}


def geo_priority(slug: str, blocked: Optional[set[str]] = None) -> int:
    """0 = write this geo page first (jurisdiction where the card is blocked)."""
    if blocked is None:
        blocked = _blocked_slugs()
    return 0 if slug in blocked else 1


# The 14 brand tokens of keywords/SKILL.md section 3.1. "etherfi" is an alias of "ether.fi".
BRANDS = frozenset({"ether.fi", "crypto.com", "coinbase", "binance", "bybit", "gnosis",
                    "redotpay", "bleap", "nexo", "wirex", "metamask", "kast", "uphold", "gemini"})
_BRAND_RE = re.compile(
    r"(?<![a-z0-9])(" + "|".join(re.escape(b) for b in sorted(BRANDS) + ["etherfi"]) + r")(?![a-z0-9])")


def brand_of(keyword: str) -> Optional[str]:
    """First brand token in the keyword (leftmost), else None."""
    m = _BRAND_RE.search(keyword.lower())
    if not m:
        return None
    return "ether.fi" if m.group(1) == "etherfi" else m.group(1)


def _siblings(items: list[KeywordRow], i: int) -> list[KeywordRow]:
    """Two supporting rows for items[i]. A branded primary only takes rows of
    its own brand or brand-less rows: a Gemini page must never be briefed with
    "is crypto.com card worth it" (gemini-card-review wrote a Crypto.com review)."""
    primary = items[i]
    b = brand_of(primary.keyword)
    if b is None:
        sib = [items[j] for j in (i + 1, i + 2) if j < len(items)]
        if len(sib) < 2 and i >= 2:
            sib = (sib + items[:2])[:2]
        return sib

    def ok(r: KeywordRow) -> bool:
        return r is not primary and brand_of(r.keyword) in (None, b)

    sib = [r for r in items[i + 1:] if ok(r)][:2]
    if len(sib) < 2 and i >= 2:
        sib += [r for r in items[:2] if ok(r) and r not in sib]
        sib = sib[:2]
    if len(sib) < 2:   # pad with brand-less rows of the same segment
        sib += [r for r in items if ok(r) and brand_of(r.keyword) is None and r not in sib]
        sib = sib[:2]
    return sib


def build_clusters(rows: list[KeywordRow]) -> list[Cluster]:
    """One cluster per primary keyword, with 2 supporting from same bucket.

    Order: WEIGHTED interleave across URL segments (AEO-3). Each pass emits
    BUCKET_WEIGHTS[segment] clusters from that segment, so a segment with
    weight 4 is produced four times as often as one with weight 1 and a
    segment with weight 0 is never produced at all.

    This replaces a flat round-robin. Flat round-robin was itself a fix (an
    earlier scheme drained bucket A before starting B), and it did its job —
    every bucket now has pages. What it cannot do is notice that assistants
    read /reviews/ eighteen times more per page than /for/, and that we had
    kept producing /for/ pages anyway until it became the second-biggest
    bucket on the site.
    """
    by_segment: dict[str, list[KeywordRow]] = {}
    seg_pattern: dict[str, str] = {}
    for r in rows:
        if r.bucket not in BUCKET_META:
            continue
        segment, pattern = BUCKET_META[r.bucket]
        by_segment.setdefault(segment, []).append(r)
        seg_pattern.setdefault(segment, pattern)

    per_segment: dict[str, list[Cluster]] = {}
    for segment, items in by_segment.items():
        chain: list[Cluster] = []
        for i, primary in enumerate(items):
            if primary.supporting_only:
                continue
            siblings = _siblings(items, i)
            # The brand filter may leave fewer than 2 rows; it must never remove
            # a page from the queue, so the pre-filter emit condition decides.
            if len(siblings) < 2 and not (i >= 2 or i + 2 < len(items)):
                continue
            chain.append(Cluster(
                primary=primary,
                supporting=siblings,
                pattern=seg_pattern[segment],
                url_segment=segment,
            ))
        if segment == "geo":
            blocked = _blocked_slugs()
            chain.sort(key=lambda c: geo_priority(slugify(c.primary.keyword), blocked))
        if chain and BUCKET_WEIGHTS.get(segment, 1) > 0:
            per_segment[segment] = chain

    # Segment order follows BUCKET_META so output stays deterministic.
    segment_order: list[str] = []
    for letter in BUCKET_META:
        seg = BUCKET_META[letter][0]
        if seg in per_segment and seg not in segment_order:
            segment_order.append(seg)

    clusters: list[Cluster] = []
    cursors = {s: 0 for s in segment_order}
    while True:
        progressed = False
        for s in segment_order:
            for _ in range(BUCKET_WEIGHTS.get(s, 1)):
                i = cursors[s]
                if i >= len(per_segment[s]):
                    break
                clusters.append(per_segment[s][i])
                cursors[s] = i + 1
                progressed = True
        if not progressed:
            break
    return clusters


# =============================================================================
# Prompt construction (cached system block + per-page user message)
# =============================================================================

# The affiliate rows inside `.claude/skills/etherfi/SKILL.md` §1 are dated
# 2026-04-01 and four of them are wrong: payout currency "SCR", payout cadence
# "rolling, not batched", physical-card commission "10 %–25 %", and the
# nowhere-sourced "within 1–2 business days". ether.fi/affiliates (read
# 2026-09-14) says USDC on the 15th of the month and 20/20/20/25 %; the gitbook
# T&C still says SCR. Two issuer surfaces disagree on the payout, so it is
# DISPUTED and the writer must assert neither — while the physical-card
# commission, which both agree on, is simply corrected.
#
# This block is appended to the cached system prompt AFTER the skill excerpt so
# it is the last word on the same subject. It lives here as a literal and not in
# the skill file because the harness refuses every write under `.claude/`
# (PROGRESS.md#T-w0-05). When E-07a lands `config/facts/etherfi.yaml` +
# `facts_lib.render()`, this constant is replaced by that render call — one
# place, and this is it until then.
AFFILIATE_FACTS_BLOCK = """
## Affiliate-program facts — THESE OVERRIDE THE SKILL SECTION ABOVE (verified 2026-09-29)

The skill excerpt above was re-read 2026-09-29. Where it disagrees with this block,
this block wins. Breaking any rule here is a P0 failure.

- **Payout is settled** — paid in USDC to the affiliate's ether.fi Cash vault,
  monthly, on the 15th of the following month. These strings are BANNED from
  every field you produce:
  `SCR` · `paid in SCR` · `rolling payout` · `not batched`
  · `within 1-2 business days` · `24-48 hours` · `5th-10th of the month`
  Write, verbatim: `paid in USDC to your ether.fi Cash vault on the 15th of the
  following month (ether.fi affiliates page, verified 2026-09-29)`.
  The ONE legitimate use of the word "rolling" here is tier maintenance (a
  rolling referral count to hold a tier) — never a payout.
- Referral cashback: Tier 1 earns 0.1 % and Tier 4 earns 0.3 % of your referrals'
  spending, lifetime, no cap; see the allow-list for Tiers 2-3. An older Referral
  Program T&C states different terms; never print `1%`, `12 months` or `$1,000`
  as referral terms.
- **Physical-card commission is `20-25 % (tiered)`.** The skill's `10 %-25 %`
  is stale. `10-25 %` is BANNED.
- A cashback or commission claim with no number you can point at in this block
  is not a claim you may print. Say where the reader looks it up instead.
"""


def _redact_unbacked(text: str) -> str:
    """Replace every money token G36 would refuse with `[see allow-list]`, so a
    skill excerpt cannot teach the writer a number the gate then rejects."""
    bad = set(money_gate.unbacked(text))
    for v, u, a, b in reversed(money_gate.tokens(text)):
        if money_gate._fmt(v, u) in bad:
            text = text[:a] + "[see allow-list]" + text[b:]
    return text


def build_cached_system_prompt() -> str:
    """Concatenate skill content for prompt caching. Re-read each run so updates propagate."""
    parts = ["# DefyCard pSEO writer — cached context\n"]
    parts.append("You are the DefyCard SEO content writer. You produce 1200-2200-word "
                 "decision-grade affiliate articles for crypto-card readers, optimised "
                 "for 2026 Google E-E-A-T. Output STRICT JSON per the schema at the end. "
                 "Never write disclaimers about being an AI. Never break P0 rules below.\n")

    parts.append("\n## Brand: DefyCard (defycard.com)\n")
    parts.append("- Niche: crypto-card affiliate / comparison hub\n")
    parts.append("- Primary product: ether.fi Cash (referral handle @defycard)\n")
    parts.append("- Positioning: 'yield while spending', anti-bank, self-custody\n")
    parts.append("- Audience: global English crypto-card seekers (US/UK/EU/LATAM/APAC)\n")

    # Before the skill excerpt, inside the byte window (Y63): same lists the G37 linter reads.
    parts.append(humanize_gate.voice_block())

    parts.append("\n## Article-writing contract (P0 + P1 rules)\n")
    parts.append(_redact_unbacked(SKILL_WRITE_ARTICLE.read_text(encoding="utf-8")[:14000]))

    parts.append("\n## EtherFI facts (verbatim from issuer; cite where relevant)\n")
    parts.append(_redact_unbacked(SKILL_ETHERFI.read_text(encoding="utf-8")[:7000]))
    # MUST come after the skill excerpt: it contradicts four rows inside it.
    parts.append(AFFILIATE_FACTS_BLOCK)
    # Competitor facts the G36 allow-list already permits: without them the writer
    # cannot see them and invents the product (gemini page called a WebBank credit card custodial).
    _cf = [f for f in facts_lib.load_all()
           if f["status"] == "current" and (f.get("applies_to") or {}).get("brand") in ("gemini", "coinbase")]
    if _cf:
        parts.append("\n## Competitor facts (issuer quotes; the ONLY source for Gemini/Coinbase claims)\n")
        for f in _cf:
            at = f["applies_to"]
            parts.append(f"- [{at['brand']} {at.get('product', '')}] {f['id']} = {f['value']} {f.get('unit') or ''}"
                         f" — \"{f['source_quote']}\"\n")
        parts.append("Describe a competitor ONLY from these rows; never infer custody, issuer or rates not listed here.\n")

    # ONE allow-list: the same function G36 checks against (money_gate).
    parts.append("\n## Money numbers you may print (G36 allow-list)\n"
                 "The ONLY numbers with a money unit you may print; anything else is "
                 "refused unread, whatever the skill excerpt above says. Where you "
                 "have no number, say where the reader looks it up. A tier- or "
                 "currency-specific figure must sit next to its own tier or currency.\n"
                 + ", ".join(money_gate.allowed_tokens()) + "\n")

    parts.append("\n## Competitor & marketing context (excerpts)\n")
    parts.append(_redact_unbacked(SKILL_COMPETITOR.read_text(encoding="utf-8")[:5000]))

    parts.append("\n## Mandatory placeholders in body_markdown\n")
    parts.append("- `<<AFFILIATE_URL>>` — text-link target (Astro substitutes at build time)\n")
    parts.append("- `<<AFFILIATE_BUTTON>>` — full-width CTA button (renders as <AffiliateButton>)\n")
    parts.append("- **`<<AFFILIATE_BUTTON>>` stands ALONE on its own line. It is a block, not a "
                 "URL — never `[text](<<AFFILIATE_BUTTON>>)`. The only placeholder allowed inside "
                 "`](...)` is `<<AFFILIATE_URL>>`.**\n")
    parts.append("- `<<IMAGE_0>>`, `<<IMAGE_1>>`, `<<IMAGE_2>>` — image slots (substituted post-generation)\n")
    parts.append("- `body_markdown` is pure Markdown: NO HTML tags of any kind (no <aside>, <details>, "
                 "<p>, <div>, <strong>). The template wraps sections itself; the 'Renders as' column "
                 "in the contract above describes the template's output, not yours.\n")
    parts.append("- NEVER paste `https://www.ether.fi/@defycard` or any literal affiliate URL.\n")

    parts.append("\n## English-only (Phase 1)\n")
    parts.append("- ALL output is English. Title, meta description, H1, body, FAQ, snapshot bullets, "
                 "image_queries, schema fields — every string is English.\n")
    parts.append("- No Russian, Spanish, Portuguese, German, etc. in any field.\n")
    parts.append("- US English spelling preferred ('color' not 'colour'), but UK spelling acceptable for UK-specific pages.\n")
    # Bucket-agnostic here BY NECESSITY: this prompt is built once and cached
    # for every page, so it cannot depend on a cluster. The per-bucket shortlist
    # lives in build_user_message, which is built per article.
    _cands = sorted(existing_paths())
    _pool = _cands[:40]
    parts.append("- Internal linking strategy (per write-article skill §13): include 4-7 inline markdown "
                 "links in body_markdown, chosen ONLY from the Internal link candidates listed below. "
                 "NEVER invent a path: links to paths outside this list are stripped by the validator "
                 "before publication — an invented link is a wasted link. Anchor text MUST vary (not "
                 "always 'click here'). Populate `related_clusters` with 4-8 slugs from the same list.\n")
    parts.append("\n### Internal link candidates (these exist; nothing else does)\n")
    parts.append("\n".join(f"- {c}" for c in _pool) + "\n")

    parts.append("\n## Output JSON schema\n")
    parts.append("""```json
{
  "title": "string, <=60 chars, primary keyword present",
  "meta_description": "string, 140-160 chars, primary keyword + benefit",
  "h1": "string, equals or close to title",
  "intro_paragraph": "string, 60-100 words, FTC-disclosure sentence first, primary keyword in first 50 words, ends with text link [...](<<AFFILIATE_URL>>)",
  "snapshot_bullets": ["**Cashback:** up to 3 %", "**FX fee:** 0 % USD/EUR, 1 % other", "**Issuance fee:** ...", "**Custody:** ...", "**Country availability:** ...", "**Last verified:** YYYY-MM-DD"],
  "body_markdown": "1200-2200 words of GitHub-flavoured Markdown using bucket-specific structure. NO markdown tables. >=1 callout (**Signal:**, **Risk:**, **Key metric:**, **Why it matters:**, **Watch:**, **Alternative:**) per H2 deep-dive section. Use <<IMAGE_0>>, <<IMAGE_1>>, <<IMAGE_2>> at safe section breaks. Use <<AFFILIATE_BUTTON>> at least twice (mid-article and bottom-line). Use <<AFFILIATE_URL>> for inline text links.",
  "long_context_paragraph": "string, 400-600 unique words distinct from body_markdown",
  "what_to_watch": ["3-5 observable, time-bound, source-bound bullets"],
  "bottom_line": ["3-4 wrap-up bullets including 'If you fit X profile, this card pays you back'"],
  "faq": [{"q": "string", "a": "40-80 word answer"}],
  "related_clusters": ["3-5 internal-link slugs (just slug, no URL)"],
  "image_queries": ["query 1 (max 40 chars, 2-3 generic keywords like 'contactless payment')", "query 2", "query 3"],
  "schema_article": {"@context": "https://schema.org", "@type": "Article", "headline": "...", "datePublished": "ISO-8601", "author": {"@type": "Organization", "name": "DefyCard", "url": "https://defycard.com"}},
  "schema_faq": {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": "...", "acceptedAnswer": {"@type": "Answer", "text": "..."}}]}
}
```
Return ONE JSON object. No prose outside the JSON. No code fences.
""")
    return "".join(parts)


INTRO_BRIEF = {
    "disclosure_first": (
        "intro_paragraph: open with the FTC affiliate-disclosure sentence, then "
        "answer the primary keyword."),
    "answer_first": (
        "intro_paragraph: the FIRST one or two sentences must answer the primary "
        "keyword directly and concretely — a number, a yes/no, or a name — with no "
        "preamble. Put the FTC affiliate-disclosure sentence IMMEDIATELY AFTER "
        "that answer, still inside this same opening paragraph. Do not open with "
        "the disclosure."),
}


def _bucket_links(cluster: Cluster, limit: int = 20) -> str:
    """Existing paths in this page's own section, which is where a genuinely
    relevant internal link comes from."""
    seg = cluster.url_segment
    cands = sorted(existing_paths())
    same = [c for c in cands if c.startswith(f"/{seg}/")][:limit]
    return "\n".join(f"  - {c}" for c in (same or cands[:limit]))


def build_user_message(cluster: Cluster) -> str:
    """Per-page brief — never cached."""
    today = datetime.now(timezone.utc).date().isoformat()
    variant = pick_intro_variant(slugify(cluster.primary.keyword))
    return f"""Generate a DefyCard pSEO article for ONE keyword cluster.

Bucket: {cluster.primary.bucket} — {cluster.primary.bucket_name}
Page pattern: {cluster.pattern}
URL: /{cluster.url_segment}/{slugify(cluster.primary.keyword)}/

Intro variant for this page ({variant}) — overrides the schema's intro rule:
  {INTRO_BRIEF[variant]}

Primary keyword (in title, H1, first 100 words, body H2):
  "{cluster.primary.keyword}"

Supporting keywords (each appears ≥2× in body):
{chr(10).join(f'  - "{r.keyword}"' for r in cluster.supporting) or '  (none)'}

Internal link candidates for THIS page's section (these exist; nothing else
does — a link outside this list is stripped before publication):
{_bucket_links(cluster)}

Today's date for `datePublished`: {today}

Write the article now as the JSON object specified by the schema. Do not include
any prose outside the JSON. Do not include markdown code fences around the JSON."""


# =============================================================================
# Claude CLI bridge (Max-plan friendly — no API key needed)
# =============================================================================


@dataclass
class GenerationResult:
    cluster: Cluster
    payload: dict
    usage: dict
    cost_usd: float
    elapsed_s: float
    error: Optional[str] = None


import subprocess


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


_HTML_TAG_RE = re.compile(r"(?<!<)</?[A-Za-z][^<>]*>")  # lookbehind: never eat one bracket of <<AFFILIATE_URL>>
_TAG_LINE_RE = re.compile(r"^[ \t]*(?:(?<!<)</?[A-Za-z][^<>]*>[ \t]*)+\n?", re.M)
_SUMMARY_RE = re.compile(r"<summary>\s*(?:<strong>|<b>)?(.*?)(?:</strong>|</b>)?\s*</summary>[ \t]*\n?", re.S)


def strip_html_wrappers(md: str) -> tuple[str, int]:
    """-> (md, n_tags_removed). <summary>Q</summary> becomes a **Q** paragraph; a line that is
    only tags is deleted whole so blank-line structure survives; any inline tag is dropped."""
    n = len(_HTML_TAG_RE.findall(md))
    if not n:
        return md, 0
    md = _SUMMARY_RE.sub(lambda m: f"**{m.group(1).strip()}**\n\n", md)
    md = _TAG_LINE_RE.sub("", md)
    md = _HTML_TAG_RE.sub("", md)
    return md, n


CLAUDE_BIN = "/usr/bin/claude"
# The ONE argv for every `claude --print` JSON caller (intel/news/council import
# call_claude_cli). `--tools ""`: with --max-turns 1 a tool call ends the process
# with exit 1 (CCH-WRITER-01); a writer has nothing to do with tools.
CLI_BASE_ARGS = ["--print", "--tools", "", "--max-turns", "1", "--output-format", "json",
                 "--disable-slash-commands", "--exclude-dynamic-system-prompt-sections",
                 "--no-session-persistence"]


def call_claude_cli(
    system_prompt: str,
    user_message: str,
    model: str = DEFAULT_MODEL,
    timeout_s: int = 240,
) -> tuple[str, dict, float]:
    """Invoke Claude Code CLI in --print mode (Max plan, no API key).

    Implementation note: large prompts go via tempfile + stdin, NOT argv.
    Passing >2 KB on argv made the CLI hang in initial testing
    (15+ minutes with no progress on a 14 KB system prompt).

    Returns (raw_text_response, usage_dict, total_cost_usd).
    Raises RuntimeError on subprocess failure.
    """
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as sp_file:
        sp_file.write(system_prompt)
        sp_path = sp_file.name
    try:
        # No prompt argv — read from stdin
        cmd = [CLAUDE_BIN, *CLI_BASE_ARGS, "--model", model, "--append-system-prompt-file", sp_path]
        proc = subprocess.run(
            cmd,
            input=user_message,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        if proc.returncode != 0:
            # The reason lives in stdout JSON (--output-format json), not stderr.
            rc = proc.returncode
            try:
                d = json.loads(proc.stdout)
            except ValueError:
                raise RuntimeError(
                    f"claude CLI exit {rc}: stdout={proc.stdout[:300]!r} stderr={proc.stderr[:300]!r}"
                )
            raise RuntimeError(
                f"claude CLI exit {rc}: subtype={d.get('subtype')!r} is_error={d.get('is_error')!r} "
                f"num_turns={d.get('num_turns')!r} result={str(d.get('result'))[:300]!r}"
            )
        cli_data = json.loads(proc.stdout)
        if cli_data.get("is_error"):
            raise RuntimeError(f"claude CLI is_error: {cli_data.get('result', '')[:300]}")
        text = cli_data.get("result", "") or ""
        if not text.strip():
            # Empty result = Claude refused / gave no content. Loud failure
            # beats the misleading "JSONDecodeError: Expecting value line 1
            # column 1" we previously saw on /home/cryptocardhub/logs/pseo,
            # which made it look like a JSON parse bug when it was actually
            # an empty model response.
            raise RuntimeError(
                f"claude CLI returned empty result (no model output); "
                f"stop_reason={cli_data.get('stop_reason')!r} "
                f"model={cli_data.get('model')!r}"
            )
        usage = cli_data.get("usage") or {}
        cost_usd = float(cli_data.get("total_cost_usd") or 0.0)
        return text, usage, cost_usd
    finally:
        try:
            os.unlink(sp_path)
        except OSError:
            pass


def generate_page(
    cached_system: str,
    cluster: Cluster,
    model: str = DEFAULT_MODEL,
) -> GenerationResult:
    t0 = time.time()
    try:
        raw_text, usage, cost_usd = call_claude_cli(
            system_prompt=cached_system,
            user_message=build_user_message(cluster),
            model=model,
        )
        # Some Haiku responses still wrap in ```json fences despite instructions
        text = _strip_code_fences(raw_text)
        payload = json.loads(text)
        return GenerationResult(
            cluster=cluster,
            payload=payload,
            usage=usage,
            cost_usd=cost_usd,
            elapsed_s=time.time() - t0,
        )
    except Exception as e:
        return GenerationResult(
            cluster=cluster,
            payload={},
            usage={},
            cost_usd=0.0,
            elapsed_s=time.time() - t0,
            error=f"{type(e).__name__}: {e}",
        )


# =============================================================================
# Output writer (Astro content collection .md file)
# =============================================================================


def would_overwrite(cluster: Cluster, content_dir: Optional[Path] = None) -> Optional[Path]:
    """The path this cluster would write to, IF that path already exists.

    Called before generation, not just before the write — the answer is
    known for free, and 28 straight faq-children runs paid Haiku + Unsplash
    to find it out anyway because the queue thought the page did not exist
    (LAW #ONE-PLACE: two slugify() implementations disagreed on where the
    file already was). `content_dir` is injectable for tests only.
    """
    cd = content_dir if content_dir is not None else CONTENT_DIR
    target = cd / f"{slugify(cluster.primary.keyword)}.md"
    return target if target.exists() else None


_TEMPLATE_H2 = {
    "what to watch": "what_to_watch",
    "bottom line": "bottom_line",
    "the bottom line": "bottom_line",
    "faq": "faq",
    "frequently asked questions": "faq",
}
_H2_RE = re.compile(r"^## .*$", re.M)
# Page-level compliance copy is not template content. Shape, audited over the whole
# pre-strip snapshot (19 pages, 2026-10-19): a paragraph that opens with a bold
# disclosure label or states the affiliate/volatility notice outright.
_RISK_RE = re.compile(
    r"^(?:\*\*(?:Risk|Disclosure|Disclaimer|Important|Country eligibility|Last verified|Warning)\b"
    r"|\**DefyCard (?:publishes|earns)|\**Crypto assets are volatile)", re.M | re.I)
_FIGURE_RE = re.compile(r"<figure\b.*?</figure>", re.S)
_IMG_SRC_RE = re.compile(r'<img\b[^>]*\bsrc="([^"]+)"')
_RULE_TAIL_RE = re.compile(r"(?:\n[ \t]*)*---[ \t]*\n*\Z")


def strip_template_sections(body_md: str, payload: dict) -> tuple[str, int]:
    """Drop body H2 sections the template already renders from frontmatter.

    [slug].astro (and build-markdown.js for the sidecar) render what_to_watch /
    bottom_line / faq from fields; a body H2 of the same name shows the reader
    the section twice (428 of 578 pages, 2026-10-19). A section goes only when
    its payload field is non-empty. Every other H2 and a bold
    `**Risk & Disclosure` paragraph ending the block are untouched. -> (body, n_removed)."""
    heads = list(_H2_RE.finditer(body_md))
    cuts = []
    for i, m in enumerate(heads):
        key = m.group(0)[3:].strip().rstrip(" \t:.?!").lower()
        field = _TEMPLATE_H2.get(key)
        if field and payload.get(field):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(body_md)
            risk = _RISK_RE.search(body_md, m.end(), end)
            if risk:  # the page's own disclosure paragraph is not template content
                end = risk.start()
            cuts.append((m.start(), end))
    if not cuts:
        return body_md, 0
    out, pos = [], 0
    for start, end in cuts:
        out.append(body_md[pos:start])
        pos = end
        # an inline image that appears nowhere else on the page is not a duplicate
        rest = body_md[:start] + body_md[end:]
        for fig in _FIGURE_RE.findall(body_md[start:end]):
            src = _IMG_SRC_RE.search(fig)
            if src and src.group(1) not in rest:
                out.append(fig + "\n\n")
        # the block's closing rule stays only when it is the sole separator
        tail = _RULE_TAIL_RE.search(body_md[start:end])
        if tail and not "".join(out).rstrip().endswith("---") and end < len(body_md):
            out.append("---\n\n")
    out.append(body_md[pos:])
    res = "".join(out)
    if pos == len(body_md):  # removed block was last: no dangling rule
        res = re.sub(r"\n*---[ \t]*\n*\Z", "\n", res.rstrip() + "\n")
        res = res.rstrip() + "\n"
    return res, len(cuts)


def emit_frontmatter(fm: dict) -> str:
    """The ONE frontmatter serializer (write_page and humanize-backfill).

    One `key: <JSON>` line per key, in dict order, delimiters included, no
    trailing newline. Law: yaml.safe_load(inner) == fm. YAML timestamps that a
    reader parsed into datetime/date go back out as bare ISO scalars."""
    def scalar(v):
        if isinstance(v, _dt.date):  # datetime is a subclass
            return v.isoformat()
        return json.dumps(v)
    return "\n".join(["---", *(f"{k}: {scalar(v)}" for k, v in fm.items()), "---"])


def write_page(result: GenerationResult, images: list[UnsplashImage]) -> Path:
    p = result.payload
    cluster = result.cluster
    slug = slugify(cluster.primary.keyword)
    # AEO-4: last line of defence. build_queue already drops frozen slugs, but
    # write_page is the only place a file is actually overwritten, so the guard
    # belongs here too — every caller routes through it.
    assert_not_frozen(slug)
    target = CONTENT_DIR / f"{slug}.md"
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)

    # Localize images: download Unsplash CDN -> public/assets/pseo/<slug>-<n>.jpg,
    # mutate img.src to /assets/pseo/<...>. Operator policy: no off-site
    # <img src>; visitors never make a third-party request.
    images = localize_images(images, slug)

    # Substitute <<IMAGE_X>> + <<AFFILIATE_BUTTON>> in body markdown.
    # <<AFFILIATE_URL>> placeholder is INTENTIONALLY LEFT IN PLACE — substituted
    # at render time by [bucket]/[slug].astro and at build time by deploy.sh's
    # sed pass. Single source of truth for the affiliate URL stays in .env, so
    # changing the URL only requires a re-deploy (no re-generation needed).
    _existing = existing_paths()
    _body_raw, _n_html = strip_html_wrappers(p["body_markdown"])
    if _n_html:
        print(json.dumps({"event": "html_wrappers_stripped", "slug": slug, "n": _n_html}))
    _body_raw, _btn_fixed = normalize_placeholders(_body_raw)
    body_md = substitute_placeholders(_body_raw, images)
    body_md, _n_stripped = strip_template_sections(body_md, p)
    if _n_stripped:
        print(json.dumps({"event": "template_sections_stripped", "slug": slug,
                          "n": _n_stripped}, ensure_ascii=False))
    body_md, _links_dropped = validate_internal_links(body_md, _existing)

    # The five surfaces the template renders besides the body. The schema
    # itself demands a link in intro_paragraph, so "the body is the only place
    # with links" was never true.
    _field_fixed = 0
    for _key in ("intro_paragraph", "long_context_paragraph"):
        if isinstance(p.get(_key), str):
            p[_key], _n = normalize_field(p[_key])
            p[_key], _d = validate_internal_links(p[_key], _existing)
            _field_fixed += _n
            _links_dropped += _d
    for _key in ("snapshot_bullets", "what_to_watch", "bottom_line"):
        if isinstance(p.get(_key), list):
            _new = []
            for _item in p[_key]:
                if isinstance(_item, str):
                    _item, _n = normalize_field(_item)
                    _item, _d = validate_internal_links(_item, _existing)
                    _field_fixed += _n
                    _links_dropped += _d
                _new.append(_item)
            p[_key] = _new
    if isinstance(p.get("faq"), list):
        for _qa in p["faq"]:
            if isinstance(_qa, dict) and isinstance(_qa.get("a"), str):
                _qa["a"], _n = normalize_field(_qa["a"])
                _qa["a"], _d = validate_internal_links(_qa["a"], _existing)
                _field_fixed += _n
                _links_dropped += _d
    if _btn_fixed or _field_fixed:
        print(json.dumps({"event": "placeholder_normalised",
                          "button_as_target_in_body": _btn_fixed,
                          "button_in_frontmatter_fields": _field_fixed},
                         ensure_ascii=False))
    if _links_dropped:
        print(json.dumps({"event": "internal_links_dropped", "slug": slug,
                          "links_dropped": _links_dropped}, ensure_ascii=False))
    # Intro paragraph contains a text-link to <<AFFILIATE_URL>> — keep the
    # placeholder for the same reason as above.
    # AEO-4: place the FTC disclosure per this page's cohort, then refuse to
    # write anything that still lacks it.
    intro_variant = pick_intro_variant(slug)
    intro = apply_intro_variant(p.get("intro_paragraph", ""), intro_variant)
    validate_intro(intro, intro_variant)

    # Image metadata for frontmatter (consumed by [bucket]/[slug].astro)
    images_fm = [
        {
            "src": img.src,
            "alt": img.alt,
            "credit_html": img.credit_html,
            "query": img.query,
        }
        for img in images
    ]

    # YAML frontmatter — Astro content collection consumes this
    fm = {
        "title": p["title"],
        "description": p["meta_description"],
        "h1": p["h1"],
        "slug": slug,
        "bucket": BUCKET_META[cluster.primary.bucket][0],
        "primary_partner": primary_partner_for(slug),
        "primary_keyword": cluster.primary.keyword,
        "supporting_keywords": [s.keyword for s in cluster.supporting],
        "intro_paragraph": intro,
        "intro_variant": intro_variant,
        "snapshot_bullets": p.get("snapshot_bullets", []),
        "long_context_paragraph": p.get("long_context_paragraph", ""),
        "what_to_watch": p.get("what_to_watch", p.get("stakes_bullets", [])),
        "bottom_line": p.get("bottom_line", []),
        "faq": p.get("faq", []),
        "related_clusters": p.get("related_clusters", []),
        "images": images_fm,
        "schema_article": p.get("schema_article", {}),
        "schema_faq": p.get("schema_faq", {}),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": DEFAULT_MODEL,
        "pattern": cluster.pattern,
    }
    fm_lines = [emit_frontmatter(fm), "", body_md, ""]
    target.write_text("\n".join(fm_lines), encoding="utf-8")
    return target


# =============================================================================
# Logging
# =============================================================================


def _emit(row: dict) -> None:
    """Print a pipeline event AND append it to logs/pseo/<utc date>.jsonl.

    A refusal printed to stdout alone is lost with the cron run (KW-04b: the
    gemini G36 refusal of 14:14Z could never be diagnosed). LOG_DIR is read at
    CALL time, same rule as log_usage."""
    row.setdefault("ts", datetime.now(timezone.utc).isoformat())
    line = json.dumps(row, ensure_ascii=False)
    print(line)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    with (LOG_DIR / f"{today}.jsonl").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _g36_context(text: str, unbacked: list[str]) -> list[str]:
    """The +-60-char window around each unbacked token (max 5, 160 chars each)."""
    text = money_gate._JSON_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)
    out: list[str] = []
    for value, unit, start, end in money_gate.tokens(text):
        if money_gate._fmt(value, unit) in unbacked and len(out) < 5:
            out.append(text[max(0, start - 60):end + 60][:160])
    return out


_BANK_REFUSAL_COOLDOWN_H = 24     # one Haiku call per refusal: one price for every cause


def _bank_refused_path() -> Path:
    return FAQ_LEDGER.with_name("bank-refused.json")


def _mark_bank_refused(slug: str) -> None:
    path = _bank_refused_path()
    led = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    led[slug] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(led, indent=1) + "\n", encoding="utf-8")


def _bank_refused_recently() -> set[str]:
    path = _bank_refused_path()
    if not path.exists():
        return set()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=_BANK_REFUSAL_COOLDOWN_H)
    return {slug for slug, ts in json.loads(path.read_text(encoding="utf-8")).items()
            if datetime.fromisoformat(ts) > cutoff}


def log_usage(result: GenerationResult, log_dir: Path | None = None) -> None:
    # `log_dir` resolves against the module global at CALL time, not def
    # time: a `Path = LOG_DIR` default binds the value once at import and a
    # test's `monkeypatch.setattr(pg, "LOG_DIR", tmp_path)` silently stops
    # redirecting it, leaking fixture rows into the real production log
    # `weekly-report.py` and the `*/30` cron both read (found in E-11
    # ruling-1 review: 4 real rows appended by a single test run).
    if log_dir is None:
        log_dir = LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    log_file = log_dir / f"{today}.jsonl"
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "slug": slugify(result.cluster.primary.keyword),
        "bucket": result.cluster.primary.bucket,
        "primary": result.cluster.primary.keyword,
        "model": DEFAULT_MODEL,
        "usage": result.usage,
        "cost_usd": round(result.cost_usd, 6),
        "elapsed_s": round(result.elapsed_s, 2),
        "error": result.error,
    }
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# =============================================================================
# Queue building
# =============================================================================


def existing_paths() -> set[str]:
    """`/bucket/slug/` for every page that actually exists on disk.

    `existing_slugs()` returns stems only, which cannot answer "does
    /reviews/ether-fi-cash/ exist" — and that question is the whole defect.
    """
    out: set[str] = set()
    for f in CONTENT_DIR.glob("*.md"):
        bucket = ""
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines()[:40]:
            if line.startswith("bucket:"):
                bucket = line.split(":", 1)[1].strip().strip('"\'')
                break
        if bucket:
            out.add(f"/{bucket}/{f.stem}/")

    # Bucket indexes are a DYNAMIC route (`src/pages/[bucket]/index.astro`), so
    # they cannot be discovered by walking filenames — and counting only content
    # entries would have stripped 14 live links to /compare/ and every other
    # bucket index. A validator that deletes working links is worse than the
    # defect it fixes, so the segments come from BUCKET_META, the same source
    # the generator uses to build page URLs.
    for _seg, *_ in BUCKET_META.values():
        out.add(f"/{_seg}/")
    out.add("/videos/")  # src/pages/videos/[...page].astro, likewise dynamic

    pages_dir = ROOT / "seo-site" / "src" / "pages"
    if pages_dir.is_dir():
        for f in pages_dir.rglob("*.astro"):
            name = f.stem
            if name.startswith("[") or name.startswith("_"):
                continue
            rel = f.relative_to(pages_dir).parent.as_posix()
            seg = "" if rel == "." else f"{rel}/"
            out.add("/" if (seg == "" and name == "index") else
                    (f"/{seg}" if name == "index" else f"/{seg}{name}/"))
    return out


# The anchor may not contain `[` or a newline. Without those guards the class
# spans structure: applied to a whole file, `[^\]]+` swallowed a YAML array's
# opening bracket and everything up to the first markdown link inside it,
# silently turning `key: ["a", "b"]` into `key: "a", "b"]`. That cost five
# broken pages before a build caught it.
# `<...>` is a legal Markdown destination form and the writer uses it, so a
# pattern that only accepts the bare form leaves those links unchecked — five
# survived the first repair sweep for exactly that reason.
# The trailing slash is optional in what the writer emits, and a pattern that
# demands it silently skips `/how-to/get-a-crypto-card` — two such links
# survived every earlier sweep and only G20 caught them.
_INTERNAL_HREF = re.compile(r"\[([^\[\]\n]+)\]\(<?(/[a-z0-9\-/]*?/?)>?\)")


# Any markdown link at all — the allow-list pass needs to SEE every href,
# including the malformed ones, which is precisely what the path-shaped pattern
# above cannot do.
_ANY_HREF = re.compile(r"\[([^\[\]\n]+)\]\(([^)\n]*)\)")
# `<?...>?` was WRONG here: it ate one angle bracket of `<<AFFILIATE_URL>>`,
# leaving `<AFFILIATE_URL>>`, which then failed the placeholder check and got
# stripped — the affiliate link, on every page the stream would produce.
# ONLY the affiliate URL may stand in an href. `<<AFFILIATE_BUTTON>>` is a
# block component and `<<IMAGE_0>>` is a picture; both were accepted by the
# previous `^<<[A-Z0-9_]+>>$`, which is how a CTA block ended up inside a link
# target on 135 pages.
_PLACEHOLDER = re.compile(r"^<<AFFILIATE_URL(?::[a-z0-9_-]+)?>>$")


def validate_internal_links(body_md: str, existing: set[str]) -> tuple[str, int]:
    """Strip inline links whose target does not exist; keep the anchor text.

    The writer prompt used to hand the model three example targets — none of
    which existed — and reassure it that Astro safely drops links to
    not-yet-published pages so they could not 404. That reassurance was true about
    `related_clusters` (RelatedLinks.astro filters them through the collection)
    and false about body_markdown, which goes to a raw <a href>. 117 dead
    targets and 218 dead links later, the fix is not a better prompt: a prompt
    supplies, it does not guarantee. This runs ON the write path.
    """
    dropped = 0

    def _sub(m: "re.Match[str]") -> str:
        nonlocal dropped
        anchor, target = m.group(1), m.group(2)
        if target in existing:
            return m.group(0)
        dropped += 1
        return anchor  # the sentence survives, the dead link does not

    body_md, dropped = _INTERNAL_HREF.sub(_sub, body_md), dropped

    # Second pass, and the one that matters: ALLOW-LIST the href instead of
    # blacklisting the shapes we happen to recognise. `[text](link)` shipped
    # live because the pattern above only looks at targets beginning with "/",
    # so a placeholder href was never examined at all.
    def _sub_any(m: "re.Match[str]") -> str:
        nonlocal dropped
        anchor, raw = m.group(1), m.group(2).strip()
        target = raw.strip("<>")          # markdown permits <...> around a URL
        allowed = (
            _PLACEHOLDER.match(raw) is not None      # <<AFFILIATE_URL>> etc
            or target in existing
            or target.startswith(("http://", "https://", "mailto:", "#"))
        )
        if allowed:
            return m.group(0)
        dropped += 1
        return anchor

    return _ANY_HREF.sub(_sub_any, body_md), dropped


def existing_slugs() -> set[str]:
    if not CONTENT_DIR.exists():
        return set()
    return {p.stem for p in CONTENT_DIR.glob("*.md")}


def is_thin(slug: str) -> bool:
    """For --upgrade-thin: True if existing page lacks long_context_paragraph >=600 chars."""
    p = CONTENT_DIR / f"{slug}.md"
    if not p.exists():
        return False
    text = p.read_text(encoding="utf-8")
    m = re.search(r"^long_context_paragraph:\s*(.+)$", text, re.MULTILINE)
    if not m:
        return True
    try:
        val = json.loads(m.group(1))
        return len(val) < 600
    except Exception:
        return True


def queue_funnel(clusters: list[Cluster]) -> dict[str, int]:
    """Why a queue came out empty, in numbers.

    An empty queue is the engine reporting a completed contract — every
    cluster already has a page — but for eight days that fact existed only as
    the words "queue empty" in a cron log nobody reads. A zero has to carry its
    cause to a surface a human actually sees (Y55), so this is the funnel the
    weekly report renders.
    """
    have = existing_slugs()
    total = covered = frozen = thin = 0
    for c in clusters:
        total += 1
        slug = slugify(c.primary.keyword)
        if slug in FROZEN_SLUGS:
            frozen += 1
            continue
        if slug in have:
            covered += 1
            if is_thin(slug):
                thin += 1
    return {"clusters_total": total, "covered": covered,
            "frozen": frozen, "thin": thin}


def faq_stream_status() -> dict:
    """The FAQ stream's own zero-cause fields for queue_empty (T-0142-03).

    Before this, an empty FAQ queue reused queue_funnel()'s bank counters
    (clusters_total/covered/frozen/thin) even though the real cause lives in
    the cohort ledger, not the keyword bank -- harmless only because
    `faq_reopen` carried the true reason as a sibling key, but a fast read of
    the log line itself pointed at the wrong queue entirely.
    """
    if not FAQ_LEDGER.exists():
        return {"stream": "faq", "cohort": None, "due": None}
    led = json.loads(FAQ_LEDGER.read_text(encoding="utf-8"))
    cohorts = led.get("cohorts", [])
    if not cohorts:
        return {"stream": "faq", "cohort": None, "due": None}
    last = cohorts[-1]
    return {"stream": "faq", "cohort": len(cohorts), "due": last.get("verdict_due_14")}


FAQ_LEDGER = ROOT / "logs" / "pseo" / "faq-cohorts.json"
FAQ_RATE_FILE = ROOT / "config" / "pseo-faq-rate.txt"
FAQ_CAP_MIN, FAQ_CAP_MAX = 1, 3      # operator's ruling 2026-08-13


def faq_daily_cap(day: Optional[str] = None) -> int:
    """1, 2 or 3 pages today — which one depends on the date itself.

    A fixed cap is a fingerprint: the same number of posts every day, forever,
    is not what a person publishing does. The cap is therefore derived from the
    date, so it varies day to day while every tick within one day computes the
    SAME value — a random draw per tick would let the cap drift upward as ticks
    disagreed with each other.

    sha256, not hash(): Python's hash is salted per process, so two cron ticks
    on the same day would disagree.
    """
    import hashlib
    d = day or datetime.now(timezone.utc).date().isoformat()
    span = FAQ_CAP_MAX - FAQ_CAP_MIN + 1
    return FAQ_CAP_MIN + int(hashlib.sha256(d.encode()).hexdigest(), 16) % span


def _faq_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "faq_children", ROOT / "scripts" / "faq-children.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def faq_child_queue() -> list[Cluster]:
    """Open cohort items that have neither been published nor refused.

    A child of a cold parent inherits nothing, so the ledger is the only
    source: it was built from questions on pages agents measurably fetch.

    Dedup reads a RECORDED fact (the path write_page actually returned),
    never a recomputed slug. Two independent slugify() implementations
    (word-boundary here, blind [:60] in the old writer) used to disagree on
    long questions, so "item['slug'] in {existing filenames}" could be False
    forever for a page that was already on disk under a different name —
    write_page kept re-writing the SAME (mismatched) file every tick,
    reporting success, for 15 days straight. Comparing a stored path against
    the filesystem cannot repeat that defect even if a third slugify()
    appears somewhere: there is nothing left to recompute.
    """
    if not FAQ_LEDGER.exists():
        return []
    led = json.loads(FAQ_LEDGER.read_text(encoding="utf-8"))
    out: list[Cluster] = []
    retired = retired_slugs()
    for ci, cohort in enumerate(led.get("cohorts", [])):
        if cohort.get("voided"):
            continue        # a voided cohort's open rows are never written
        for item in cohort["items"]:
            pub = item.get("published")
            if item.get("refused") or (pub and (ROOT / pub["path"]).exists()):
                continue
            if item["slug"] in retired or slugify(item["question"]) in retired:
                continue    # SEO-11
            parent_kw = item["parent"].strip("/").split("/")[-1].replace("-", " ")
            primary = KeywordRow(code=f"FAQ-{item['slug'][:12]}", bucket="L",
                                 bucket_name="FAQ", keyword=item["question"])
            # Supporting keywords are the parent's topic and the question's own
            # subject — build_user_message requires exactly two.
            supporting = [
                KeywordRow(code="FAQ-s1", bucket="L", bucket_name="FAQ",
                           keyword=parent_kw),
                KeywordRow(code="FAQ-s2", bucket="L", bucket_name="FAQ",
                           keyword=re.sub(r"[?？]", "", item["question"]).strip().lower()),
            ]
            c = Cluster(primary=primary, supporting=supporting,
                        pattern="P-FAQ", url_segment="faq")
            c.faq_parent = item["parent"]      # type: ignore[attr-defined]
            c.faq_slug = item["slug"]          # type: ignore[attr-defined]
            c.faq_cohort = ci                  # type: ignore[attr-defined]
            out.append(c)
    return out


def faq_child_queue_with_reopen(dry_run: bool) -> tuple[list, "dict | None"]:
    """faq_child_queue(), auto-opening the next cohort when it comes back
    empty (2026-09-14, A4 step 5 rework).

    THE STALL THIS CLOSES: cohort 1 finished printing 2026-09-05 (every item
    published or refused) and nothing ever opened cohort 2 — `faq-children.py
    --cohort 20 --write` was a manual command, never wired to any cron. The
    original A4 plan added a NEW weekly cron line for this; that would still
    leave up to 6 days of stall between a cohort finishing mid-week and the
    next Monday reopening it. This tick already fires every 30 minutes under
    the existing, already-installed `15,45 * * * *` crontab entry, so
    checking "is the last cohort fully consumed, and is the tap open" right
    here reopens within the hour, and needs no cron this sandbox is blocked
    from installing.

    Never runs under --dry-run: a dry run must never write the cohort
    ledger. -> (queue, reopen_result) where reopen_result is None if reopen
    was never attempted (queue was already non-empty, or dry_run), else the
    dict open_next_cohort() returned ({"event": "opened", ...} or
    {"event": "cohort_refused", "reason": ...}) — carried into the
    queue_empty log line so a refusal states its cause (Y55).
    """
    queue = faq_child_queue()
    if queue or dry_run:
        return queue, None
    reopen = _faq_module().open_next_cohort(only_if_consumed=True,
                                            writer_sha=writer_sha())
    if reopen["event"] == "opened":
        queue = faq_child_queue()
    return queue, reopen


def _parent_text(parent_path: str) -> str:
    slug = parent_path.strip("/").split("/")[-1]
    f = CONTENT_DIR / f"{slug}.md"
    return f.read_text(encoding="utf-8", errors="replace") if f.exists() else ""


def _payload_text(payload: dict) -> str:
    """Everything the reader will see, flattened, for the overlap measurement."""
    parts: list[str] = []

    def walk(v):
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)

    walk(payload)
    return "\n".join(parts)


def faq_delta_gate(result) -> dict:
    """Fable's two literals, applied between the writer and the disk.

    The parent's FAQ answer is already quotable; a child that restates it
    splits one citation between two of our own URLs. Refusal is recorded with
    its reason (Y58) and the topic is closed, not retried into water.
    """
    fc = _faq_module()
    parent = _parent_text(getattr(result.cluster, "faq_parent", ""))
    if not parent:
        return {"passed": False, "reasons": ["parent page not found on disk"],
                "overlap": 1.0, "new_fact_count": 0}
    return fc.judge(_payload_text(result.payload), parent)


def _faq_ledger_txn(mutate) -> None:
    """Read-modify-write FAQ_LEDGER under an exclusive flock.

    Found the hard way, not designed in advance: a manual ledger repair and
    a live cron tick both did a bare read/modify/write on this same file
    within the same ~3-minute generation window, and whichever wrote last
    silently won, discarding the other's fact. Same flock pattern as
    probabilistic_gate()'s sentinel file, blocking (not LOCK_NB) because the
    critical section here is a few milliseconds of JSON, not a scheduling
    decision worth skipping.
    """
    import fcntl
    if not FAQ_LEDGER.exists():
        return
    lock_path = FAQ_LEDGER.with_suffix(".json.lock")
    with open(lock_path, "a+") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            led = json.loads(FAQ_LEDGER.read_text(encoding="utf-8"))
            mutate(led)
            FAQ_LEDGER.write_text(json.dumps(led, indent=1, ensure_ascii=False) + "\n",
                                  encoding="utf-8")
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def writer_sha() -> str:
    """Identity of the generator: sha256 of the cached system prompt. A G36
    refusal is a property of THIS writer, so it may be retried once the writer
    changes (faq-children.select) and never before."""
    import hashlib
    return hashlib.sha256(build_cached_system_prompt().encode("utf-8")).hexdigest()[:16]


def mark_faq_refused(slug: str, verdict: dict, cohort: Optional[int] = None,
                     gate: str = "delta", sha: Optional[str] = None) -> None:
    """Record a refusal on the item, in the cohort being worked (`cohort`, an
    index) — never by slug across every cohort, which overwrote cohort 2's own
    record when cohort 3 re-bought the same question. `gate` + `writer_sha`
    let select() tell a topic property (delta) from a generator property (G36)."""
    def _mutate(led: dict) -> None:
        for ci, c in enumerate(led.get("cohorts", [])):
            if cohort is not None and ci != cohort:
                continue
            for item in c["items"]:
                if item["slug"] == slug:
                    item["refused"] = {
                        "on": datetime.now(timezone.utc).date().isoformat(),
                        "overlap": verdict.get("overlap"),
                        "new_facts": verdict.get("new_fact_count"),
                        "reasons": verdict.get("reasons", []),
                        "gate": gate,
                        "writer_sha": sha,
                    }
    _faq_ledger_txn(_mutate)


# --- per-day attempt cap + G36 breaker (CCH-FAQ-G36-0929, edit 3) -----------
# A refusal returns the publication slot, so the daily cap of 1-3 stopped
# bounding paid generation calls (8 calls against a cap of 3 on 09-28).
FAQ_ATTEMPT_CAP = FAQ_CAP_MAX + 2
FAQ_G36_BREAKER = 3


def _faq_attempts_path() -> Path:
    today = datetime.now(timezone.utc).date().isoformat()
    return _state_file(f"pseo-faq-attempts-{today}.json")


def _faq_attempts() -> dict:
    try:
        return json.loads(_faq_attempts_path().read_text())
    except Exception:
        return {"refusals": 0, "g36_streak": 0}


def faq_breaker() -> Optional[str]:
    """Reason to skip generation for the rest of the UTC day, else None."""
    a = _faq_attempts()
    if a["g36_streak"] >= FAQ_G36_BREAKER:
        return "g36_breaker"
    if a["refusals"] >= FAQ_ATTEMPT_CAP:
        return "attempt_cap"
    return None


def faq_note_attempt(refused_gate: Optional[str]) -> None:
    """Count a refusal (gate name) or reset the streak (None = published)."""
    a = _faq_attempts()
    if refused_gate is None:
        a["g36_streak"] = 0
    else:
        a["refusals"] += 1
        a["g36_streak"] = a["g36_streak"] + 1 if refused_gate == "G36" else 0
        if a["g36_streak"] == FAQ_G36_BREAKER:
            telegram_lib.alert(f"faq g36_breaker: {FAQ_G36_BREAKER} G36 refusals in a row — "
                               f"FAQ stream paused until the next UTC day")
    _faq_attempts_path().write_text(json.dumps(a))


def mark_faq_published(slug: str, path: Path) -> None:
    """Record the ACTUAL file the writer produced. Never recomputed later.

    `path` is write_page()'s own return value, so this cannot drift from
    what the writer really did — even a future slugify change cannot make
    this dedup lie, because it never recalculates anything.
    """
    def _mutate(led: dict) -> None:
        for cohort in led.get("cohorts", []):
            for item in cohort["items"]:
                if item["slug"] == slug:
                    item["published"] = {
                        "on": datetime.now(timezone.utc).date().isoformat(),
                        "path": str(path.relative_to(ROOT)),
                    }
    _faq_ledger_txn(_mutate)


def faq_published_count() -> int:
    """Metric for the no-progress guard: how many cohort items are DONE."""
    if not FAQ_LEDGER.exists():
        return 0
    led = json.loads(FAQ_LEDGER.read_text(encoding="utf-8"))
    return sum(1 for cohort in led.get("cohorts", []) for item in cohort["items"]
               if item.get("published"))


# slug -> applies_to.product a `current` fact must carry before the page may print (KW-04, row C44)
_FACT_GATED = {"coinbase-one-card": "one"}


def build_queue(
    clusters: list[Cluster],
    only_new: bool,
    upgrade_thin: bool,
    skip_refused: bool = False,
) -> list[Cluster]:
    have = existing_slugs()
    cooling = _bank_refused_recently() if skip_refused else set()
    retired = retired_slugs()
    out: list[Cluster] = []
    for c in clusters:
        slug = slugify(c.primary.keyword)
        if slug in retired:
            continue          # SEO-11: a retired URL stays retired
        if slug in FROZEN_SLUGS:
            continue          # AEO-4: converting pages are never re-queued
        if slug in cooling:
            continue          # KW-04b: refused <24h ago; only the conveyor skips, a human --limit run never does
        if slug in _FACT_GATED and not any(
                f["status"] == "current" and (f.get("applies_to") or {}).get("product") == _FACT_GATED[slug]
                for f in facts_lib.load_all()):
            continue          # C44: no page without >=1 current issuer fact for that product
        if upgrade_thin:
            if slug in have and is_thin(slug):
                out.append(c)
        elif only_new:
            if slug not in have:
                out.append(c)
        else:
            out.append(c)
    return out


# =============================================================================
# Main
# =============================================================================


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rate-file", type=Path, default=RATE_FILE_DEFAULT,
                    help="single-integer file = max pages per day (probabilistic) or per run (bulk)")
    ap.add_argument("--limit", type=int, default=None,
                    help="hard override of rate-file (mostly for testing)")
    ap.add_argument("--dry-run", action="store_true",
                    help="parse + queue + log brief; no API call, no file write")
    ap.add_argument("--probabilistic", action="store_true",
                    help="cron-tick mode: gate via daily count + cooldown + GATE_PROBABILITY; "
                         "exits cleanly without firing on most ticks")
    ap.add_argument("--max-runtime-min", type=int, default=180,
                    help="bail out after this many minutes (bulk mode)")
    ap.add_argument("--only-new", action="store_true", default=True,
                    help="skip slugs that already exist (default ON)")
    ap.add_argument("--full-rebuild", action="store_true",
                    help="regenerate ALL pages (unsets --only-new) — burns tokens, use sparingly")
    ap.add_argument("--faq-children", action="store_true",
                    help="Y66 stream 2: publish the open FAQ-children cohort "
                         "instead of the keyword bank, under the delta gate")
    ap.add_argument("--upgrade-thin", action="store_true",
                    help="re-deepen pages with long_context_paragraph < 600 chars")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    return ap.parse_args(argv)


def read_rate(rate_file: Path, limit: Optional[int]) -> int:
    if limit is not None:
        return limit
    if rate_file.exists():
        try:
            return int(rate_file.read_text().strip())
        except Exception:
            pass
    return 10  # safe default


def fetch_images_for_article(payload: dict) -> list[UnsplashImage]:
    """Pull 3 image queries from Haiku output and fetch one image per query.
    Fail-soft: returns empty list on any error; article still publishes (sans images)."""
    queries = payload.get("image_queries") or []
    if not isinstance(queries, list):
        return []
    images: list[UnsplashImage] = []
    for q in queries[:3]:
        if not isinstance(q, str) or not q.strip():
            continue
        results = unsplash_search(q, count=1)
        if results:
            images.append(results[0])
    return images


# =============================================================================
# Local image hosting (operator policy 2026-05-07 — no external CDN <img src>)
# =============================================================================
PSEO_ASSETS_DIR = ROOT / "seo-site" / "public" / "assets" / "pseo"
PSEO_ASSETS_URL = "/assets/pseo"


def _download_pseo_image(remote_src: str, slug: str, idx: int) -> Optional[str]:
    """Download an Unsplash CDN URL → public/assets/pseo/<slug>-<idx>.jpg.

    Returns the public path (e.g. "/assets/pseo/best-crypto-card-2026-0.jpg")
    or None on failure. Idempotent: existing files are reused without
    re-downloading. The body of the .md file references the LOCAL path so
    visitors never make a request to images.unsplash.com.
    """
    if not remote_src or not remote_src.startswith("https://images.unsplash.com/"):
        return None
    PSEO_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{slug}-{idx}.jpg"
    out = PSEO_ASSETS_DIR / fname
    if out.exists() and out.stat().st_size > 1024:
        return f"{PSEO_ASSETS_URL}/{fname}"
    try:
        req = urllib.request.Request(
            remote_src,
            headers={"User-Agent": "DefyCardBot/1.0 (+https://defycard.com; image-fetch)"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            ctype = resp.headers.get("content-type", "")
            if not ctype.startswith("image/"):
                return None
            data = resp.read()
        if len(data) < 1024:
            return None
        out.write_bytes(data)
        return f"{PSEO_ASSETS_URL}/{fname}"
    except Exception as e:
        print(f"[pseo] image download failed for {remote_src[:80]}: {e}",
              file=sys.stderr)
        return None


def localize_images(images: list[UnsplashImage], slug: str) -> list[UnsplashImage]:
    """Mutate each image so that `src` points at our local CDN path. Falls
    back to the original Unsplash URL only if download fails (so the page
    still renders rather than 404'ing the <img>)."""
    out: list[UnsplashImage] = []
    for idx, img in enumerate(images):
        local = _download_pseo_image(img.src, slug, idx)
        if local:
            img.src = local
        out.append(img)
    return out


def main(argv: list[str]) -> int:
    """Entry point. Whatever happens inside — an exception, an early return,
    a crash while the file is being edited under a live cron — a slot that did
    not buy a page goes back to the day's cap."""
    try:
        return _main(argv)
    finally:
        _refund_slot_if_unspent()


def _main(argv: list[str]) -> int:
    # Force unbuffered stdout so cron-tee logs are real-time
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass

    args = parse_args(argv)

    # E-02: the operator's kill-switch, checked before EVERYTHING else --
    # including --dry-run, which the plan's own acceptance test exercises
    # this under. Unlike fleet_paused below, this one fires even in
    # dry-run mode: a dry run is used to prove the switch works without
    # spending anything, and the point of a stop-cran is that nothing
    # downstream of it runs, dry-run or not.
    if not pseo_enabled():
        print(json.dumps({"event": "skip", "reason": "pseo_disabled"}))
        print("[pseo-tick] skip: pseo_disabled (PSEO_ENABLED=false)")
        return 0

    if args.full_rebuild:
        args.only_new = False

    # T-0125: fleet pause is a distinct outcome from a generator failure --
    # checked BEFORE the probabilistic gate (and before any bulk-mode model
    # call) so a paused window never consumes a daily-count slot or resets
    # the cooldown timer for a call that was never going to happen. Skipped
    # under --dry-run, which never reaches call_claude_cli() anyway (see
    # the early return further down) and is used for gate testing.
    if not args.dry_run:
        paused_until = fleet_paused_until_epoch()
        if paused_until is not None and paused_until > time.time():
            remaining_min = int((paused_until - time.time() + 59) // 60)
            until_iso = datetime.fromtimestamp(paused_until, tz=timezone.utc).isoformat()
            reason, by = fleet_pause_reason_by()
            print(f"[pseo-tick] skip: fleet_paused (until {until_iso}, {remaining_min}min left)")
            print(f"  reason: {reason}")
            print(f"  by: {by}")
            return 0

    rate = read_rate(args.rate_file, args.limit)
    if args.faq_children and args.limit is None:
        # Not the bank conveyor's 30/day. Fable's cohort design is 10-20 a
        # WEEK, and the operator set the shape: 1 to 3 pages a day, varying,
        # so the cadence does not read as a machine keeping a quota.
        rate = faq_daily_cap()
        if not args.probabilistic:
            print(f"[pseo] FAQ stream — today's cap is {rate} "
                  f"(derived from the date, same for every tick today)")

    if args.faq_children and not args.dry_run:
        why = faq_breaker()
        if why:
            print(json.dumps({"event": "skip", "reason": why}))
            print(f"[pseo-tick] skip: {why}")
            return 0

    # ---- Probabilistic gate (cron-tick mode) ----
    # Fires every ~5 min via crontab. Most ticks short-circuit cheaply.
    if args.probabilistic and not args.dry_run:
        allowed, reason = probabilistic_gate(daily_cap=rate)
        _SLOT["claimed"] = allowed
        _SLOT["refundable"] = allowed
        if not allowed:
            # Quiet exit so logs/pseo/<date>.jsonl doesn't bloat with skip noise.
            print(f"[pseo-tick] skip: {reason}")
            return 0
        # Slot has been claimed under flock — rollback in main loop if
        # generation fails (see _decrement_daily_count call paths below).
        print(f"[pseo-tick] {reason} — generating one article")
        # In probabilistic mode we generate exactly ONE article per fire
        # (the cron + cooldown + cap mechanics are what shapes daily volume).
        per_run_limit = 1
    else:
        per_run_limit = rate
        print(f"[pseo] rate={rate} model={args.model} dry_run={args.dry_run} "
              f"only_new={args.only_new} upgrade_thin={args.upgrade_thin}")

    rows = parse_keyword_bank(SKILL_KEYWORDS)
    if not args.probabilistic:
        print(f"[pseo] parsed {len(rows)} keyword rows from skill")
    clusters = build_clusters(rows)
    if not args.probabilistic:
        print(f"[pseo] built {len(clusters)} clusters")

    faq_reopen: dict | None = None
    if args.faq_children:
        queue, faq_reopen = faq_child_queue_with_reopen(args.dry_run)
        if not args.probabilistic:
            print(f"[pseo] FAQ cohort queue: {len(queue)} unpublished, unrefused")
    else:
        queue = build_queue(clusters, args.only_new, args.upgrade_thin,
                            skip_refused=args.probabilistic)
    if not args.probabilistic:
        print(f"[pseo] queue size: {len(queue)} (after filters)")
    queue = queue[:per_run_limit]

    if not args.probabilistic:
        print(f"[pseo] running first {len(queue)} (per-run cap)")

    if args.dry_run:
        for c in queue[:5]:
            print(f"  DRY: /{c.url_segment}/{slugify(c.primary.keyword)}/  "
                  f"({c.pattern}) primary='{c.primary.keyword}'")
        if len(queue) > 5:
            print(f"  ... and {len(queue) - 5} more")
        return 0

    if not queue:
        # The slot was claimed by probabilistic_gate BEFORE the queue was
        # built, and this branch used to return without giving it back. Today
        # that is harmless because the queue is always empty; on the day the
        # keyword bank is topped up mid-day, idle ticks would have eaten the
        # daily cap before the first real article. The rollback the gate's own
        # comment promises is one line.
        if args.probabilistic and not args.dry_run:
            _decrement_daily_count()
            _SLOT["claimed"] = False
        extra = {"faq_reopen": faq_reopen} if args.faq_children else {}
        funnel = faq_stream_status() if args.faq_children else queue_funnel(clusters)
        print(json.dumps({
            "event": "queue_empty",
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "slot_returned": bool(args.probabilistic and not args.dry_run),
            **funnel,
            **extra,
        }, ensure_ascii=False))
        print("[pseo] queue empty — nothing to generate")
        pipeline_name = "pseo-faq-children" if args.faq_children else "pseo-pages"
        metric = faq_published_count() if args.faq_children else len(existing_slugs())
        no_progress_guard.check(pipeline_name, metric=metric, queue_nonempty=False)
        return 0

    # ---- Live run (Max plan via Claude Code CLI, no API key needed) ----
    if not Path(CLAUDE_BIN).exists():
        print("ERROR: /usr/bin/claude not found (Claude Code CLI required)", file=sys.stderr)
        return 2

    cached_system = build_cached_system_prompt()
    import hashlib
    writer_sha_ = hashlib.sha256(cached_system.encode("utf-8")).hexdigest()[:16]
    if not args.probabilistic:
        print(f"[pseo] cached system prompt: ~{len(cached_system) // 4} tokens (rough)")
    if not UNSPLASH_KEY:
        print("[pseo] WARN: UNSPLASH_ACCESS_KEY not set — articles will publish without images")

    deadline = time.time() + args.max_runtime_min * 60
    total_cost = 0.0
    succeeded = 0
    failed = 0
    queue_had_candidates = bool(queue)
    for i, cluster in enumerate(queue):
        if time.time() > deadline:
            print(f"[pseo] runtime cap reached after {i} pages — stopping")
            break
        if args.faq_children and faq_breaker():
            print(f"[pseo] skip: {faq_breaker()} — stopping before the next paid call")
            break
        if args.faq_children:
            # Selector sibling: rows already in a cohort never passed today's
            # select() filter. Refuse before spending anything (T-0140 rule).
            bad = _faq_module().reject_reason(cluster.primary.keyword)
            if bad:
                sel_slug = getattr(cluster, "faq_slug", slugify(cluster.primary.keyword))
                _emit({"event": "refused", "gate": "selector", "slug": sel_slug, "reason": bad})
                mark_faq_refused(sel_slug, {"reasons": [bad]},
                                 getattr(cluster, "faq_cohort", None), "selector", writer_sha_)
                if args.probabilistic and _SLOT["claimed"]:
                    _decrement_daily_count()
                    _SLOT["claimed"] = False
                continue
        # AEO-4-style last line of defence, but BEFORE spending anything.
        # Whoever is about to write checks the exact path it will write, first.
        target_check = would_overwrite(cluster)
        if target_check is not None:
            failed += 1
            slug_check = slugify(cluster.primary.keyword)
            _emit({"event": "would_overwrite", "slug": slug_check,
                   "path": str(target_check.relative_to(ROOT))})
            telegram_lib.alert(
                f"would_overwrite: {slug_check} already exists on disk, "
                f"refused before spending on generation"
            )
            if args.probabilistic and _SLOT["claimed"]:
                _decrement_daily_count()
                _SLOT["claimed"] = False
            if args.faq_children:
                # The file is real; the ledger just did not know it yet.
                # Record the fact now so this slot is not refused forever.
                mark_faq_published(getattr(cluster, "faq_slug", slug_check), target_check)
            continue
        result = generate_page(cached_system, cluster, args.model)
        log_usage(result)
        total_cost += result.cost_usd
        if result.error:
            failed += 1
            print(f"[pseo] FAIL {i+1}/{len(queue)} {cluster.primary.keyword!r}: {result.error}")
            # Roll back the daily-cap slot we claimed pre-generation in
            # probabilistic mode so a transient Claude/network error doesn't
            # permanently drain quota (council round 2 finding).
            if args.probabilistic:
                _decrement_daily_count()
            continue
        # Fetch images per Haiku's image_queries (fail-soft) — localize
        # happens inside write_page() once slug is finalized.
        images = fetch_images_for_article(result.payload)
        try:
            if args.faq_children:
                verdict = faq_delta_gate(result)
                slug_ = getattr(result.cluster, "faq_slug", "")
                print(json.dumps({
                    "event": "faq_delta_gate",
                    "slug": slug_,
                    "parent": getattr(result.cluster, "faq_parent", ""),
                    "overlap": verdict.get("overlap"),
                    "new_facts": verdict.get("new_fact_count"),
                    "passed": verdict.get("passed"),
                    "reasons": verdict.get("reasons", []),
                    "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                }, ensure_ascii=False))
                if not verdict.get("passed"):
                    # Refused, recorded, and the topic is closed — never padded
                    # to length (Y58). The slot is returned: a refusal is not a
                    # publication, and eating the day's cap on it would silence
                    # the stream for the rest of the day.
                    mark_faq_refused(slug_, verdict, getattr(result.cluster, "faq_cohort", None),
                                     "delta", writer_sha_)
                    faq_note_attempt("delta")
                    if args.probabilistic and _SLOT["claimed"]:
                        _decrement_daily_count()
                        _SLOT["claimed"] = False
                    continue
            # G37 (CCH-HUM-04): copy-edit the flagged sentences. Runs AFTER the
            # delta gate and BEFORE G36, so G36 judges the REWRITTEN text.
            hslug = getattr(cluster, "faq_slug", "") or slugify(cluster.primary.keyword)
            orig_payload = result.payload
            hres = humanize_gate.humanize(result.payload)
            result.payload = hres.payload
            total_cost += hres.cost_usd
            hrow = {
                "event": "humanize", "slug": hslug,
                "stream": "faq" if args.faq_children else "bank",
                "flags_before": hres.flags_before, "flags_after": hres.flags_after,
                "rewritten": hres.rewritten, "accepted": hres.accepted,
                "rejected": hres.rejected, "refused_reason": hres.refused_reason,
                "cost_usd": round(hres.cost_usd, 6),
                "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            }
            print(json.dumps(hrow, ensure_ascii=False))
            _hdir = LOG_DIR.parent / "humanize"   # LOG_DIR read at CALL time (log_usage rule)
            _hdir.mkdir(parents=True, exist_ok=True)
            with (_hdir / f"{_dt.datetime.now(_dt.timezone.utc).date().isoformat()}.jsonl").open(
                    "a", encoding="utf-8") as _hf:
                _hf.write(json.dumps(hrow, ensure_ascii=False) + "\n")
            if hres.refused_reason:
                failed += 1
                _emit({"event": "refused", "gate": "G37", "slug": hslug,
                       "reasons": [hres.refused_reason]})
                telegram_lib.alert(f"refused: {hres.refused_reason} — {hslug} not written (G37)")
                if not args.faq_children:
                    _mark_bank_refused(hslug)
                if args.faq_children:
                    mark_faq_refused(hslug, {"reasons": [hres.refused_reason]},
                                     getattr(result.cluster, "faq_cohort", None),
                                     "G37", writer_sha_)
                    faq_note_attempt("G37")
                if args.probabilistic and _SLOT["claimed"]:
                    _decrement_daily_count()
                    _SLOT["claimed"] = False
                continue
            # G36 (E-11): every money number the reader will see must come
            # from a verified fact. BEFORE write_page, for both streams —
            # a refusal must leave no file behind (Y63: the page is the
            # thing a reader believes, so the check belongs on the last
            # side of the door, not after it).
            gate_slug = slugify(cluster.primary.keyword)
            unbacked = money_gate.unbacked(_payload_text(result.payload))
            if unbacked:
                failed += 1
                _emit({"event": "refused", "gate": "G36", "slug": gate_slug,
                       "reasons": money_gate.reasons(unbacked),
                       "context": _g36_context(_payload_text(result.payload), unbacked)})
                telegram_lib.alert(
                    f"refused: unbacked_number:{unbacked[0]} — {gate_slug} not written "
                    f"({len(unbacked)} unbacked token(s), G36)"
                )
                if not args.faq_children:
                    _mark_bank_refused(gate_slug)
                if args.faq_children:
                    # Close the topic like the delta gate does. Leaving it open
                    # re-buys the same refusal every tick — the 15-day carousel
                    # shape, with a Haiku call each time.
                    mark_faq_refused(getattr(result.cluster, "faq_slug", gate_slug),
                                     {"reasons": money_gate.reasons(unbacked)},
                                     getattr(result.cluster, "faq_cohort", None),
                                     "G36", writer_sha_)
                    faq_note_attempt("G36")
                if args.probabilistic and _SLOT["claimed"]:
                    _decrement_daily_count()
                    _SLOT["claimed"] = False
                continue
            path = write_page(result, images)
            _SLOT["claimed"] = False        # spent: a page exists
            if hres.rewritten:              # G37 blind audit: sampled, after a real write only
                total_cost += (humanize_gate.audit_pair(hslug, orig_payload, hres.payload,
                                                        log_dir=_hdir) or {}).get("cost_usd", 0.0)
            if args.faq_children:
                # Record the FACT the writer just produced. This is the only
                # place the ledger learns a slug is done — never a recompute.
                mark_faq_published(getattr(cluster, "faq_slug", ""), path)
                faq_note_attempt(None)
        except Exception as e:
            failed += 1
            _emit({"event": "failed", "stage": "write",
                   "slug": slugify(cluster.primary.keyword),
                   "error": f"{type(e).__name__}: {e}"})
            if args.probabilistic:
                _decrement_daily_count()
            continue
        succeeded += 1
        # Probabilistic gate already incremented under flock — do not double.
        n_imgs = len(images)
        print(f"[pseo] OK {i+1}/{len(queue)} {path.name} "
              f"({result.usage.get('output_tokens', 0)} out, {n_imgs} imgs, "
              f"${result.cost_usd:.4f}, {result.elapsed_s:.1f}s)")

    if not args.probabilistic or succeeded > 0:
        print(f"[pseo] done: succeeded={succeeded} failed={failed} "
              f"total_cost=${total_cost:.4f}")

    # General watchdog (LAW: "successful" and "made progress" are not the
    # same claim). Applies to this pipeline in BOTH modes it runs: the FAQ
    # children stream and the bank/pSEO stream share this one guard.
    pipeline_name = "pseo-faq-children" if args.faq_children else "pseo-pages"
    metric = faq_published_count() if args.faq_children else len(existing_slugs())
    if no_progress_guard.check(pipeline_name, metric=metric,
                               queue_nonempty=queue_had_candidates):
        telegram_lib.alert(
            f"no_progress: {pipeline_name} reported success 3 runs running "
            f"with a non-empty queue and metric stuck at {metric}"
        )
        return 1
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
