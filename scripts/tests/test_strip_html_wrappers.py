"""CCH-ASIDE-BOLD-1019: the raw model body is Markdown only; HTML is the writer's, never the model's."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
spec = importlib.util.spec_from_file_location("pseo_generate", ROOT / "scripts/pseo-generate.py")
pg = importlib.util.module_from_spec(spec)
sys.modules["pseo_generate"] = pg
spec.loader.exec_module(pg)

ASIDE = "<aside>\n**Risk and disclosure:** x\n</aside>\n"


def test_aside_wrapper_removed():
    assert pg.strip_html_wrappers(ASIDE) == ("**Risk and disclosure:** x\n", 2)


def test_details_summary_becomes_bold_paragraph():
    md = "<details>\n<summary><strong>Q?</strong></summary>\nA.\n</details>\n"
    assert pg.strip_html_wrappers(md)[0] == "**Q?**\n\nA.\n"


def test_placeholders_pass_through_byte_identical():
    md = "See [x](<<AFFILIATE_URL>>).\n\n<<IMAGE_0>>\n\n<<AFFILIATE_BUTTON>>\n"
    assert pg.strip_html_wrappers(md) == (
        "See [x](<<AFFILIATE_URL>>).\n\n<<IMAGE_0>>\n\n<<AFFILIATE_BUTTON>>\n", 0)


def test_write_page_strips_wrapper_in_written_file(monkeypatch, tmp_path):
    content = tmp_path / "content"
    monkeypatch.setattr(pg, "CONTENT_DIR", content)
    monkeypatch.setattr(pg, "localize_images", lambda imgs, slug: [])
    monkeypatch.setattr(pg, "existing_paths", lambda: set())
    row = pg.KeywordRow(code="Z1", bucket="F", bucket_name="How-to", keyword="html wrapper probe")
    cluster = pg.Cluster(primary=row, supporting=[], pattern="P-HowTo", url_segment="how-to")
    payload = {
        "title": "T", "meta_description": "D", "h1": "H",
        "intro_paragraph": "Intro. Read on.", "body_markdown": ASIDE,
    }
    path = pg.write_page(pg.GenerationResult(cluster, payload, {}, 0.0, 0.0), [])
    lines = path.read_text(encoding="utf-8").splitlines()
    assert not any(l.startswith("<aside") for l in lines)
    assert "**Risk and disclosure:** x" in lines


def test_prompt_forbids_html_and_drops_aside_row():
    out = pg.build_cached_system_prompt()
    assert "NO HTML tags of any kind" in out
    assert "| `<aside>` |" not in out
