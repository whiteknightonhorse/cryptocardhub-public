"""E-09 — the affiliate payout claim is gone from the corpus AND from the writer.

Two halves, and only both together are the fix. Deleting "SCR" from 15 published
pages while the writer's cached system prompt still carries the 2026-04-01 skill
rows means the next FAQ child prints it back on the next 15/45-minute tick —
that is Y50/Y53/Y54's "one producer fixed, the siblings still broadcasting",
three times over.

Every expected value below is a LITERAL. Y49 and Y54 both cost a round to the
same defect: a gate that computes its expectation by calling the code under test
moves with the mutation and stays green.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = ROOT / "scripts"
CONTENT = ROOT / "seo-site" / "src" / "content" / "pseo"
TABLE = SCRIPTS / "tables" / "affiliate-2026-09.tsv"

sys.path.insert(0, str(SCRIPTS))
import corpus_edit  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "pseo_generate", SCRIPTS / "pseo-generate.py")
pg = importlib.util.module_from_spec(_SPEC)
sys.modules["pseo_generate"] = pg
try:
    _SPEC.loader.exec_module(pg)  # type: ignore[union-attr]
except SystemExit:
    pass


def _corpus() -> list[tuple[str, str]]:
    return [(p.stem, p.read_text(encoding="utf-8")) for p in CONTENT.glob("*.md")]


# --- half one: the published corpus -----------------------------------------

def test_no_page_states_the_payout_asset():
    """`grep -rl "\\bSCR\\b" seo-site/src/content/pseo` = 0, the plan's own words.

    Two issuer surfaces disagree (affiliates page: USDC on the 15th; gitbook
    T&C: SCR), so the fact is disputed and the corpus asserts neither.
    """
    guilty = [stem for stem, t in _corpus() if "SCR" in t]
    assert guilty == [], f"pages still naming the payout asset: {guilty}"


def test_no_page_states_a_payout_date_for_the_affiliate_bonus():
    """The invented delays. "1–2 business days" is the one the plan names; the
    other three are the same claim wearing different clothes on the corpus's
    highest-traffic page, and Y53's rule is to grep every producer."""
    invented = ["1–2 business days of qualifying spend",
                "1–2 business days of your friend",
                "within 24–48 hours of your friend",
                "within 24–48 hours of the $100 milestone",
                "~5th–10th of the next month"]
    for phrase in invented:
        guilty = [stem for stem, t in _corpus() if phrase in t]
        assert guilty == [], f"{phrase!r} still published on {guilty}"


def test_physical_card_commission_is_the_tier_table_not_the_stale_range():
    stale = ["10%–25%", "10 %–25 %", "10–25%", "10 %-25 %"]
    for phrase in stale:
        guilty = [stem for stem, t in _corpus() if phrase in t]
        assert guilty == [], f"{phrase!r} still published on {guilty}"

    refer = (CONTENT / "how-to-refer-friend-ether-fi.md").read_text(encoding="utf-8")
    assert "20–25 %" in refer, "the #1 content page lost the corrected figure"


def test_the_disputed_payout_is_replaced_by_where_to_look_it_up():
    """Removal alone would leave a hole. The sanctioned sentence must be there."""
    sanctioned = "affiliates page"
    for stem in ("when-do-referral-bonuses-and-commissions-arrive-in-my",
                 "what-happens-if-my-referred-friend-signs-up-but-never",
                 "how-to-refer-friend-ether-fi"):
        t = (CONTENT / f"{stem}.md").read_text(encoding="utf-8")
        assert sanctioned in t, f"{stem} states nothing in place of the claim"
        assert "verified 2026-09-14" in t


# --- half two: the writer, read at the boundary where it leaves -------------
# Y53: testing the builder instead of the wiring is a unit test in costume. The
# value read here is the one `generate_page` actually hands the model.

def test_the_writer_prompt_carries_the_affiliate_facts_block():
    prompt = pg.build_cached_system_prompt()

    for law in ("THESE OVERRIDE THE SKILL SECTION ABOVE",
                "20-25 % (tiered)",
                "paid in USDC to your ether.fi Cash vault",
                "2026-09-29"):
        assert law in prompt, f"the writer never sees: {law!r}"

    # It must come AFTER the stale rows it contradicts — a rule the model reads
    # BEFORE them is not an override. Anchoring this on the section HEADER
    # instead of on the row itself was the first version of this assert, and the
    # mutation "move the block between the header and the skill text" stayed
    # green: the header is not the claim.
    stale_row = "| Payout currency |"
    # Since 2026-09-29 the grown facts render pushes §1.4 past SKILL.md[:7000], so
    # the row may be absent (nothing left to override). When present, order holds.
    if stale_row in prompt:
        assert prompt.index("THESE OVERRIDE THE SKILL SECTION ABOVE") > \
            prompt.index(stale_row)


def test_the_writer_is_forbidden_the_exact_words_the_corpus_just_lost():
    prompt = pg.build_cached_system_prompt()
    tail = prompt[prompt.index("THESE OVERRIDE THE SKILL SECTION ABOVE"):]
    for word in ("`paid in SCR`", "`rolling payout`", "`within 1-2 business days`",
                 "`24-48 hours`", "`10-25 %` is BANNED", "`12 months`", "`$1,000`"):
        assert word in tail, f"the ban list does not name {word!r}"


