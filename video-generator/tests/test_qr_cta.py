"""Y54 P1-2 gates (G15) — the QR is the only machine-passable route out of a Short.

A Short's description URL is a text node with no `navigationEndpoint` (Y52), so
on this format the QR is the whole path from the video to the site. That makes
it a money artifact, and it is verified the way Y52 taught: decoded out of a
REAL rendered mp4, never out of the source PNG. A valid `.ass` sat next to five
captionless videos; a valid QR png would prove exactly as much.

Two properties beyond "a QR is present", both of which a mutation would
otherwise slip past:

  * it decodes to the page the topic points at — a QR that cleanly encodes the
    homepage is the Y50/Y53 money bug in a new channel, and it is *scannable*,
    so nothing downstream would ever complain;
  * it survives being downscaled to a phone-sized viewport, which is the only
    place it is ever used.
"""
from __future__ import annotations
import subprocess

import pytest

from pipeline import main as pipeline_main, render
# Reused rather than re-declared: same sandboxed ledger and catalog the Y53
# gates run against (`ledger` redirects the dedup DB into tmp_path, `catalog`
# redirects the content dir and silences the news/viral rolls).
from tests.test_pseo_topics import _write_page, catalog, ledger  # noqa: F401
from tests.conftest import write_stub_media

# The laws as literals — not read back from the code under test (Y49).
EXPECTED_MEDIUM = "utm_medium=qr"
PHONE_VIEWPORT_W = 400
CTA_SECONDS = 6.0

cv2 = pytest.importorskip("cv2", reason="QR decode gate needs opencv")
np = pytest.importorskip("numpy")


def _silence(path, seconds):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-t", f"{seconds}", "-c:a", "libmp3lame", "-q:a", "9", str(path)],
        capture_output=True, check=True, timeout=120,
    )
    return path


def _black_png(path):
    """Black, deliberately: a controlled background is how Y52's caption gate
    learned to tell 'drawn' from 'file exists'."""
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=1080x1920",
         "-frames:v", "1", str(path)],
        capture_output=True, check=True, timeout=60,
    )
    return path


def _render(tmp_path, *, cta_url, qr_payload, tts_seconds=12.0):
    voice = _silence(tmp_path / "voice.mp3", tts_seconds)
    music = _silence(tmp_path / "music.mp3", 60)
    clips = [{"kind": "photo", "path": str(_black_png(tmp_path / "c0.png"))}]
    return render.render(
        voiceover_path=voice, music_path=music, clips=clips, words=[],
        ftc_overlay_path=None, target_seconds=tts_seconds,
        out_path=tmp_path / "final.mp4",
        cta_url=cta_url, qr_payload=qr_payload, cta_seconds=CTA_SECONDS,
    )


def _cta_frame(mp4, tmp_path, name="frame.png"):
    """A frame from inside the CTA window — 2 s before the end."""
    out = subprocess.check_output(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(mp4)],
        timeout=30,
    )
    import json as _json
    dur = float(_json.loads(out)["format"]["duration"])
    frame = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{dur - 2.0:.3f}", "-i", str(mp4),
         "-frames:v", "1", str(frame)],
        capture_output=True, check=True, timeout=60,
    )
    return cv2.imread(str(frame), cv2.IMREAD_GRAYSCALE)


def _decode(img):
    return cv2.QRCodeDetector().detectAndDecode(img)[0]


@pytest.fixture(autouse=True)
def production_env(monkeypatch):
    """Run with the production flag SET. Y52's caption gate was blind precisely
    because it ran without the env var that turned the bug on."""
    monkeypatch.setenv("DEFY_NEW_DESIGN", "1")


def test_the_qr_in_the_rendered_video_decodes_to_the_topics_own_page(tmp_path):
    """The money assertion: not 'a QR is there' but 'it goes where the video says'."""
    # Written out as a literal, NOT as qr_url(...): a gate that computes its
    # expectation from the code under test travels with the mutation and stays
    # green (Y49). This string is the law — the renderer must reproduce it.
    expected = ("https://defycard.com/geo/best-crypto-card-india/"
                "?utm_source=youtube&utm_medium=qr")
    mp4 = _render(tmp_path, cta_url="defycard.com/geo/best-crypto-card-india",
                  qr_payload=pipeline_main.qr_url("youtube", "/geo/best-crypto-card-india/"))

    decoded = _decode(_cta_frame(mp4, tmp_path))
    assert decoded, "no QR decoded out of the rendered mp4 — the .ass lesson (Y52)"
    assert decoded == expected, (
        f"QR decodes to {decoded!r}, expected {expected!r}. A QR that cleanly "
        f"encodes the wrong page is the Y50 money bug, scannable."
    )
    assert EXPECTED_MEDIUM in decoded, (
        "without utm_medium=qr this route cannot be separated from shorts in "
        "GA4, and the 30-day kill criterion cannot be evaluated"
    )


def test_the_qr_still_decodes_at_phone_size(tmp_path):
    """It is only ever scanned off a phone playing a vertical video. Decoding at
    1080p proves nothing about that; this pins the measured 420 px margin."""
    payload = pipeline_main.qr_url("youtube", "/reviews/is-redotpay-legit/")
    mp4 = _render(tmp_path, cta_url=pipeline_main.cta_text("/reviews/is-redotpay-legit/"),
                  qr_payload=payload)

    img = _cta_frame(mp4, tmp_path)
    h = int(img.shape[0] * PHONE_VIEWPORT_W / img.shape[1])
    small = cv2.resize(img, (PHONE_VIEWPORT_W, h), interpolation=cv2.INTER_AREA)
    assert _decode(small) == payload, (
        f"QR unreadable once downscaled to {PHONE_VIEWPORT_W}px wide — a real "
        f"camera pointed at a screen is worse than this resize"
    )


