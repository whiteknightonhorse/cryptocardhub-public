"""CCH-SEO-34 — a named editor, /methodology/ and /editorial-policy/. Reads the BUILT dist; laws are literals."""
from pathlib import Path
import json
import re
import pytest

SEO = Path(__file__).resolve().parent.parent
DIST = SEO / "dist"
REVIEW = DIST / "reviews" / "binance-card-review" / "index.html"

pytestmark = pytest.mark.skipif(not REVIEW.exists(), reason="dist/ not built")


def _ld(html, type_):
    out = []
    for m in re.finditer(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        d = json.loads(m.group(1))
        for n in (d.get("@graph", [d]) if isinstance(d, dict) else d):
            if n.get("@type") == type_:
                out.append(n)
    return out


@pytest.mark.parametrize("path", ["methodology", "editorial-policy"])
def test_new_pages_indexable_canonical_in_sitemap(path):
    html = (DIST / path / "index.html").read_text()
    assert f'<link rel="canonical" href="https://defycard.com/{path}/"' in html
    assert "noindex" not in html
    sm = (DIST / "sitemap-0.xml").read_text()
    assert f"<loc>https://defycard.com/{path}/</loc>" in sm
    assert html.count("<h1") == 1


def test_editor_anchor_and_name():
    html = (DIST / "editorial-policy" / "index.html").read_text()
    assert html.count('id="editor"') == 1
    assert "Evgenii A. is the editor of DefyCard." in html
    assert "we fix confirmed errors within 48 hours" in html
    assert 'href="/disclosure/"' in html
    person = _ld(html, "Person")
    assert person and person[0]["name"] == "Evgenii A."
    assert person[0]["@id"] == "https://defycard.com/editorial-policy/#editor"


def test_byline_is_editor_and_nothing_says_reviewed():
    html = REVIEW.read_text()
    byline = re.search(r'<p class="byline">(.*?)</p>', html, re.S).group(1)
    assert byline.startswith("Editor: ")
    assert 'href="/editorial-policy/#editor"' in byline
    assert byline.count("Evgenii A.") == 1
    bad = [str(p) for p in DIST.rglob("index.html")
           if "· Reviewed" in (t := p.read_text(errors="ignore")) or 'class="byline">By' in t]
    assert bad == []


def test_article_jsonld_editor_person_author_not_person():
    arts = _ld(REVIEW.read_text(), "Article")
    assert arts
    a = arts[0]
    assert a["editor"]["name"] == "Evgenii A."
    assert a["editor"]["@type"] == "Person"
    assert a["author"]["@type"] != "Person"


def test_about_and_footer_link_new_pages():
    about = (DIST / "about" / "index.html").read_text()
    assert 'href="/methodology/"' in about and 'href="/editorial-policy/"' in about
    assert "scoring rubric" not in about
    assert "<h2>Editorial methodology</h2>" not in about
    home = (DIST / "index.html").read_text()
    assert 'href="/methodology/"' in home and 'href="/editorial-policy/"' in home
