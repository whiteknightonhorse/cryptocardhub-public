"""Y53 P1-1 gates (G14) — the pSEO catalog is the backbone of the topic list.

news-ingest supplies ~0.05 topics/day by design; the 541-page catalog supplies
~26/day of capacity against a need of 2. These gates hold the four properties
that make the switch safe: the catalog is actually used, its topics carry the
query and the slug back to the page, the 21-day cooldown still applies to them,
and an unreachable catalog falls back instead of stopping the slot.
"""
from __future__ import annotations
import ast
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipeline import dedup, fetch_topic, main as pipeline_main

# The laws, stated here as literals and not read back from the code under test
# (Y49: a gate that imports its threshold from the module it polices moves with
# the mutation and stays green).
COOLDOWN_DAYS = 21
CONVERTING_PAGE_COUNT = 20
PRIORITY_BUCKETS = ("reviews", "faq", "geo", "help")

_GENERATOR = Path(__file__).resolve().parents[2] / "scripts" / "pseo-generate.py"


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(dedup, "_DB_PATH", tmp_path / "published.db")
    return tmp_path


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    d = tmp_path / "pseo"
    d.mkdir()
    monkeypatch.setattr(fetch_topic, "_CONTENT_DIR", d)
    # Neither news nor viral may win a roll while a catalog gate is measuring.
    monkeypatch.setenv("NEWS_POOL_RATIO", "0.0")
    monkeypatch.setenv("VIRAL_POOL_RATIO", "0.0")
    return d


def _write_page(dirpath: Path, slug: str, *, bucket: str = "learn",
                keyword: str | None = None, h1: str | None = None) -> None:
    fm = {
        "title": h1 or f"{slug} title",
        "h1": h1 or f"{slug} title",
        "slug": slug,
        "bucket": bucket,
        "primary_keyword": keyword or slug.replace("-", " "),
    }
    body = "\n".join(f"{k}: {json.dumps(v)}" for k, v in fm.items())
    (dirpath / f"{slug}.md").write_text(f"---\n{body}\n---\n\ntext\n", encoding="utf-8")


def _seed_used(days_ago: float, topic_id: int, channel: str = "youtube") -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    with dedup._conn() as cx:
        cx.execute(
            "INSERT OR REPLACE INTO topic_usage(topic_id, channel, last_used) VALUES (?,?,?)",
            (topic_id, channel, stamp),
        )


def test_catalog_page_becomes_the_topic_with_query_and_slug(ledger, catalog):
    """The point of the source: a topic that carries the page's search query
    and a way back to the page it was built from."""
    _write_page(catalog, "is-redotpay-legit", bucket="reviews",
                keyword="is redotpay legit", h1="Is RedotPay Legit?")
    topic = fetch_topic.pick("youtube", seed=1)
    assert topic["pool"] == "pseo", "the catalog must be the source, not a fallback"
    assert topic["primary"] == "is redotpay legit"
    assert topic["target_pseo_slug"] == "is-redotpay-legit"
    assert topic["target_seconds"] and topic["target_words"], "must be render-ready"


def test_converting_pages_and_priority_buckets_come_first(ledger, catalog):
    """Order is the whole value of the source — a proven page ahead of a
    question-answering bucket, and both ahead of the rest of the catalog."""
    _write_page(catalog, "zzz-filler", bucket="learn")
    _write_page(catalog, "aaa-faq-page", bucket="faq")
    _write_page(catalog, "is-redotpay-legit", bucket="reviews")
    order = [p["slug"] for p in fetch_topic._pseo_pages()]
    assert order[0] == "is-redotpay-legit", "a converting page outranks everything"
    assert order.index("aaa-faq-page") < order.index("zzz-filler"), \
        f"a {PRIORITY_BUCKETS} page must outrank a plain one; got {order}"


def test_a_catalog_page_is_not_filmed_twice_inside_the_cooldown(ledger, catalog):
    """The 21-day window applies to catalog topics exactly as to every other
    source — the hole Y49 closed must not reopen through the new one."""
    # The used page sorts FIRST (same bucket, alphabetically ahead), so the
    # cooldown is the only thing that can move the pick off it.
    _write_page(catalog, "aaa-used-recently", bucket="faq")
    _write_page(catalog, "zzz-never-used", bucket="faq")
    assert fetch_topic._pseo_pages()[0]["slug"] == "aaa-used-recently", "fixture order"

    _seed_used(COOLDOWN_DAYS - 1, fetch_topic._pseo_id("aaa-used-recently"))
    for seed in range(5):
        assert fetch_topic.pick("youtube", seed=seed)["target_pseo_slug"] == "zzz-never-used"

    # ...and it comes back once the window has passed, or the source would
    # burn itself down to nothing.
    _seed_used(COOLDOWN_DAYS + 1, fetch_topic._pseo_id("aaa-used-recently"))
    _seed_used(1.0, fetch_topic._pseo_id("zzz-never-used"))
    assert fetch_topic.pick("youtube", seed=1)["target_pseo_slug"] == "aaa-used-recently"


