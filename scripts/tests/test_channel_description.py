"""E-21 gate — the YouTube channel description carries the site link and the
affiliate disclosure, and fits YouTube's 1000-char about-page limit.

Literal law (Y49): the 1000-char cap and both required markers are hardcoded
here, not read from the file under test.
"""
from __future__ import annotations
from pathlib import Path

DESCRIPTION_PATH = (
    Path(__file__).resolve().parent.parent
    / "yt-channel-descriptions" / "DEFYCARD.txt"
)
MAX_CHARS = 1000
REQUIRED_LINK = "defycard.com"
REQUIRED_DISCLOSURE_MARKER = "may earn a commission"


def test_description_exists_and_fits_youtube_limit():
    text = DESCRIPTION_PATH.read_text()
    assert 0 < len(text) <= MAX_CHARS


def test_description_links_to_site():
    text = DESCRIPTION_PATH.read_text()
    assert REQUIRED_LINK in text


def test_description_carries_affiliate_disclosure():
    text = DESCRIPTION_PATH.read_text()
    assert REQUIRED_DISCLOSURE_MARKER in text


if __name__ == "__main__":
    import pytest
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
