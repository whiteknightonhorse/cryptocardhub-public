"""CCH-SEO-07 — headless browsers and synthetic clicks must not reach GA4.

Reads the built dist/index.html; expected strings are literals (Y49).
"""
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "dist" / "index.html").read_text()


def test_webdriver_opt_out_before_config():
    guard = "ga-disable-G-JLRZD59902"
    assert guard in HTML
    assert "navigator.webdriver" in HTML
    assert HTML.index(guard) < HTML.index("gtag('config'")


def test_click_tracker_ignores_untrusted_events():
    line = "if (!e.isTrusted) return;"
    assert line in HTML
    assert HTML.index(line) < HTML.index("gtag('event', 'register_click'")