def test_no_qr_is_drawn_when_the_caller_asks_for_none(tmp_path):
    """The parameter genuinely controls the picture — otherwise the gate above
    would pass on a renderer that always draws its own QR."""
    mp4 = _render(tmp_path, cta_url="defycard.com", qr_payload=None)
    assert not _decode(_cta_frame(mp4, tmp_path))


def test_a_long_slug_shrinks_the_text_instead_of_dropping_the_path(tmp_path):
    """Falling back to the bare domain when the slug is long re-opens the very
    bug this work closes, silently and only for the longest URLs."""
    long_path = "/how-to/how-to-connect-wallet-to-ether-fi-cash-account/"
    text = pipeline_main.cta_text(long_path)
    font = str(render._config.root() / "assets" / "fonts" / "Anton-Regular.ttf")
    size = render._fit_fontsize(text, font, 1080 - 80)

    from PIL import ImageFont
    assert ImageFont.truetype(font, size).getlength(text) <= 1080 - 80, "text overflows the frame"
    assert size >= 34, "shrunk below legibility — shorten the slug, do not hide the path"
    assert text.endswith("ether-fi-cash-account"), "the path must survive whole"


def test_the_human_text_carries_no_tracking_and_the_scanner_url_does():
    """Two strings, one destination. A person retypes what is on screen; a
    scanner needs the scheme and GA4 needs the markup."""
    path = "/geo/best-crypto-card-turkey/"
    text, scanned = pipeline_main.cta_text(path), pipeline_main.qr_url("youtube", path)

    assert text == "defycard.com/geo/best-crypto-card-turkey"
    assert "utm_" not in text and "https" not in text
    assert scanned.startswith("https://defycard.com" + path)
    assert "utm_source=youtube" in scanned and EXPECTED_MEDIUM in scanned
    assert "utm_content" not in scanned, (
        "the per-video slug is ~28 chars that push the QR two versions denser; "
        "the page path already identifies the topic"
    )
    assert pipeline_main.cta_text("/") == "defycard.com", "homepage keeps the bare domain"


def test_the_wiring_delivers_the_page_to_the_renderer(ledger, catalog, tmp_path,
                                                      monkeypatch):
    """The frame gates above call render() directly, so they stay green even if
    main.py never passes cta_url — which is exactly how the bug shipped: the
    parameter existed, defaulted to the homepage, and NO caller set it.

    This runs the real run_pipeline and reads what arrives at the renderer.
    """
    _write_page(catalog, "best-crypto-card-india", bucket="geo",
                keyword="best crypto card india")

    from pipeline import (captions, fetch_music, fetch_visuals, telegram_preview,
                          tts_edge, write_script)
    from pipeline.publish import youtube as pub_yt

    monkeypatch.setattr(pipeline_main, "_out_dir", lambda *a, **k: tmp_path / "out")
    monkeypatch.setattr(write_script, "write", lambda topic, dry_run=False: {
        "voiceover": "words", "body": "b.", "cta": "c",
        "thumbnail_hook": "CHEAPEST | WE CHECKED",
    })
    # T-0142-02 (G39): main.py now ffprobes voice_path right after synth, so
    # the mock must leave a real audio file behind, not just a return value.
    # Force the edge provider regardless of a live TTS_PROVIDER=elevenlabs in
    # the real .env — otherwise main.py routes to the unmocked
    # tts_eleven.synthesize instead of this one.
    monkeypatch.setattr(pipeline_main._config, "tts_provider", lambda: "edge")
    monkeypatch.setattr(tts_edge, "synthesize",
                         lambda voiceover, platform, out_path, **k: _silence(out_path, 2.0) and {"voice": "v"})
    monkeypatch.setattr(fetch_visuals, "fetch", lambda *a, **k: [{"path": "x"}])
    monkeypatch.setattr(fetch_music, "fetch", lambda *a, **k: {"track": "m"})
    monkeypatch.setattr(captions, "align", lambda *a, **k: [{"word": "words"}])
    monkeypatch.setattr(telegram_preview, "send", lambda *a, **k: None)
    monkeypatch.setattr(pub_yt, "upload", lambda **k: {"url": "u"})

    seen: dict = {}

    def _fake_render(**k):
        seen.update(k)
        # T-0142-02 (G39): main.py ffprobes the rendered final.mp4 right after
        # render() returns, so the stub must leave a real file at out_path.
        write_stub_media(k["out_path"], 2.5)

    monkeypatch.setattr(pipeline_main.render, "render", _fake_render)

    pipeline_main.run_pipeline("youtube", dry_run=True, seed=1)

    assert seen.get("cta_url") == "defycard.com/geo/best-crypto-card-india", (
        f"the renderer burned {seen.get('cta_url')!r} into the frame of a video "
        f"about India — the homepage still shows a card that cannot be issued there"
    )
    assert "/geo/best-crypto-card-india/" in (seen.get("qr_payload") or ""), (
        f"QR payload never reached the renderer: {seen.get('qr_payload')!r}")
    assert EXPECTED_MEDIUM in seen["qr_payload"]
