"""CCH-PIN-02 gate — the built feeds, not the sources (Y50). Law literals in the test (Y49)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

import pytest

DIST = Path(__file__).resolve().parent.parent / "dist"
pytestmark = pytest.mark.skipif(
    not (DIST / "pinterest.xml").is_file() or not (DIST / "rss.xml").is_file(),
    reason="seo-site/dist feeds absent — run pnpm build first")

PREFIX = "https://defycard.com/"


def _items(name):
    return ET.parse(DIST / name).getroot().find("channel").findall("item")


@pytest.mark.parametrize("name", ["pinterest.xml", "rss.xml"])
def test_every_link_is_absolute_literal(name):
    items = _items(name)
    assert items
    for it in items:
        assert it.findtext("link").startswith("https://defycard.com/")


def test_pinterest_items_cap_guid_and_enclosure():
    from PIL import Image
    items = _items("pinterest.xml")
    assert 1 <= len(items) <= 50
    guids = [i.findtext("guid") for i in items]
    assert len(set(guids)) == len(guids)
    for it in items:
        enc = it.find("enclosure")
        assert enc is not None, "item without enclosure"
        url = enc.get("url")
        assert url.startswith(PREFIX + "assets/pins/")
        f = DIST / url[len(PREFIX):]
        assert f.is_file()
        with Image.open(f) as im:
            assert im.size == (1000, 1500)


def test_rss_sorted_by_pubdate_desc():
    dates = [parsedate_to_datetime(i.findtext("pubDate")) for i in _items("rss.xml")]
    assert len(dates) <= 50
    assert dates == sorted(dates, reverse=True)