# --- the tool's ceiling, which is what makes a corpus edit safe -------------

def _sandbox(tmp_path: Path, pages: dict[str, str]) -> Path:
    d = tmp_path / "pseo"
    d.mkdir()
    for name, body in pages.items():
        (d / f"{name}.md").write_text(body, encoding="utf-8")
    return d


def _run(table: Path, content: Path, expect: int, write: bool = True):
    argv = [sys.executable, str(SCRIPTS / "corpus_edit.py"), "--table", str(table),
            "--expect", str(expect), "--content", str(content)]
    if write:
        argv.append("--write")
    return subprocess.run(argv, capture_output=True, text=True, cwd=ROOT)


def test_a_table_that_changes_far_more_than_declared_writes_nothing(tmp_path):
    """THE ACCIDENT, 2026-08-13: a nine-item job proposed 15,176 edits."""
    pages = {f"p{i}": "cashback is paid in SCR today\n" for i in range(9)}
    content = _sandbox(tmp_path, pages)
    table = tmp_path / "t.tsv"
    table.write_text("paid in SCR\tpaid in the asset on the affiliates page\n",
                     encoding="utf-8")

    r = _run(table, content, expect=2)

    assert r.returncode == 1
    assert "REFUSED" in r.stdout
    assert all("SCR" in (content / f"p{i}.md").read_text(encoding="utf-8")
               for i in range(9)), "it refused and wrote anyway"


def test_a_literal_that_matches_nothing_is_a_refusal_not_a_note(tmp_path):
    """A typo'd literal is a silent no-op, which is exactly how a money claim
    survives its own repair run and the log still says success."""
    content = _sandbox(tmp_path, {"p0": "cashback is paid in SCR today\n"})
    table = tmp_path / "t.tsv"
    table.write_text("paid in SCR\tpaid per the affiliates page\n"
                     "paid in SRC\ttypo, matches nothing\n", encoding="utf-8")

    r = _run(table, content, expect=1)

    assert r.returncode == 1
    assert "matched nothing" in r.stdout
    assert "SCR" in (content / "p0.md").read_text(encoding="utf-8")


def test_both_serializations_of_one_sentence_are_replaced(tmp_path):
    """The same sentence lives twice on a page: as prose, and inside a one-line
    JSON frontmatter value where an en dash is `\\u2013`. Replacing only the
    form that matched first leaves the page contradicting itself."""
    page = ('snapshot_bullets: ["**Bonus:** arrives within 1\\u20132 business days"]\n'
            '\nThe bonus arrives within 1–2 business days.\n')
    content = _sandbox(tmp_path, {"p0": page})
    table = tmp_path / "t.tsv"
    table.write_text("arrives within 1–2 business days\tis paid per the affiliates page\n",
                     encoding="utf-8")

    r = _run(table, content, expect=1)

    assert r.returncode == 0, r.stdout
    out = (content / "p0.md").read_text(encoding="utf-8")
    assert "business days" not in out, out
    # Twice: once in the frontmatter twin, once in the prose. One would mean
    # the applier stopped at whichever serialization it happened to match first.
    assert out.count("is paid per the affiliates page") == 2
    assert "\\u2013" not in out


def test_the_shipped_table_still_changes_exactly_the_declared_15_pages(tmp_path):
    """The table is idempotent against the live corpus (it has already run), so
    the count is measured on a pristine copy restored from git… which the pSEO
    corpus is not under. Instead: re-apply the table to a copy of the corpus
    with the NEW strings swapped back to the OLD ones, i.e. its own inverse."""
    content = tmp_path / "pseo"
    shutil.copytree(CONTENT, content)
    table = corpus_edit.load_table(TABLE)
    for f in content.glob("*.md"):
        t = before = f.read_text(encoding="utf-8")
        for old, new in table:
            for a, b in corpus_edit.serializations(old, new):
                t = t.replace(b, a)
        if t != before:
            f.write_text(t, encoding="utf-8")

    st = corpus_edit.apply_table(sorted(content.glob("*.md")), table,
                                 write=False, expect=15, tolerance=2)

    assert st["refused"] in (None, "4 table entries matched nothing"), st["refused"]
    assert st["files"] == 15, st["changed"]
    # CCH-KW-02 removed the body copies of What-to-watch / Bottom-line / FAQ that
    # the template renders from frontmatter; these four entries lived ONLY in
    # such copies, so they have nothing left to match. Literal prefixes (Y49):
    # any OTHER unused entry is still a typo and still fails.
    gone = ("Referral commissions arrive **rolling**, not batched",
            "[ether.fi's rolling payout model](<<AFFILIATE_URL>>)",
            "A: Sign-up and high-spender bonuses ($10",
            "A: ether.fi and RedotPay accrue rolling")
    assert len(st["unused"]) == 4 and all(
        any(u.startswith(g) for g in gone) for u in st["unused"]), st["unused"]
