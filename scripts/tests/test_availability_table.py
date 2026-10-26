"""E-08 — the corpus states the issuer's allow-list, and keeps stating it.

Two kinds of law live here and they fail for different reasons:

* the TABLE laws run `scripts/tables/availability-2026-09.tsv` against a frozen
  copy of fifteen pre-edit pages (`fixtures/e08-pre/`) and pin what it does —
  they stay red if an entry is dropped, if the ceiling is removed, or if a
  count is left labelling a list it no longer counts;
* the CORPUS laws read the live `seo-site/src/content/pseo` and pin the state
  the table was applied to reach. A page printed tomorrow that calls Hungary
  unavailable turns them red, which is the point: the issuer says otherwise.

Every expected number and string below is a LITERAL. Computing one by calling
the code under test is the Y49/Y54 defect this project has now paid for twice.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import corpus_edit as ce  # noqa: E402

TABLE = ROOT / "scripts" / "tables" / "availability-2026-09.tsv"
FIXTURES = Path(__file__).parent / "fixtures" / "e08-pre"
CONTENT = ROOT / "seo-site" / "src" / "content" / "pseo"

# Measured on the fifteen frozen pages, 2026-09-14. best-crypto-card-canada is
# the negative control: it carries the "20 countries and 21 US states" claim
# with no country list at all, so the table must leave it alone.
N_FIXTURES = 15
N_CHANGED = 14
N_REPLACEMENTS = 71


@pytest.fixture
def corpus(tmp_path: Path) -> list[Path]:
    for f in FIXTURES.glob("*.md"):
        shutil.copy(f, tmp_path)
    return sorted(tmp_path.glob("*.md"))


@pytest.fixture
def table() -> list[tuple[str, str]]:
    return ce.load_table(TABLE)


# The ceiling laws below run a ONE-ROW table, not the 159-row one: on a
# 15-page sample most of the real table matches nothing, and `apply_table`
# refuses on an unused row by design — the two laws would collide and the
# ceiling's would never be the one that spoke. This row deletes a single
# bullet on a single page, so "how many files a table may change" is the only
# question left in the frame. The real table's own ceiling was exercised live:
# `--expect 262` wrote, `--expect 26` refused (PROGRESS.md#T-w1-08-allow-list).
ONE_ROW = [("\n- **Hungary** — Crypto-payment restrictions on consumer cards",
            "")]


# --------------------------------------------------------------- table laws
def test_the_table_changes_fourteen_of_fifteen_pages(corpus, table):
    st = ce.apply_table(corpus, table, write=True)
    assert st["files"] == N_CHANGED
    assert st["applied"] == N_REPLACEMENTS
    assert (corpus[0].parent / "best-crypto-card-canada.md").read_text(
        encoding="utf-8") == (FIXTURES / "best-crypto-card-canada.md").read_text(
        encoding="utf-8"), "a page with no country list must not be touched"


def test_no_page_calls_hungary_unavailable_afterwards(corpus, table):
    ce.apply_table(corpus, table, write=True)
    assert "".join(f.read_text(encoding="utf-8") for f in corpus).count(
        "Hungary") == 0


def test_the_iso_two_letter_spelling_is_the_same_law(corpus, table):
    """`HU` on 12 pages said what `Hungary` said, in two characters.

    The list grammar that built most of this table reads capitalised NAMES and
    is blind to country codes; the codes were found by diffing a page that had
    already been "finished". Y56: one page, one law, whatever the
    serialization.
    """
    ce.apply_table(corpus, table, write=True)
    joined = "".join(f.read_text(encoding="utf-8") for f in corpus)
    assert not re.search(r"\bHU\b", joined)
    page = (corpus[0].parent / "metamask-card-not-working.md").read_text(
        encoding="utf-8")
    assert "EU (except NL, FI, EE)" in page
    assert "EU (excluding NL/FI/EE)" in page


def test_dropping_one_entry_leaves_hungary_behind(corpus, table):
    """The mutation Y49 asks for: a gate that cannot go red is not a gate.

    The victim is the bullet on crypto-card-mica-eu, the one occurrence no
    other row covers. Dropping a row from the layered middle proves nothing —
    a shorter row underneath catches the name anyway, which is what the
    layering is for.
    """
    victim = "\n- **Hungary** — Crypto-payment restrictions on consumer cards"
    mutated = [(o, n) for o, n in table if o != victim]
    assert len(mutated) == len(table) - 1, "the victim row must exist to drop"
    ce.apply_table(corpus, mutated, write=True)
    assert "- **Hungary**" in (corpus[0].parent
                               / "crypto-card-mica-eu.md").read_text(
        encoding="utf-8")


def test_dropping_the_count_row_leaves_twenty_over_nineteen_names(corpus,
                                                                  table):
    """The second half of the count law: which row enforces it."""
    victim = ("20 countries (Belarus, Bangladesh, China, Cuba, Estonia, "
              "Finland, Hungary")
    mutated = [(o, n) for o, n in table if not o.startswith(victim)]
    assert len(mutated) < len(table), "the victim row must exist to drop"
    ce.apply_table(corpus, mutated, write=True)
    page = (corpus[0].parent / "are-crypto-cards-fdic-insured.md").read_text(
        encoding="utf-8")
    assert "NOT available in 20 countries (Belarus" in page
    assert "Hungary" not in page, "a shorter row still deletes the name"


def test_a_count_never_outlives_the_list_it_counted(corpus, table):
    """`19 countries (…19 names…)`, not `20 countries (…19 names…)`.

    The sentence that carries both is one literal, so a table that deletes the
    name and leaves the number is this test's red.
    """
    ce.apply_table(corpus, table, write=True)
    page = (corpus[0].parent / "are-crypto-cards-fdic-insured.md").read_text(
        encoding="utf-8")
    assert "NOT available in 19 countries (Belarus, Bangladesh, China" in page
    assert "20 countries (Belarus" not in page


def test_ireland_is_removed_from_available_lists_and_kept_in_refusals(corpus,
                                                                     table):
    """Belgium and Ireland are the issuer's own two crosses.

    Deleting the name wherever it appears would have deleted it from the one
    sentence that had it right, and left that sentence's false half — five
    countries the issuer does serve — standing.
    """
    ce.apply_table(corpus, table, write=True)
    page = (corpus[0].parent
            / "mica-compliant-vs-non-compliant-crypto-card.md").read_text(
        encoding="utf-8")
    assert ("Belgium and Ireland are the two European countries the issuer "
            "refuses outright.") in page
    assert "France, Germany, Spain, Italy, Ireland, UK" not in page
    ships = (corpus[0].parent / "crypto-card-comparison.md").read_text(
        encoding="utf-8")
    assert "EU (Austria, Bulgaria, Croatia" in ships
    assert "Austria, Belgium, Bulgaria" not in ships


def test_the_ceiling_refuses_a_table_ten_times_its_measurement(corpus):
    before = {f: f.read_text(encoding="utf-8") for f in corpus}
    st = ce.apply_table(corpus, ONE_ROW, write=True, expect=10)
    assert st["refused"] == "expected 10±2 files, the table changes 1"
    assert all(f.read_text(encoding="utf-8") == t for f, t in before.items())


def test_the_measured_expectation_passes(corpus):
    st = ce.apply_table(corpus, ONE_ROW, write=True, expect=1, tolerance=0)
    assert st["refused"] is None
    assert st["files"] == 1


def test_a_literal_that_matches_nothing_is_a_refusal(corpus):
    st = ce.apply_table(corpus, ONE_ROW + [("Hungary is on the moon", "")],
                        write=True, expect=1, tolerance=0)
    assert st["refused"] == "1 table entries matched nothing"


def test_a_newline_in_the_table_is_a_newline(table):
    """The bullet deletions are multi-line; a TSV cannot hold a raw newline."""
    assert any("\n" in o for o, _ in table)
    assert not any("\\n" in o for o, _ in table)


# -------------------------------------------------------------- corpus laws
def _corpus_text() -> str:
    return "".join(f.read_text(encoding="utf-8")
                   for f in sorted(CONTENT.glob("*.md")))


def _published_surfaces() -> dict[str, str]:
    """Every file that ships a jurisdiction verdict to a reader or an agent.

    Y53, and the reason this test grew a second half: the corpus is not the
    only producer. `seo-site/public/llms-full.txt` is served verbatim at
    `/llms-full.txt` — the surface the answer engines read first — and it
    carried the same "NOT recommended in ... Hungary ..." sentence for four
    months after the issuer's page said otherwise. A gate that watches one
    producer of a claim proves nothing about the others.

    `dist/` is included when it exists: it is what the deploy rsyncs, so a
    stale build that still carries the claim is caught before it is served,
    not after.
    """
    out = {"corpus": _corpus_text()}
    for d in (ROOT / "seo-site" / "public", ROOT / "seo-site" / "dist"):
        if not d.is_dir():
            continue                      # dist/ before the first build
        found = {f.name: f for f in d.glob("llms*.txt")}
        # An empty glob reads exactly like a clean surface. Measured
        # 2026-09-15: an interrupted `pnpm build` left dist/ half-written (no
        # index.html, no sidecars) — had it stopped one step earlier this gate
        # would have gone green over a surface it never opened. A directory
        # that is here owes both files; a zero must carry its cause (Y55).
        assert set(found) == {"llms.txt", "llms-full.txt"}, \
            f"{d} is present but ships {sorted(found)} — surface not measured"
        for f in sorted(found.values()):
            out[str(f.relative_to(ROOT))] = f.read_text(encoding="utf-8")
    return out


def test_the_live_corpus_never_calls_hungary_unavailable():
    for where, text in _published_surfaces().items():
        assert "Hungary" not in text, where


def test_the_brief_for_agents_states_the_allow_list_and_enumerates_no_countries():
    """llms-full.txt may not publish a country blocklist at all.

    Under an allow-list the complement is not a finite list, so any enumeration
    is wrong the day the issuer adds a country — which is exactly how Hungary,
    Bangladesh and the Philippines were published as refused while the issuer
    listed them. The verdict lives in one place, `/geo/availability-matrix/`,
    which is rendered from `config/availability.yaml`.
    """
    text = (ROOT / "seo-site" / "public" / "llms-full.txt").read_text(
        encoding="utf-8")
    assert "NOT recommended** to readers in:" not in text
    assert "`/geo/availability-matrix/`" in text
    assert "allow-list" in text


def test_belgium_and_ireland_appear_only_where_they_are_refused():
    """Measured 2026-09-14: three pages, and what each of them says.

    `Northern Ireland` is a UK region, and Crypto.com's 2024 exit from Ireland
    is a fact about a different issuer — neither is an ether.fi availability
    claim, so both stay.
    """
    hits = {f.stem for f in sorted(CONTENT.glob("*.md"))
            if "Belgium" in f.read_text(encoding="utf-8")
            or "Ireland" in f.read_text(encoding="utf-8")}
    assert hits == {"best-crypto-card-uk",
                    "crypto-com-card-pros-and-cons",
                    "does-crypto-com-card-work-in-europe",
                    "mica-compliant-vs-non-compliant-crypto-card"}
