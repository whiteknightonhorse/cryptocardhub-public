"""CCH-SEO-25 — a date is labelled as what it is; /about/ promises only what code does.
Reads the BUILT dist; laws are literals."""
from pathlib import Path
import re
import pytest

SEO = Path(__file__).resolve().parent.parent
DIST = SEO / "dist"
FACTS = SEO.parent / "config" / "facts"

pytestmark = pytest.mark.skipif(not (DIST / "about" / "index.html").exists(), reason="dist/ not built")


def test_footer_label_is_last_updated_everywhere():
    # footer label only: one content page legitimately quotes "Last reviewed:" in its own snapshot bullet
    bad = [str(p) for p in DIST.rglob("index.html")
           if 'class="page-meta">Last reviewed' in p.read_text(errors="ignore")]
    assert bad == []
    html = (DIST / "reviews" / "binance-card-review" / "index.html").read_text()
    assert html.count('class="page-meta">Last updated:') == 1


def test_about_makes_no_30_day_promise_and_counts_facts():
    html = (DIST / "about" / "index.html").read_text()
    assert not re.search(r"30 days|thirty days", html, re.I)
    n = len(list(FACTS.glob("*.yaml")))
    assert f"for {n} issuers" in html
