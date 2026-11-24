"""corpus_edit.py's `--watch-table` mode (T-0142-09): append a literal
`what_to_watch` bullet to one page per (slug, line) pair, with the same
ceiling discipline `apply_table` already has (Y49/THE ACCIDENT: a corpus-wide
script must declare how many edits it expects and refuse above that).

Real pages, not invented markdown — same rationale as test_intel_route.py's
`corpus` fixture: the applier's behaviour depends on the real
`what_to_watch: [...]` frontmatter shape, and a hand-written fixture would
only prove it works on a document the site does not contain.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
CONTENT = ROOT / "seo-site" / "src" / "content" / "pseo"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ce = _load("corpus_edit_test", "corpus_edit.py")

# Two real pages known to carry `what_to_watch: [...]` today.
PAGE_A = "best-crypto-card-texas"
PAGE_B = "best-crypto-card-california"
LINE = "**Second card:** [Plasma One](<<AFFILIATE_URL:plasma>>) test bullet."


@pytest.fixture
def corpus(tmp_path):
    out = tmp_path / "pseo"
    out.mkdir()
    for slug in (PAGE_A, PAGE_B):
        text = (CONTENT / f"{slug}.md").read_text(encoding="utf-8")
        assert LINE not in text
        (out / f"{slug}.md").write_text(text, encoding="utf-8")
    return out


def _watch_list(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    m = re.search(r'^what_to_watch:\s*(\[.*\])\s*$', text, re.M)
    return json.loads(m.group(1))


def test_load_watch_table_parses_slug_tab_line(tmp_path):
    table = tmp_path / "t.tsv"
    table.write_text(f"# comment\n\nfoo\t{LINE}\nbar\tanother\n", encoding="utf-8")
    pairs = ce.load_watch_table(table)
    assert pairs == [("foo", LINE), ("bar", "another")]


def test_apply_watch_table_appends_the_line_and_writes(corpus):
    st = ce.apply_watch_table(
        corpus, [(PAGE_A, LINE), (PAGE_B, LINE)], write=True, expect=2, tolerance=0)
    assert st["refused"] is None
    assert st["files"] == 2
    assert LINE in _watch_list(corpus / f"{PAGE_A}.md")
    assert LINE in _watch_list(corpus / f"{PAGE_B}.md")


def test_dry_run_writes_nothing(corpus):
    before = (corpus / f"{PAGE_A}.md").read_text(encoding="utf-8")
    ce.apply_watch_table(corpus, [(PAGE_A, LINE)], write=False, expect=1, tolerance=0)
    assert (corpus / f"{PAGE_A}.md").read_text(encoding="utf-8") == before


def test_bump_generated_only_on_changed_files(corpus):
    before = (corpus / f"{PAGE_A}.md").read_text(encoding="utf-8")
    st = ce.apply_watch_table(
        corpus, [(PAGE_A, LINE)], write=True, expect=1, tolerance=0,
        bump_generated=True)
    assert st["generated_bumped"] == 1
    after = (corpus / f"{PAGE_A}.md").read_text(encoding="utf-8")
    assert re.search(r'generated_at: "[^"]+"', before).group(0) != \
        re.search(r'generated_at: "[^"]+"', after).group(0)


def test_mutation_missing_file_is_refused_not_silently_skipped(corpus):
    """MUTATION: a slug with no matching file (e.g. a typo, or a bare
    placeholder that reverted the writer's own path). A silent skip is
    exactly how the 08-13 slugify defect went unnoticed for 15 days."""
    st = ce.apply_watch_table(
        corpus, [(PAGE_A, LINE), ("does-not-exist", LINE)],
        write=True, expect=2, tolerance=0)
    assert st["refused"] is not None
    assert not LINE in _watch_list(corpus / f"{PAGE_A}.md"), \
        "a refused run must write NOTHING, not just skip the bad entry"


def test_mutation_noop_line_already_present_is_refused(corpus):
    """MUTATION: the exact same line appended twice (a retried --write) mixed
    with one real change — the batch's file count matches --expect exactly,
    so only the noop check (not the count check) can catch the retry."""
    ce.apply_watch_table(corpus, [(PAGE_A, LINE)], write=True, expect=1, tolerance=0)
    st = ce.apply_watch_table(
        corpus, [(PAGE_A, LINE), (PAGE_B, LINE)], write=True, expect=1, tolerance=0)
    assert st["refused"] is not None
    assert "did not apply" in st["refused"]
    assert LINE not in _watch_list(corpus / f"{PAGE_B}.md"), \
        "a refused run must write NOTHING, not apply the good half of the batch"


def test_mutation_expect_mismatch_refuses_before_writing(corpus):
    """MUTATION: --expect set too high (the T-0142-09 ceiling: 11 pages, not
    more) — the run must refuse, not silently apply fewer."""
    st = ce.apply_watch_table(
        corpus, [(PAGE_A, LINE)], write=True, expect=11, tolerance=0)
    assert st["refused"] is not None
    assert LINE not in _watch_list(corpus / f"{PAGE_A}.md")


# --- keep_verified / `#` rows ------------------------------------------------

_DATED = '---\nsnapshot_bullets: ["a"]\n---\n**Last verified:** 2026-09-14\nfoo bar\n'
_UNDATED = '---\nsnapshot_bullets: ["a"]\n---\nfoo bar\n'


def _page(tmp_path, text=_DATED):
    d = tmp_path / "c"
    d.mkdir(exist_ok=True)
    (d / "p.md").write_text(text, encoding="utf-8")
    return d


def test_keep_verified_replaces_phrase_and_keeps_date(tmp_path):
    d = _page(tmp_path)
    st = ce.apply_table([d / "p.md"], [("foo bar", "baz")], write=True,
                        keep_verified=True)
    out = (d / "p.md").read_text(encoding="utf-8")
    assert "baz" in out and "2026-09-14" in out
    assert st["dated"] == 0


def test_keep_verified_adds_no_bullet(tmp_path):
    d = _page(tmp_path, _UNDATED)
    st = ce.apply_table([d / "p.md"], [("foo bar", "baz")], write=True,
                        keep_verified=True)
    assert "Last verified" not in (d / "p.md").read_text(encoding="utf-8")
    assert st["dated"] == 0


def test_default_still_bumps_the_date(tmp_path):
    d = _page(tmp_path)
    ce.apply_table([d / "p.md"], [("foo bar", "baz")], write=True)
    assert "2026-09-14" not in (d / "p.md").read_text(encoding="utf-8")


def test_main_passes_keep_verified(tmp_path, monkeypatch):
    d = _page(tmp_path)
    t = tmp_path / "T.tsv"
    t.write_text("foo bar\tbaz\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "corpus_edit", "--table", str(t), "--expect", "1", "--content", str(d),
        "--write", "--keep-verified"])
    assert ce.main() == 0
    assert "2026-09-14" in (d / "p.md").read_text(encoding="utf-8")


def test_hash_row_is_refused_unless_escaped(tmp_path):
    t = tmp_path / "T.tsv"
    t.write_text("## H\t## G\n", encoding="utf-8")
    with pytest.raises(ValueError):
        ce.load_table(t)
    t.write_text("\\## H\t\\## G\n# comment, no tab\n", encoding="utf-8")
    assert ce.load_table(t) == [("## H", "## G")]


def test_market_figures_table_has_251_rows():
    assert len(ce.load_table(
        ROOT / "scripts" / "tables" / "market-figures-2026-11.tsv")) == 251