def test_an_empty_catalog_falls_back_instead_of_stopping_the_slot(ledger, catalog):
    """A missing or unreadable catalog is an outage of one source, not of the
    pipeline: the static pools are still there."""
    topic = fetch_topic.pick("youtube", seed=3)
    assert topic["pool"] in ("finance", "viral")


def test_a_page_without_a_query_is_not_a_topic(ledger, catalog):
    """The catalog is taken FOR the search query. A page without one has
    nothing to offer this source and must not arrive with an empty keyword."""
    (catalog / "broken.md").write_text(
        '---\ntitle: "No keyword here"\nslug: "broken"\nbucket: "faq"\n---\n\ntext\n',
        encoding="utf-8")
    assert fetch_topic._pseo_pages() == []
    assert fetch_topic.pick("youtube", seed=1)["pool"] in ("finance", "viral")


def test_the_title_leads_with_the_search_query(ledger, catalog):
    """Y53: query first — YouTube weighs leading tokens when matching search."""
    _write_page(catalog, "is-redotpay-legit", bucket="reviews",
                keyword="is redotpay legit")
    topic = fetch_topic.pick("youtube", seed=1)
    title = pipeline_main._build_title({"thumbnail_hook": "SCAM? | WE CHECKED"}, topic)
    assert title.lower().startswith("is redotpay legit"), title
    assert "SCAM? — WE CHECKED" in title, "the hook must survive as the payoff"


def test_an_oversized_hook_is_dropped_whole_never_truncated(ledger, catalog):
    """The Y35 incident in its general form: half a hook reads as a truncated
    teaser and killed CTR. If it does not fit, it does not go."""
    _write_page(catalog, "is-redotpay-legit", bucket="reviews",
                keyword="is redotpay legit")
    topic = fetch_topic.pick("youtube", seed=1)
    hook = "A" * 200
    title = pipeline_main._build_title({"thumbnail_hook": hook}, topic)
    assert title == "is redotpay legit", f"expected the bare query, got {title!r}"
    assert "A" not in title, "a hook that does not fit must be dropped, not cut"


def test_the_video_links_to_its_own_page_not_the_homepage(ledger, catalog):
    """The money path. Y50 made the partner a property of the PAGE: a viewer
    of "best crypto card india" who lands on the homepage is shown ether.fi,
    which cannot be issued to them. That bug is only fixed as long as the link
    goes to the page the video is about."""
    _write_page(catalog, "best-crypto-card-india", bucket="geo",
                keyword="best crypto card india")
    topic = fetch_topic.pick("youtube", seed=1)
    assert pipeline_main.topic_path(topic) == "/geo/best-crypto-card-india/"

    caption = pipeline_main._build_caption(
        "youtube", {"thumbnail_hook": "H"}, {"visible": ["#a"], "api": ["a"]},
        topic["id"], pipeline_main.topic_path(topic))
    assert "defycard.com/geo/best-crypto-card-india/?" in caption, caption
    assert "utm_content=" in caption, "the page link must stay attributable"

    # ...and the pinned comment names the SAME destination, or the two split
    # the click intent Y50 consolidated.
    pinned = pipeline_main._build_pinned_yt(
        {}, topic["id"], pipeline_main.topic_path(topic))
    assert "/geo/best-crypto-card-india/" in pinned, pinned


