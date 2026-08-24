"""G20 (Y51 AEO-4) — the answer may move in front of the disclosure; the
disclosure may not leave the paragraph. And the pages that earn money are not
regenerated at all.

Two separate laws, both literal here:

1. FTC 16 CFR 255 wants the affiliate disclosure clear and conspicuous, above
   the fold. It does not want it in sentence one. Variant B therefore leads
   with the answer and keeps the disclosure as the next sentence of the same
   opening paragraph. If the disclosure ever vanishes from a variant-B intro
   this is a compliance failure, not a formatting one — so the generator
   REPAIRS the text and then REFUSES anything it could not repair.

2. Twenty pSEO URLs have produced a register_click (GA4, 2026-08-11). They are
   the entire measured revenue surface of the site. An experiment that
   improves 524 pages and silently rewrites one of those twenty is a loss we
   would not detect for weeks.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
_SPEC = importlib.util.spec_from_file_location("pseo_generate", ROOT / "scripts" / "pseo-generate.py")
pg = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("pseo_generate", pg)
_SPEC.loader.exec_module(pg)

# The literal marker. Typed here, not imported.
MARKER = "may earn a commission"
ANSWER = "Yes — ether.fi Cash ships to 43 countries and pays 3 % back in ETH."


def test_variant_b_keeps_the_disclosure_in_the_opening_paragraph():
    out = pg.apply_intro_variant(f"{ANSWER} The rest of the intro follows.", "answer_first")
    assert MARKER in out, "variant B dropped the FTC disclosure entirely"


def test_variant_b_does_not_lead_with_the_disclosure():
    out = pg.apply_intro_variant(f"{ANSWER} More context here.", "answer_first")
    first_sentence = out.split(". ")[0]
    assert MARKER not in first_sentence, out
    assert out.startswith("Yes —"), out


def test_variant_b_puts_the_disclosure_immediately_after_the_answer():
    """'Above the fold' means the reader meets it in the same breath as the
    answer, not four sentences later."""
    out = pg.apply_intro_variant(
        f"{ANSWER} Second sentence. Third sentence.", "answer_first")
    sentences = out.split(". ")
    assert MARKER in sentences[1], out


def test_variant_b_repairs_a_writer_that_led_with_the_disclosure():
    """The writer is a language model; it will sometimes ignore the brief."""
    llm_output = (f"DefyCard publishes affiliate-linked reviews; we may earn a "
                  f"commission when you sign up through our links. {ANSWER}")
    out = pg.apply_intro_variant(llm_output, "answer_first")
    assert out.startswith("Yes —"), out
    assert MARKER in out


def test_variant_a_still_leads_with_the_disclosure():
    out = pg.apply_intro_variant(f"{ANSWER} And more.", "disclosure_first")
    assert MARKER in out.split(". ")[0], out


def test_validation_rejects_an_intro_with_no_disclosure():
    with pytest.raises(pg.IntroComplianceError):
        pg.validate_intro("Yes, it works in 43 countries. Nothing else here.",
                          "answer_first")


def test_validation_rejects_variant_b_that_still_leads_with_the_disclosure():
    """Otherwise the experiment is not running, it is only relabelled — and
    the eight-week comparison would be between two identical cohorts."""
    with pytest.raises(pg.IntroComplianceError):
        pg.validate_intro(
            "DefyCard publishes affiliate-linked reviews; we may earn a commission "
            "when you sign up through our links. Yes, it works.", "answer_first")


def test_validation_accepts_a_correct_variant_b():
    pg.validate_intro(pg.apply_intro_variant(f"{ANSWER} More.", "answer_first"),
                      "answer_first")


def test_cohort_assignment_is_stable_and_roughly_even():
    """Deterministic by slug: regenerating a page must not move it between
    cohorts, or the two populations quietly swap members over eight weeks."""
    slugs = [f"test-slug-{i}" for i in range(400)]
    first = [pg.pick_intro_variant(s) for s in slugs]
    assert first == [pg.pick_intro_variant(s) for s in slugs]
    b = first.count("answer_first")
    assert 150 <= b <= 250, f"split is {b}/400, not ~50/50"


# --- the freeze -------------------------------------------------------------

# Spelled out again, by hand, from the GA4 read on 2026-08-11.
KNOWN_CONVERTERS = [
    "best-crypto-card-india", "best-crypto-card-turkey", "is-redotpay-legit",
    "ether-fi-cash-review", "how-do-crypto-debit-cards-work",
]


def test_a_converting_page_is_refused_by_the_writer():
    for slug in KNOWN_CONVERTERS:
        with pytest.raises(pg.FrozenPageError):
            pg.assert_not_frozen(slug)


def test_an_ordinary_page_is_not_frozen():
    pg.assert_not_frozen("best-crypto-card-uk")


def test_the_freeze_list_covers_every_converting_url():
    """20 pSEO slugs; the 21st converting URL is `/`, which this generator
    does not produce."""
    assert len(pg.FROZEN_SLUGS) == 20
    for slug in KNOWN_CONVERTERS:
        assert slug in pg.FROZEN_SLUGS


def test_a_frozen_slug_never_enters_the_generation_queue():
    """--full-rebuild sets only_new=False, i.e. 'regenerate everything'. That
    is exactly the switch this guard exists for."""
    clusters = pg.build_clusters(pg.parse_keyword_bank(pg.SKILL_KEYWORDS))
    queue = pg.build_queue(clusters, only_new=False, upgrade_thin=False)
    frozen = [c for c in queue if pg.slugify(c.primary.keyword) in pg.FROZEN_SLUGS]
    assert not frozen, [pg.slugify(c.primary.keyword) for c in frozen]
    # and the sanity check that the fixture is not vacuous
    assert any(pg.slugify(c.primary.keyword) in pg.FROZEN_SLUGS for c in clusters), \
        "no converting page is in the cluster set at all — fixture is meaningless"
