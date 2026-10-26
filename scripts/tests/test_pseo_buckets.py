"""G19 (Y51 AEO-3) — new pages are produced where assistants actually read.

The weights come from a measurement, not a preference: fetches per page per
bucket, over 2026-07-28..08-11, by the three answer-time agents.

    hub 12.1 · reviews 10.8 · faq 8.0 · help 5.6 · how-to 3.9
    geo 2.9 · compare 2.9 · learn 2.8 · tools 1.7 · for 0.6

`for` had 79 pages and 0.6 fetches each. It was the second largest bucket on
the site and the least read one on it, because a flat round-robin cannot
notice that. Weight 0 stops production; it deletes nothing.

Every threshold below is a literal. Nothing is computed from BUCKET_WEIGHTS,
because a test that derives its expectation from the table it is checking
stays green when the table is reverted.
"""
from __future__ import annotations

import collections
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
_SPEC = importlib.util.spec_from_file_location("pseo_generate", ROOT / "scripts" / "pseo-generate.py")
pg = importlib.util.module_from_spec(_SPEC)
# Registered before exec: the module defines @dataclass types, and dataclasses
# resolves annotations through sys.modules[cls.__module__].
sys.modules["pseo_generate"] = pg
_SPEC.loader.exec_module(pg)

SAMPLE = 100
# Buckets assistants actually read. Literal list, literal floor.
CITED_BUCKETS = ("reviews", "faq", "hub", "help", "geo")
MIN_CITED_SHARE = 70          # per cent of the next SAMPLE pages
STOPPED_BUCKETS = ("for", "tools")


@pytest.fixture(scope="module")
def clusters():
    return pg.build_clusters(pg.parse_keyword_bank(pg.SKILL_KEYWORDS))


def test_most_of_the_next_hundred_pages_go_to_cited_buckets(clusters):
    counts = collections.Counter(c.url_segment for c in clusters[:SAMPLE])
    cited = sum(counts[b] for b in CITED_BUCKETS)
    assert cited >= MIN_CITED_SHARE, (
        f"only {cited}/{SAMPLE} of the next pages land in {CITED_BUCKETS}: "
        f"{dict(counts)}")


def test_the_two_dead_buckets_are_never_produced_again(clusters):
    """Not 'rarely'. A flat round-robin already produced them 'rarely' and
    still built /for/ into 79 pages."""
    produced = {c.url_segment for c in clusters}
    for b in STOPPED_BUCKETS:
        assert b not in produced, f"{b} is still being generated"


def test_reviews_outnumber_compare_by_at_least_three_to_one(clusters):
    """The single largest measured gap among buckets we still produce
    (10.8 vs 2.9 fetches per page). If the weights are flattened back to
    round-robin these come out equal and this fails."""
    counts = collections.Counter(c.url_segment for c in clusters[:SAMPLE])
    assert counts["reviews"] >= 3 * counts["compare"], dict(counts)


def test_the_generator_is_not_simply_round_robin(clusters):
    """A round-robin gives every produced bucket the same count. Assert the
    distribution is genuinely uneven, independently of any particular pair."""
    counts = collections.Counter(c.url_segment for c in clusters[:SAMPLE])
    assert max(counts.values()) >= 2 * min(counts.values()), dict(counts)


# --- geo: blocked jurisdictions first ---------------------------------------

BLOCKED_FIRST = "best-crypto-card-india"      # not on the issuer's allow-list
NOT_BLOCKED = "best-crypto-card-uk"           # ether.fi works there


def test_a_blocked_jurisdiction_outranks_an_unblocked_one():
    assert pg.geo_priority(BLOCKED_FIRST) == 0
    assert pg.geo_priority(NOT_BLOCKED) == 1


def test_geo_queue_starts_with_jurisdictions_where_the_card_is_blocked(clusters):
    """An honest 'you cannot get this card here, here is what you can get'
    page is the most-cited object this site has: 6 of the 10 clicks assistants
    have ever sent us landed on the India and Turkey pages."""
    geo = [pg.slugify(c.primary.keyword) for c in clusters if c.url_segment == "geo"]
    assert geo, "no geo clusters at all"
    blocked = [s for s in geo if pg.geo_priority(s) == 0]
    assert blocked, "no geo page targets a blocked jurisdiction"
    # every blocked slug precedes every unblocked one
    last_blocked = max(geo.index(s) for s in blocked)
    first_open = min((i for i, s in enumerate(geo) if pg.geo_priority(s) == 1),
                     default=len(geo))
    assert last_blocked < first_open, (
        f"an unblocked country is queued ahead of a blocked one: {geo[:8]}")


def test_weights_are_declared_per_url_segment_not_per_bank_letter():
    """Buckets H and I of the keyword bank both write into /learn/. Weighting
    them separately would hand /learn/ twice its measured share without anyone
    writing that down."""
    learn_letters = [k for k, v in pg.BUCKET_META.items() if v[0] == "learn"]
    assert len(learn_letters) > 1, "fixture assumption changed"
    assert "learn" in pg.BUCKET_WEIGHTS
    assert not set(pg.BUCKET_WEIGHTS) & set(pg.BUCKET_META), \
        "weights are keyed by URL segment, never by bank letter"
