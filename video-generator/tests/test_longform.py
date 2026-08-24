"""G16 (Y52) — a long-form video is long enough to be one, and its only link
is clickable and attributable.

WHY THIS GATE EXISTS: YouTube classifies by duration alone. At 180 s or less a
vertical video is a Short, and a Short's description URL is rendered as a plain
text node with no navigationEndpoint — nobody can click it. So a long-form
video that quietly comes out 20 s short is not "a bit short": it is a Short
with an unclickable link, i.e. the exact failure the format was built to
escape. Two independent locks are asserted here — the measured duration of a
REAL rendered mp4 (lesson Y49: a formula-only test cannot catch a bug that
lives in the formula), and the markup of the description string.

THE LAWS ARE LITERALS (lesson Y49). The numbers below are written out, not
imported from the module under test, so a mutation of the module moves the
code away from the law instead of moving the law along with the code.
"""
from __future__ import annotations

import json
import re
import subprocess

import pytest

from pipeline import longform
from pipeline.publish import youtube as pub_yt

# LAW: the rendered file must land here. 180.0 s is YouTube's Shorts boundary
# exactly; the floor sits 5 s above it so no rounding can push a pilot video
# back across. The ceiling catches a narration or a render that ran away.
MIN_SECONDS = 185.0
MAX_SECONDS = 260.0

# LAW: the campaign markup that makes a long-form session separable from a
# Shorts session in GA4 (`utm_content` lands in sessionManualAdContent).
REQUIRED_PARAMS = ["utm_source=youtube", "utm_medium=longform", "utm_content="]

# LAW: one URL in the description, chapters starting at 0:00, at least three of
# them, none closer together than 10 s — below that YouTube renders none.
MAX_DESCRIPTION_URLS = 1
MIN_CHAPTERS = 3
MIN_CHAPTER_GAP_SECONDS = 10.0

URL_RE = re.compile(r"https?://[^\s)]+")
CHAPTER_RE = re.compile(r"^(\d+):(\d{2}) \S", re.MULTILINE)

SLUG = "is-redotpay-legit"


def _probe(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        timeout=60,
    )
    return float(json.loads(out)["format"]["duration"])


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    """One real long-form mp4, rendered through the production path.

    Placeholder b-roll and silent narration of the estimated length — but the
    render, the concat, the caption pass and the duration arithmetic are the
    live ones. Artefacts go to tmp, never the live output/ tree.
    """
    tmp = tmp_path_factory.mktemp("longform")
    orig = longform.out_dir
    longform.out_dir = lambda slug, day=None: tmp / slug  # type: ignore[assignment]
    try:
        yield longform.render_one(SLUG, dry_run=True, day="2026-08-11")
    finally:
        longform.out_dir = orig  # type: ignore[assignment]


def test_rendered_video_is_past_the_shorts_boundary(rendered):
    """The measurement is on the artefact, not on the formula that made it."""
    dur = _probe(rendered["final_path"])
    assert dur >= MIN_SECONDS, (
        f"{dur:.2f}s — at or under the {MIN_SECONDS}s floor this uploads as a "
        f"Short and its description link stops being clickable"
    )
    assert dur <= MAX_SECONDS, f"{dur:.2f}s exceeds the {MAX_SECONDS}s ceiling"


def test_description_carries_one_clickable_tracked_link(rendered):
    desc = rendered["description"]
    urls = URL_RE.findall(desc)
    assert len(urls) == MAX_DESCRIPTION_URLS, f"expected one URL, got {urls}"
    assert urls[0].startswith("https://defycard.com/"), urls[0]
    for param in REQUIRED_PARAMS:
        assert param in desc, f"description is missing {param!r}:\n{desc}"


def test_link_points_at_the_page_the_video_answers(rendered):
    """Partner routing is a property of the page (Y50) — the link must land on
    the source page, or a Turkey/India viewer gets sent to a card they cannot
    be issued."""
    url = URL_RE.findall(rendered["description"])[0]
    assert f"/{SLUG}/" in url, url


