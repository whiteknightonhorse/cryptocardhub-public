"""pinterest-feed.py gates. Laws are literals here, never read from the script (Y49/Y54)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent.parent
spec = importlib.util.spec_from_file_location("pinterest_feed_t", ROOT / "scripts" / "pinterest-feed.py")
pf = importlib.util.module_from_spec(spec)
sys.modules["pinterest_feed_t"] = pf
spec.loader.exec_module(pf)


def _page(c, slug, bucket="review", date="2026-05-01", title=None, hero=True, gen="2026-05-01T00:00:00+00:00"):
    fm = {"title": title or f"Title of {slug}", "description": "d", "slug": slug, "bucket": bucket,
          "schema_article": ({"datePublished": date} if date else {}), "generated_at": gen,
          "images": [{"src": "/assets/h.jpg"}] if hero else []}
    (c / f"{slug}.md").write_text("---\n" + json.dumps(fm) + "\n---\nbody\n")


@pytest.fixture
def env(tmp_path, monkeypatch):
    c = tmp_path / "content"; c.mkdir()
    pub = tmp_path / "public"; (pub / "assets").mkdir(parents=True)
    Image.new("RGB", (1280, 720), (200, 50, 50)).save(pub / "assets" / "h.jpg")
    _page(c, "new1", date="2026-10-20")
    for i, d in enumerate(["2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05"]):
        _page(c, f"old{i}", date=d)
    _page(c, "news1", bucket="news", date="2026-10-20")
    _page(c, "nohero", hero=False)
    _page(c, "nodate", date=None)
    monkeypatch.setattr(pf, "CONTENT", c)
    monkeypatch.setattr(pf, "PUBLIC", pub)
    monkeypatch.setattr(pf, "PINS", pub / "assets" / "pins")
    monkeypatch.setattr(pf, "MANIFEST", tmp_path / "m.json")
    monkeypatch.setattr(pf, "LOG", tmp_path / "feed.jsonl")
    return tmp_path


def test_first_run_adds_new_plus_three_backfill_only(env):
    st = pf.run()
    m = json.loads((env / "m.json").read_text())
    assert set(m) == {"new1", "old4", "old3", "old2"}
    assert (st["skipped_news"], st["skipped_no_hero"], st["skipped_no_date"]) == (1, 1, 1)
    assert m["old4"]["url"] == "https://defycard.com/review/old4/"


def test_second_run_is_idempotent(env):
    # 5 old pages drain at 3/day: runs 1-2 backfill, run 3 finds nothing left
    pf.run(); pf.run(); pf.run()
    settled = (env / "m.json").read_bytes()
    st = pf.run()
    assert st["rendered"] == 0 and st["rerendered"] == 0
    assert (env / "m.json").read_bytes() == settled


def test_rendered_pin_is_1000x1500_jpeg(env):
    pf.run()
    im = Image.open(env / "public" / "assets" / "pins" / "new1.jpg")
    assert im.format == "JPEG" and im.size == (1000, 1500)


def test_title_change_rerenders_but_keeps_added(env):
    pf.run()
    m1 = json.loads((env / "m.json").read_text())["new1"]
    _page(env / "content", "new1", date="2026-10-20", title="A brand new headline")
    st = pf.run()
    m2 = json.loads((env / "m.json").read_text())["new1"]
    assert st["rerendered"] == 1 and m2["title"] == "A brand new headline" and m2["added"] == m1["added"]
    assert m2["bytes"] != m1["bytes"]


def test_date_is_datePublished_never_generated_at(env):
    for i in range(5, 10):
        _page(env / "content", f"old{i}", date=f"2026-04-0{i-4}")
    pf.run()  # new1 + 3 backfill; 7 old pages remain
    for i in range(10):
        if (env / "content" / f"old{i}.md").exists():
            _page(env / "content", f"old{i}", date=f"2026-05-0{i+1}" if i < 5 else f"2026-04-0{i-4}",
                  gen="2026-10-25T00:00:00+00:00")
    st = pf.run()  # bumped generated_at must not make them "new": still exactly 3 backfill
    assert st["candidates_new"] == 0 and st["rendered"] == 3


def test_old_pages_with_new_generated_at_are_not_new(env):
    _page(env / "content", "old0", date="2026-05-01", gen="2026-10-25T00:00:00+00:00")
    assert pf.run()["candidates_new"] == 1  # only new1
