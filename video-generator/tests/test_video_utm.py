"""G9' (Y50 P0-3) — every video link is attributable, and the pinned comment
is not a link farm.

WHY: 213 publications produced strictly zero measurable sessions. Part of that
is reach, but part is that every clip pointed at a bare https://defycard.com/,
which lands in GA4 as `(direct)` — indistinguishable from the scanner traffic
that makes up most of that bucket. Unmarked links cannot be told apart from
no links at all, so the channel could never be judged on evidence.

THE LAWS ARE LITERALS HERE (lesson Y49): the expected parameter names and the
one-URL limit are written out, not imported from the module under test.
"""
from __future__ import annotations

import re

from pipeline import main as m

# LAW: GA4 collects `utm_content` into the STANDARD dimension
# `sessionManualAdContent` (verified live 2026-08-11 against
# properties/<property-id>). `customEvent:utm_content` returns 400 — it is an
# event-scoped custom dimension that was never registered. These are the
# parameters that must appear on a video link.
REQUIRED_PARAMS = ["utm_source=youtube", "utm_medium=shorts", "utm_content="]

# LAW: a pinned comment carries at most this many URLs.
MAX_PINNED_URLS = 1

URL_RE = re.compile(r"https?://[^\s)]+")

SCRIPT = {"thumbnail_hook": "CRYPTO CARD | THE CATCH", "body": "b.", "cta": "c"}
HASHTAGS = {"visible": ["#a", "#b", "#c", "#d"], "api": ["a"]}


def test_youtube_description_carries_full_campaign_markup():
    cap = m._build_caption("youtube", SCRIPT, HASHTAGS, topic_id=7)
    for param in REQUIRED_PARAMS:
        assert param in cap, f"description is missing {param!r}:\n{cap}"


def test_utm_content_identifies_the_individual_video():
    """Two videos must not share a utm_content, or the dimension says nothing."""
    a = m._build_caption("youtube", SCRIPT, HASHTAGS, topic_id=7)
    b = m._build_caption("youtube", SCRIPT, HASHTAGS, topic_id=8)
    grab = lambda c: re.search(r"utm_content=([^\s&]+)", c).group(1)  # noqa: E731
    assert grab(a) != grab(b), "utm_content is identical across videos"
    assert grab(a), "utm_content is empty"


def test_youtube_description_has_exactly_one_url():
    cap = m._build_caption("youtube", SCRIPT, HASHTAGS, topic_id=7)
    assert len(URL_RE.findall(cap)) == 1


def test_pinned_comment_has_at_most_one_url():
    urls = URL_RE.findall(m._build_pinned_yt(SCRIPT, topic_id=7))
    assert len(urls) <= MAX_PINNED_URLS, f"pinned comment is a link farm: {urls}"


def test_pinned_comment_url_is_also_tracked():
    pinned = m._build_pinned_yt(SCRIPT, topic_id=7)
    for param in REQUIRED_PARAMS:
        assert param in pinned, f"pinned comment link is missing {param!r}"


def test_video_slug_is_stable_and_dated():
    assert m.video_slug("youtube", 7, day="2026-08-11") == "yo-20260811-07"
    assert m.video_slug("youtube", 7, day="2026-08-11") == m.video_slug("youtube", 7, day="2026-08-11")


def test_tracked_url_is_wellformed_and_on_our_domain():
    u = m.tracked_url("youtube", 7, day="2026-08-11")
    assert u.startswith("https://defycard.com/?"), u
    assert " " not in u