def test_description_has_usable_chapters(rendered):
    desc = rendered["description"]
    stamps = [int(m.group(1)) * 60 + int(m.group(2))
              for m in CHAPTER_RE.finditer(desc)]
    assert len(stamps) >= MIN_CHAPTERS, f"only {len(stamps)} chapters: {desc}"
    assert stamps[0] == 0, f"first chapter must be 0:00, got {stamps[0]}"
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert all(g >= MIN_CHAPTER_GAP_SECONDS for g in gaps), (
        f"chapters closer than {MIN_CHAPTER_GAP_SECONDS}s: {gaps}"
    )


def test_description_never_calls_a_long_form_a_short(rendered):
    """LAW: #Shorts is a claim about format, not a topic tag, and on a 200+ s
    video it is false. It shipped on the first five pilot renders because
    for_youtube() hardcoded it back when every YouTube video really was a
    Short. Tagging the one format that can carry a clickable link as the format
    that cannot is the same confusion this whole pilot exists to end.
    """
    tags = rendered["description"].lower()
    assert "#shorts" not in tags, rendered["description"]


def test_description_keeps_the_affiliate_disclosure(rendered):
    assert "Affiliate link" in rendered["description"]
    assert "Not financial advice" in rendered["description"]


def test_pilot_never_publishes(rendered):
    assert rendered["published"] is False


def test_a_thin_page_is_excluded_not_padded():
    """Padding is how Y49's 38 s floor produced voiceless b-roll. A page that
    cannot carry the format is dropped from the pilot instead."""
    with pytest.raises(ValueError, match="excluded"):
        longform.build_script({"slug": "thin", "intro_paragraph": "Two words."})


def test_short_narration_is_a_hard_failure(tmp_path, monkeypatch):
    """No 'accept short' branch: that is exactly how a long-form silently
    becomes a Short."""
    monkeypatch.setattr(longform.tts_edge, "synthesize",
                        lambda *a, **k: _silence_stub(a, k))
    monkeypatch.setattr(longform, "out_dir", lambda slug, day=None: tmp_path / slug)
    with pytest.raises(RuntimeError, match="narration is"):
        longform.render_one(SLUG, dry_run=True, day="2026-08-11")


def _silence_stub(args, kwargs):
    """Stand-in TTS that returns a 30 s narration — a Short's length."""
    out_path = args[2]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
         "-t", "30", str(out_path)], capture_output=True, check=True, timeout=60,
    )
    out_path.with_suffix(".timestamps.json").write_text("[]")
    return {"voice": "stub", "path": str(out_path)}


# --- the cross-check that runs at upload time --------------------------------

def _fake_video(path, seconds):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s=128x128:r=5",
         "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, check=True, timeout=120,
    )
    return path


def test_upload_refuses_a_long_file_marked_as_a_short(tmp_path):
    v = _fake_video(tmp_path / "long.mp4", 190)
    with pytest.raises(ValueError, match="utm_medium=longform"):
        pub_yt.validate_format_markup(v, "watch: https://defycard.com/?utm_medium=shorts")


def test_upload_refuses_a_short_file_marked_as_long_form(tmp_path):
    v = _fake_video(tmp_path / "short.mp4", 30)
    with pytest.raises(ValueError, match="utm_medium=shorts"):
        pub_yt.validate_format_markup(v, "watch: https://defycard.com/?utm_medium=longform")


def test_upload_itself_runs_the_check_before_the_network(tmp_path):
    """The validator must be wired into upload(), not merely exist. dry_run is
    used deliberately: the check has to fire before the early return, or a
    dry run would never exercise it."""
    v = _fake_video(tmp_path / "long2.mp4", 190)
    with pytest.raises(ValueError):
        pub_yt.upload(video_path=v, title="t", description="no markup at all",
                      tags=[], dry_run=True)


def test_upload_refuses_the_boundary_dead_zone(tmp_path):
    v = _fake_video(tmp_path / "edge.mp4", 182)
    with pytest.raises(ValueError, match="dead zone"):
        pub_yt.validate_format_markup(v, "https://defycard.com/?utm_medium=longform")
