"""CCH-KW-02: the body never carries a section the template renders from a field."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pg = _load("pseo_generate", "scripts/pseo-generate.py")
cli = _load("strip_template_sections", "scripts/strip-template-sections.py")

FULL = {"what_to_watch": ["x"], "bottom_line": ["y"], "faq": [{"q": "q", "a": "a"}]}
INTRO = "Lead.\n\n## Fees\n\nFee text.\n\n---\n\n"
RISK = "## Risk & Disclosure\n\nRisk text.\n"
BODY = (INTRO + "## What to Watch\n\nw\n\n---\n\n## The Bottom Line\n\nb\n\n---\n\n"
        "## Frequently Asked Questions\n\nf\n\n---\n\n" + RISK)


def test_three_sections_go_rest_byte_identical():
    out, n = pg.strip_template_sections(BODY, FULL)
    assert n == 3
    assert out == INTRO + RISK


def test_lowercase_and_punctuation_variants_removed():
    body = "A\n\n## what to watch:\n\nw\n\n## Bottom line\n\nb\n\n## Other\n\no\n"
    out, n = pg.strip_template_sections(body, FULL)
    assert n == 2 and out == "A\n\n## Other\n\no\n"


def test_empty_payload_field_keeps_the_section():
    body = "A\n\n## FAQ\n\nf\n"
    out, n = pg.strip_template_sections(body, {**FULL, "faq": []})
    assert (out, n) == (body, 0)


def test_risk_and_disclosure_and_variants_never_removed():
    body = "A\n\n## What to Watch Going Forward\n\nw\n\n" + RISK
    out, n = pg.strip_template_sections(body, FULL)
    assert (out, n) == (body, 0)


def test_removed_last_block_leaves_no_dangling_rule():
    out, n = pg.strip_template_sections("A\n\n---\n\n## FAQ\n\nf\n", FULL)
    assert n == 1 and out == "A\n"


def test_risk_paragraph_after_faq_survives():
    for risk in ("**Risk & Disclosure:** text\n", "**Risk & Disclosure**\n\nDefyCard publishes.\n",
                 "**Risk + Disclosure:** t\n", "**Risk warning:** t\n"):
        body = "A\n\n## FAQ\n\nf\n\n---\n\n" + risk
        out, n = pg.strip_template_sections(body, FULL)
        assert n == 1 and out == "A\n\n---\n\n" + risk, out


def test_disclosure_shaped_tails_survive_any_label():
    for risk in ("**Disclosure:** DefyCard earns affiliate commissions.\n",
                 "**Important:** DefyCard publishes affiliate-linked reviews.\n",
                 "**DefyCard publishes affiliate-linked reviews; we may earn.**\n",
                 "**Risk warning:** t\n"):
        body = "A\n\n## FAQ\n\n**Q?**\n\nans\n\n" + risk
        out, n = pg.strip_template_sections(body, FULL)
        assert n == 1 and out.endswith(risk) and "ans" not in out, out


def test_unique_inline_figure_survives_duplicate_figure_goes():
    fig = '<figure class="x">\n  <img src="/a/%s.jpg" />\n</figure>'
    body = ("A\n\n" + fig % "one" + "\n\n## Other\n\no\n\n## FAQ\n\nf\n\n"
            + fig % "two" + "\n\n" + fig % "one" + "\n")
    out, n = pg.strip_template_sections(body, FULL)
    assert n == 1 and out.count("/a/two.jpg") == 1 and out.count("/a/one.jpg") == 1, out
    assert "\nf\n" not in out


def _corpus(tmp_path, n):
    for i in range(n):
        (tmp_path / f"p{i}.md").write_text(
            '---\ntitle: "t"\nfaq: [{"q": "q", "a": "a"}]\n---\nA\n\n## FAQ\n\nf\n')
    return tmp_path


def test_cli_expect_mismatch_refuses_with_zero_writes(tmp_path):
    _corpus(tmp_path, 3)
    before = {f.name: f.read_text() for f in tmp_path.glob("*.md")}
    assert cli.run(tmp_path, expect=50, write=True) == 1
    assert {f.name: f.read_text() for f in tmp_path.glob("*.md")} == before


def test_cli_write_strips_and_keeps_frontmatter_bytes(tmp_path):
    _corpus(tmp_path, 3)
    assert cli.run(tmp_path, expect=3, write=True) == 0
    for f in tmp_path.glob("*.md"):
        assert f.read_text() == '---\ntitle: "t"\nfaq: [{"q": "q", "a": "a"}]\n---\nA\n'
