"""G3x — the SLUGIFY/ledger defect class: 15 days idle, $4.13 wasted, 28
"successful" faq-children runs that never grew the corpus.

Root cause was two disagreeing slugify() implementations, wired to a dedup
that RECOMPUTED a slug and compared it to the filesystem instead of reading a
recorded fact. This file locks down the three independent repairs so any one
of them regressing goes red on its own:

  1. dedup reads a RECORDED published path — never a recomputed slug (1b)
  2. a run about to write refuses BEFORE generation if the exact target path
     already exists (1d, would_overwrite)
  3. a pipeline that reports "success" for 3 runs running with a non-empty
     queue and an unmoved metric is stalled, not healthy (1d, no_progress)

Mutation check performed by hand while writing this fix (not automated
here): reverting faq_child_queue()'s dedup to `item["slug"] in
existing_slugs()` turns test_ledger_dedup_reads_fact_not_recompute red,
because the fixture below deliberately makes the recorded path and the
recomputed slug DISAGREE — exactly the historical shape.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
_SPEC = importlib.util.spec_from_file_location("pseo_generate", ROOT / "scripts" / "pseo-generate.py")
pg = importlib.util.module_from_spec(_SPEC)
sys.modules["pseo_generate"] = pg
_SPEC.loader.exec_module(pg)

_NPG_SPEC = importlib.util.spec_from_file_location("no_progress_guard", ROOT / "scripts" / "no_progress_guard.py")
npg = importlib.util.module_from_spec(_NPG_SPEC)
_NPG_SPEC.loader.exec_module(npg)


# ---------------------------------------------------------------------------
# 1. dedup reads a recorded fact, never a recomputed slug
# ---------------------------------------------------------------------------

def test_ledger_dedup_reads_fact_not_recompute(tmp_path, monkeypatch):
    """The exact historical shape: item['slug'] is the WORD-BOUNDARY slug,
    but the file on disk was written under the OLD blind-[:60] name, so the
    two disagree. A recompute-based dedup (`item["slug"] in existing_slugs()`)
    would never find this file and would re-queue it forever. Fact-based
    dedup finds it because the ledger was told the real path at publish time.
    """
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    actual_file = content_dir / "can-i-use-ether-fi-cash-if-i-live-outside-the-supported-coun.md"
    actual_file.write_text("stub", encoding="utf-8")

    ledger = tmp_path / "faq-cohorts.json"
    # faq_child_queue() resolves a recorded path as ROOT / pub["path"], so
    # patch ROOT to tmp_path for this call rather than fight relative_to().
    monkeypatch.setattr(pg, "FAQ_LEDGER", ledger)
    monkeypatch.setattr(pg, "ROOT", tmp_path)
    ledger.write_text(json.dumps({"cohorts": [{"items": [{
        "question": "Can I use ether.fi Cash if I live outside the supported countries?",
        "parent": "/faq/which-crypto-card-works-in-my-country/",
        "parent_fetches": 49,
        "slug": "can-i-use-ether-fi-cash-if-i-live-outside-the-supported",
        "published": {"on": "2026-08-17",
                       "path": "content/can-i-use-ether-fi-cash-if-i-live-outside-the-supported-coun.md"},
    }]}]}), encoding="utf-8")

    queue = pg.faq_child_queue()
    assert queue == [], (
        "a published item whose recorded path exists on disk must NOT be "
        "re-queued, even though its slug does not match the filename"
    )


def test_ledger_dedup_requeues_unpublished_item(tmp_path, monkeypatch):
    """Sanity control: an item with no 'published' fact at all is still
    open work, even if a same-named file happens to exist for other
    reasons — the ledger's own fact is the only thing that closes an item."""
    ledger = tmp_path / "faq-cohorts.json"
    ledger.write_text(json.dumps({"cohorts": [{"items": [{
        "question": "Is ether.fi Cash the same as the ether.fi protocol?",
        "parent": "/reviews/ether-fi-cash-review/",
        "parent_fetches": 44,
        "slug": "is-ether-fi-cash-the-same-as-the-ether-fi-protocol",
    }]}]}), encoding="utf-8")
    monkeypatch.setattr(pg, "FAQ_LEDGER", ledger)
    monkeypatch.setattr(pg, "ROOT", tmp_path)

    queue = pg.faq_child_queue()
    assert len(queue) == 1


# ---------------------------------------------------------------------------
# 2. would_overwrite: known before generation, not after
# ---------------------------------------------------------------------------

def test_would_overwrite_refuses_before_generation(tmp_path):
    content_dir = tmp_path
    (content_dir / "what-is-the-daily-limit-on-crypto-com-card.md").write_text("x", encoding="utf-8")

    primary = pg.KeywordRow(code="P1", bucket="A", bucket_name="test",
                            keyword="What is the daily limit on crypto.com card?")
    cluster = pg.Cluster(primary=primary, supporting=[], pattern="P1", url_segment="faq")

    hit = pg.would_overwrite(cluster, content_dir=content_dir)
    assert hit is not None
    assert hit.name == "what-is-the-daily-limit-on-crypto-com-card.md"


def test_would_overwrite_is_none_for_new_slug(tmp_path):
    primary = pg.KeywordRow(code="P1", bucket="A", bucket_name="test",
                            keyword="A brand new question nobody asked yet?")
    cluster = pg.Cluster(primary=primary, supporting=[], pattern="P1", url_segment="faq")
    assert pg.would_overwrite(cluster, content_dir=tmp_path) is None


# ---------------------------------------------------------------------------
# 3. no_progress: 3 successful runs, non-empty queue, unmoved metric
# ---------------------------------------------------------------------------

def test_no_progress_guard_trips_after_three_stuck_runs(tmp_path):
    for _ in range(2):
        assert npg.check("pipeline-x", metric=5, queue_nonempty=True, state_dir=tmp_path) is False
    assert npg.check("pipeline-x", metric=5, queue_nonempty=True, state_dir=tmp_path) is True


def test_no_progress_guard_resets_on_growth(tmp_path):
    npg.check("pipeline-y", metric=5, queue_nonempty=True, state_dir=tmp_path)
    npg.check("pipeline-y", metric=5, queue_nonempty=True, state_dir=tmp_path)
    # metric grew — the streak must not carry over
    assert npg.check("pipeline-y", metric=6, queue_nonempty=True, state_dir=tmp_path) is False


def test_no_progress_guard_empty_queue_is_not_stalling(tmp_path):
    for _ in range(5):
        assert npg.check("pipeline-z", metric=5, queue_nonempty=False, state_dir=tmp_path) is False