def test_the_wiring_delivers_the_page_link_to_the_uploader(ledger, catalog,
                                                          tmp_path, monkeypatch):
    """Not `_build_caption` in isolation — the description as it ARRIVES at the
    uploader, through run_pipeline.

    Y52's lesson, applied before it can bite again: a helper that builds the
    right string proves nothing if its caller does not pass the argument. Only
    the media steps are stubbed here; the topic -> path -> caption -> upload
    wiring is the real one.
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
    # T-0142-02 (G39): main.py ffprobes voice_path/final.mp4 right after synth
    # and render, so both mocks must leave real files behind. Force the edge
    # provider regardless of a live TTS_PROVIDER=elevenlabs in the real .env —
    # otherwise main.py routes to the unmocked tts_eleven.synthesize, which
    # writes its own ~1s dry-run placeholder and G39 (correctly) flags the
    # mismatch against this test's separately-mocked render() duration.
    from tests.conftest import write_stub_media
    monkeypatch.setattr(pipeline_main._config, "tts_provider", lambda: "edge")
    monkeypatch.setattr(tts_edge, "synthesize",
                         lambda voiceover, platform, out_path, **k: write_stub_media(out_path, 2.0) and {"voice": "v"})
    monkeypatch.setattr(fetch_visuals, "fetch", lambda *a, **k: [{"path": "x"}])
    monkeypatch.setattr(fetch_music, "fetch", lambda *a, **k: {"track": "m"})
    monkeypatch.setattr(captions, "align", lambda *a, **k: [{"word": "words"}])
    monkeypatch.setattr(pipeline_main.render, "render",
                         lambda **k: write_stub_media(k["out_path"], 2.5))
    monkeypatch.setattr(telegram_preview, "send", lambda *a, **k: None)

    seen: dict[str, str] = {}
    monkeypatch.setattr(pub_yt, "upload", lambda **k: seen.update(
        description=k["description"], pinned=k["pinned_comment"]) or {"url": "u"})

    pipeline_main.run_pipeline("youtube", dry_run=True, seed=1)

    assert "/geo/best-crypto-card-india/" in seen["description"], (
        f"the uploader received a description pointing elsewhere:\n{seen['description']}")
    assert "/geo/best-crypto-card-india/" in seen["pinned"], seen["pinned"]


def test_a_topic_with_no_page_keeps_the_old_destinations(ledger, catalog):
    """Y50's behaviour for pool topics is unchanged — this work adds a
    destination for catalog topics, it does not re-route the rest."""
    viral = {"id": 7, "pool": "viral", "title": "t"}
    assert pipeline_main.topic_path(viral) == "/"
    assert "/hub/best-crypto-card-2026/" in pipeline_main._build_pinned_yt({}, 7)


def test_the_converting_list_matches_the_frozen_list_in_the_generator():
    """The two literals are deliberate copies serving different laws (freeze a
    page against rewriting / put a page first in the topic list). Copies drift,
    so drift is what this gate measures: unfreezing a page must force a
    decision about whether the channel still films it."""
    tree = ast.parse(_GENERATOR.read_text(encoding="utf-8"))
    frozen = next(
        {el.value for el in node.value.args[0].elts}
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", "") == "FROZEN_SLUGS" for t in node.targets)
    )
    assert len(frozen) == CONVERTING_PAGE_COUNT, (
        f"scripts/pseo-generate.py freezes {len(frozen)} pages, this gate knows "
        f"{CONVERTING_PAGE_COUNT} — if the revenue surface really changed, "
        f"update both literals and this number, deliberately"
    )
    assert frozen == set(fetch_topic._CONVERTING_SLUGS), (
        "the converting list in fetch_topic.py drifted from FROZEN_SLUGS: "
        f"only here {set(fetch_topic._CONVERTING_SLUGS) - frozen}, "
        f"only there {frozen - set(fetch_topic._CONVERTING_SLUGS)}"
    )


def test_viral_still_runs_and_is_not_a_dead_branch(ledger, catalog, monkeypatch):
    """0.15, not 0.0: a branch reached only on exhaustion of a source with 4.5x
    the needed capacity would never execute, and unexecuted branches are what
    misfired in Y49 and Y52. Held at the ratio the operator can override."""
    _write_page(catalog, "is-redotpay-legit", bucket="reviews")
    monkeypatch.delenv("VIRAL_POOL_RATIO", raising=False)
    assert 0.0 < fetch_topic._viral_ratio() < 1.0
    pools = {fetch_topic.pick("youtube", seed=s)["pool"] for s in range(60)}
    assert "pseo" in pools, "the catalog must carry the majority of slots"
    assert "viral" in pools, "the viral branch must actually run, not just exist"


def test_every_link_the_catalog_can_produce_resolves_in_the_built_site(ledger):
    """The path is built from frontmatter (`/{bucket}/{slug}/`) but SERVED by
    Astro's routing — the two agreeing is an assumption, not a guarantee, and
    a video pointing at a 404 is worse than one pointing at the homepage.

    Graded against dist/, not the sources: this is precisely the gap a bug
    would live in (Y50). Checked for all 541 pages, not a sample, because the
    one page whose bucket disagrees is exactly the one that would slip through.
    """
    dist = Path(__file__).resolve().parents[2] / "seo-site" / "dist"
    if not dist.is_dir():
        pytest.skip("site not built in this checkout")
    pages = fetch_topic._pseo_pages()
    if not pages:
        pytest.skip("seo-site catalog not present in this checkout")
    dead = [p["slug"] for p in pages
            if not (dist / pipeline_main.topic_path(p).strip("/") / "index.html").is_file()]
    assert not dead, (
        f"{len(dead)} catalog pages would be filmed with a link to a page that "
        f"is not served: {dead[:10]}"
    )


def test_the_real_catalog_carries_the_cadence(ledger):
    """Not a fixture: the live catalog must actually hold enough pages for the
    cadence. This is the number the whole work rests on — 541 pages against a
    need of 2/day over a 21-day window."""
    pages = fetch_topic._pseo_pages()
    if not pages:
        pytest.skip("seo-site catalog not present in this checkout")
    need = 2 * COOLDOWN_DAYS
    assert len(pages) >= need, (
        f"catalog holds {len(pages)} usable pages, the cadence needs {need} "
        f"to get through one cooldown window without repeating"
    )
    assert len({p["id"] for p in pages}) == len(pages), "slug ids must not collide"
