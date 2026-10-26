"""E-20 (VG_MODE=h1) — the topic source narrows to hot etherfi pSEO pages.

Filters: verified fetches >= 5, resolved partner == etherfi, and no video
already made for that page (permanent, not the 21-day rotation cooldown every
other source uses).

The partner filter (`_h1_primary_partner`) is the SAME three-rule law the page
itself renders its CTAs under (`affiliates.mjs:primaryPartnerFor` /
`pseo-generate.py:primary_partner_for`, Y50), transplanted here rather than
re-derived: explicit `primary_partner` frontmatter wins; else an
ether.fi-unavailable geo slug (`config/availability.yaml`'s
`unavailable_slugs`) routes to the fallback partner; else the default is
etherfi. w4-20.ruling-1 caught a FIRST version of this filter that read only
declared frontmatter (treating "undeclared" as "unconfirmed") — that is a
STRICTER law than the page actually renders under, and on this corpus it
emptied the H1 queue from 6 real candidates to 2, because only 36 of 574 pages
declare `primary_partner` explicitly while the rest still render etherfi via
the default. w4-20.ruling-2 ordered the transplant; see `test_partner_law_...`
below for the literal three-case proof and `test_real_corpus_w37_queue` for
the corpus-level acceptance.

`test_real_corpus_w37_queue` is the acceptance proof: it runs `_h1_candidates`
against the REAL `seo-site/src/content/pseo/` frontmatter (unpatched
`_CONTENT_DIR`) AND the real `config/availability.yaml`, so the partner filter
is exercised on the actual live corpus, not an invented fixture. The `hot`
list below is the FULL frozen snapshot (all 13 rows with fetches >= 5) of the
real, git-ignored `logs/aeo/targets-2026-W37.json` as measured 2026-09-21
(recorded here because that file rotates out of retention and is not
committed); the dedup seed reflects BOTH real facts in `published.db` as of
that date (`ether-fi-cash-review` -> Ob461IDVJdo, `gnosis-pay-review` ->
JvG4iIF8w9o, both 2026-08-13) — w4-20.ruling-3 caught a version of this test
that seeded only the first and asserted a queue of 12, when the live ledger
already excludes a second row too, for a real queue of 11.

The remaining tests below (`test_ordering_and_three_exclusion_reasons` etc.)
use a synthetic tmp catalog to exercise the ordering/exclusion LOGIC in
isolation — they are unit tests of the code, not a claim about what the real
corpus currently contains.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import pytest

from pipeline import _config, compliance, dedup, fetch_topic

VG = Path(__file__).resolve().parent.parent


def _write_page(dirpath: Path, slug: str, *, primary_partner: str | None = "etherfi",
                bucket: str = "faq") -> None:
    fm = {
        "title": f"{slug} title", "h1": f"{slug} title", "slug": slug, "bucket": bucket,
        "primary_keyword": slug.replace("-", " "),
    }
    lines = [f"{k}: {json.dumps(v)}" for k, v in fm.items()]
    if primary_partner is not None:
        lines.insert(4, f"primary_partner: {json.dumps(primary_partner)}")
    (dirpath / f"{slug}.md").write_text("---\n" + "\n".join(lines) + "\n---\n\ntext\n",
                                        encoding="utf-8")


def _write_targets(dirpath: Path, week: str, hot: list[dict]) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / f"targets-{week}.json").write_text(
        json.dumps({"week": week, "hot": hot}), encoding="utf-8")


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(dedup, "_DB_PATH", tmp_path / "published.db")
    return tmp_path


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    d = tmp_path / "pseo"
    d.mkdir()
    monkeypatch.setattr(fetch_topic, "_CONTENT_DIR", d)
    return d


@pytest.fixture
def aeo(tmp_path, monkeypatch):
    d = tmp_path / "aeo"
    monkeypatch.setattr(fetch_topic, "_AEO_DIR", d)
    return d


@pytest.fixture
def h1_mode(monkeypatch):
    monkeypatch.setenv("VG_MODE", "h1")


# Synthetic ordering/exclusion fixture — proves the LOGIC, not the corpus.
# (slug, fetches, primary_partner) — the last two are all >= the floor but
# excluded for two DIFFERENT reasons, one each. An undeclared partner is NOT
# by itself an exclusion reason under the transplanted law (w4-20.ruling-2) —
# it only excludes via `unavailable_slugs`, exercised here with the real
# geo-unavailable slug `best-crypto-card-india` (see `_h1_primary_partner`;
# full three-case proof lives in `test_partner_law_matches_the_page_s_own_law`).
SYNTHETIC_PAGES = [
    ("refer-friend", 41, "etherfi"),
    ("ether-fi-cash-review", 17, "etherfi"),
    ("how-does-ether-fi-cash-work", 12, "etherfi"),
    ("who-can-get-ether-fi-cash", 11, "etherfi"),
    ("does-ether-fi-cash-work-in-europe", 9, "etherfi"),
    ("do-i-need-to-live-in-the-us", 7, "etherfi"),
    ("which-crypto-card-works-in-my-country", 5, "gnosis"),  # explicit different partner
    ("best-crypto-card-india", 5, None),                     # undeclared + real unavailable_slugs geo
]
SYNTHETIC_EXPECTED_ORDER = [
    "refer-friend", "ether-fi-cash-review", "how-does-ether-fi-cash-work",
    "who-can-get-ether-fi-cash", "does-ether-fi-cash-work-in-europe",
    "do-i-need-to-live-in-the-us",
]


def test_ordering_and_two_exclusion_reasons(ledger, catalog, aeo, h1_mode):
    """Synthetic catalog: every hot page HAS a primary_partner declared or
    resolves via the real availability law, so this isolates
    ranking-by-fetches + the floor + the two partner-exclusion reasons
    (explicit mismatch, undeclared-but-geo-unavailable) from the corpus
    itself — that question belongs to test_real_corpus_w37_queue below."""
    for slug, _fetches, partner in SYNTHETIC_PAGES:
        _write_page(catalog, slug, primary_partner=partner)
    _write_targets(aeo, "2026-W37",
                   [{"path": f"/faq/{s}/", "fetches": n} for s, n, _p in SYNTHETIC_PAGES])
    candidates = fetch_topic._h1_candidates("youtube")
    assert [c["slug"] for c in candidates] == SYNTHETIC_EXPECTED_ORDER
    topic = fetch_topic.pick("youtube", seed=1)
    assert topic["pool"] == "h1"
    assert topic["target_pseo_slug"] == "refer-friend"


# Frozen snapshot of the REAL logs/aeo/targets-2026-W37.json (measured
# 2026-09-21; the file itself is gitignored and rotates out of retention, so
# the numbers are recorded here rather than read live). FULL set of rows with
# fetches >= 5 (13 rows, not just the 7 the task's plan text named) — real
# slugs, real corpus paths, ordered as `_h1_candidates` itself orders them
# (fetches desc, path asc on ties).
REAL_W37_HOT = [
    {"path": "/how-to/how-to-refer-friend-ether-fi/", "fetches": 41},
    {"path": "/reviews/ether-fi-cash-review/", "fetches": 17},
    {"path": "/learn/how-does-ether-fi-cash-work/", "fetches": 12},
    {"path": "/faq/who-can-get-ether-fi-cash/", "fetches": 11},
    {"path": "/faq/does-ether-fi-cash-work-in-europe/", "fetches": 9},
    {"path": "/faq/do-i-need-to-live-in-the-us-to-use-ether-fi-cash/", "fetches": 7},
    {"path": "/faq/is-ether-fi-available-in-my-country/", "fetches": 5},
    {"path": "/faq/which-crypto-card-works-in-my-country/", "fetches": 5},
    {"path": "/help/bybit-card-declined/", "fetches": 5},
    {"path": "/learn/do-crypto-cards-work-internationally/", "fetches": 5},
    {"path": "/learn/does-crypto-com-card-work-in-europe/", "fetches": 5},
    {"path": "/learn/how-does-crypto-cashback-work/", "fetches": 5},
    {"path": "/reviews/gnosis-pay-review/", "fetches": 5},
]


def test_real_corpus_w37_queue(ledger, aeo, h1_mode):
    """Acceptance proof — runs against the REAL pSEO corpus frontmatter
    (unpatched `_CONTENT_DIR`) and the REAL `config/availability.yaml`, not an
    invented fixture (w4-20.ruling-1) and not the frontmatter-only law that
    under-filtered it (w4-20.ruling-2), and not a partially-seeded ledger that
    undercounted real exclusions (w4-20.ruling-3).

    Two of the 13 rows are permanently excluded because `published.db`
    already carries a video for them: `ether-fi-cash-review` (Ob461IDVJdo,
    2026-08-13) and `gnosis-pay-review` (JvG4iIF8w9o, 2026-08-13) — both seeded
    below from the real ledger, not invented. None of the remaining 11 slugs
    are geo pages in `config/availability.yaml`'s `unavailable_slugs`, none
    declare a non-etherfi `primary_partner`, and all pass
    `compliance.topic_safe` (measured directly below) — so all 11 render an
    etherfi CTA on the live site and all 11 belong in the H1 queue. This pins
    today's actual queue so a future corpus/filter/availability/ledger change
    is a deliberate, visible diff here.
    """
    _write_targets(aeo, "2026-W37", REAL_W37_HOT)
    # Seed BOTH real dedup facts for 2026-W37 without touching the
    # production ledger (production published.db, ids verified 2026-09-21).
    already_filmed = {
        "ether-fi-cash-review": "Ob461IDVJdo",
        "gnosis-pay-review": "JvG4iIF8w9o",
    }
    for slug, external_id in already_filmed.items():
        dedup.log_publish(channel="youtube", topic_id=fetch_topic._pseo_id(slug),
                          transport="direct", external_id=external_id, status="ok",
                          notes="h1")
    candidates = fetch_topic._h1_candidates("youtube")
    expected = [
        "how-to-refer-friend-ether-fi",
        "how-does-ether-fi-cash-work",
        "who-can-get-ether-fi-cash",
        "does-ether-fi-cash-work-in-europe",
        "do-i-need-to-live-in-the-us-to-use-ether-fi-cash",
        "is-ether-fi-available-in-my-country",
        "which-crypto-card-works-in-my-country",
        "bybit-card-declined",
        "do-crypto-cards-work-internationally",
        "does-crypto-com-card-work-in-europe",
        "how-does-crypto-cashback-work",
    ]
    assert [c["slug"] for c in candidates] == expected
    # compliance.topic_safe is the gate `_pick_h1` applies on top of
    # `_h1_candidates` — every real candidate must also clear it, or the
    # queue this test pins would still be narrower once actually picked.
    assert all(compliance.topic_safe(c) for c in candidates)


def test_a_page_with_a_video_already_made_is_excluded(ledger, catalog, aeo, h1_mode):
    _write_page(catalog, "already-filmed")
    _write_targets(aeo, "2026-W37", [{"path": "/faq/already-filmed/", "fetches": 20}])
    topic_id = fetch_topic._pseo_id("already-filmed")
    dedup.log_publish(channel="youtube", topic_id=topic_id, transport="direct",
                      external_id="yt123", status="ok", notes="h1")
    assert fetch_topic._h1_candidates("youtube") == []


def test_verified_below_the_floor_is_excluded(ledger, catalog, aeo, h1_mode):
    _write_page(catalog, "just-under")
    _write_targets(aeo, "2026-W37", [{"path": "/faq/just-under/", "fetches": 4}])
    assert fetch_topic._h1_candidates("youtube") == []


def test_video_generator_disabled_still_raises_loud_when_h1_exhausted(ledger, catalog, aeo, h1_mode):
    """Exhaustion is loud (Y49) — VG_MODE=h1 with nothing eligible must not
    silently fall through to the finance/viral/news pools."""
    with pytest.raises(RuntimeError, match="VG_MODE=h1"):
        fetch_topic.pick("youtube", seed=1)


def test_exhaustion_names_cooldown_not_the_generic_reason(ledger, catalog, aeo, h1_mode):
    """T-0144: when candidates DO pass the fetch/partner/no-video filters but
    every one is cooling in the topic_failure ledger (the actual W35 root
    cause — a dead writer cooled 15 topics in 2.5h), the exhaustion message
    must name the ledger, not the generic 'below N verified fetches' text
    that names causes which were never true.

    Mutation target: revert the message to the single generic string ->
    this test goes red (match fails)."""
    for slug, fetches, partner in SYNTHETIC_PAGES[:3]:
        _write_page(catalog, slug, primary_partner=partner)
    _write_targets(aeo, "2026-W37",
                   [{"path": f"/faq/{s}/", "fetches": n} for s, n, _p in SYNTHETIC_PAGES[:3]])
    for slug, _fetches, _partner in SYNTHETIC_PAGES[:3]:
        topic_id = fetch_topic._pseo_id(slug)
        dedup.record_topic_failure(topic_id, "youtube", "no output")
    with pytest.raises(RuntimeError, match="cooling in the topic_failure ledger"):
        fetch_topic.pick("youtube", seed=1)


def test_default_mode_is_unaffected(ledger, catalog, aeo, monkeypatch):
    """Mutation target: if the h1 branch in pick() ran unconditionally, this
    regression test goes red — VG_MODE unset must give the pre-E-20 selector."""
    monkeypatch.delenv("VG_MODE", raising=False)
    monkeypatch.setenv("NEWS_POOL_RATIO", "0.0")
    monkeypatch.setenv("VIRAL_POOL_RATIO", "0.0")
    _write_page(catalog, "ordinary-page")
    topic = fetch_topic.pick("youtube", seed=1)
    assert topic["pool"] == "pseo"


def test_partner_law_matches_the_page_s_own_law(ledger, catalog, aeo, h1_mode):
    """The H1 partner filter must be the SAME law the page itself renders its
    CTAs under (`affiliates.mjs:primaryPartnerFor` /
    `pseo-generate.py:primary_partner_for`, Y50), transplanted, not a
    stricter frontmatter-only re-derivation (w4-20.ruling-2). Three literal
    cases, against the REAL `config/availability.yaml` (not mocked):

    (a) explicit non-etherfi frontmatter -> excluded, regardless of geo.
    (b) no field, a real ether.fi-unavailable slug (`best-crypto-card-india`,
        literally in `config/availability.yaml`'s `unavailable_slugs`) ->
        excluded: the page renders a cryptocom CTA on the live site.
    (c) no field, an ordinary slug -> included: the page renders etherfi by
        default, same as 538 of 574 live pages that never declared a partner.

    Mutation target (E-20a, rewritten from "drop the whole partner check" to
    "drop the unavailable_slugs branch, always return etherfi for an
    undeclared page"): case (b) would then let `best-crypto-card-india`
    through, and go red.
    """
    _write_page(catalog, "declared-cryptocom", primary_partner="cryptocom")
    _write_page(catalog, "best-crypto-card-india", primary_partner=None)
    _write_page(catalog, "ordinary-undeclared-page", primary_partner=None)

    assert fetch_topic._h1_primary_partner("declared-cryptocom") == "cryptocom"
    assert fetch_topic._h1_primary_partner("best-crypto-card-india") == "cryptocom"
    assert fetch_topic._h1_primary_partner("ordinary-undeclared-page") == "etherfi"

    _write_targets(aeo, "2026-W37", [
        {"path": "/geo/declared-cryptocom/", "fetches": 50},
        {"path": "/geo/best-crypto-card-india/", "fetches": 50},
        {"path": "/faq/ordinary-undeclared-page/", "fetches": 50},
    ])
    assert [c["slug"] for c in fetch_topic._h1_candidates("youtube")] == [
        "ordinary-undeclared-page",
    ]
