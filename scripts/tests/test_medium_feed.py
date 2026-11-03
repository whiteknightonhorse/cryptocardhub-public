"""CCH-MEDIUM-01: /feed/pages.json, clean markdown copy, G37b. Every expectation is a LITERAL.

Reads the BUILT dist (deploy builds it before the gates); a missing dist is a red test, not a skip."""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import humanize_gate as hg  # noqa: E402

DIST = ROOT / "seo-site" / "dist"
INDIA = "faq/can-i-get-a-crypto-card-in-india-right-now"
SALES = Path("/home/sales/sales-app/style/style-lint.py")
KEYS = {"url", "title", "type", "date_published", "date_modified", "words", "medium_eligible"}
TYPES = {"faq", "learn", "how-to", "geo", "compare", "help", "reviews", "for", "tools", "hub", "news"}
CHROME = ["Home", "On this page", "Related guides", "Related Guides", "Related reading",
          "Skip to content", "Terms apply. Capital at risk.", "Last reviewed:", "Frequently Asked Questions"]


def test_style_lint_is_the_sales_file_verbatim():
    got = hashlib.md5((ROOT / "scripts" / "style-lint.py").read_bytes()).hexdigest()
    assert got == "ca241f901511910c7d7a50633bd86750"
    try:
        assert hashlib.md5(SALES.read_bytes()).hexdigest() == got
    except (PermissionError, FileNotFoundError):
        pass   # the tenant cannot read the sales tree; the dispatcher compares after merge


def test_feed_schema():
    feed = json.loads((DIST / "feed" / "pages.json").read_text(encoding="utf-8"))
    assert 1 <= len(feed) <= 200
    for e in feed:
        assert set(e) == KEYS
        assert e["type"] in TYPES
        assert e["url"].startswith("https://defycard.com/") and e["url"].endswith("/")
        for k in ("date_published", "date_modified"):
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", e[k])
        assert isinstance(e["words"], int) and isinstance(e["medium_eligible"], bool)
    pub = [e["date_published"] for e in feed]
    assert pub == sorted(pub, reverse=True)
    assert "ether.fi/@" not in json.dumps(feed) and "AFFILIATE" not in json.dumps(feed)


def test_markdown_copy_is_article_body_only():
    lines = (DIST / INDIA / "index.md").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# Can I Get a Crypto Card in India Right Now? (2026)"
    assert sum(1 for l in lines if l.startswith("# ")) == 1
    assert sum(l.count("we may earn a commission") for l in lines) == 1
    for bad in CHROME:
        assert not any(l.strip() == bad or l.lstrip("# ").strip() == bad for l in lines), bad
    assert not any(l.startswith("## Related") for l in lines)


def test_india_page_is_style_green():
    r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "style-lint.py"), "--json",
                        str(DIST / INDIA / "index.html")], capture_output=True, text=True)
    res = next(iter(json.loads(r.stdout).values()))
    assert r.returncode == 0 and res["green"]
    assert 1103 <= res["words"] <= 1219 and res["passive_ratio"] <= 0.20


def test_exclamation_and_three_em_dashes_is_not_medium_eligible():
    bad = {"intro_paragraph": "Great card! It is fast — really — and cheap — truly.",
           "body_markdown": "## Fees\n\nFees are low."}
    res = hg.humanize(bad, call=lambda s, u, m: ("{}", {}, 0.0), g37b=True)
    assert res.medium_eligible is False and res.payload == bad   # original kept, never blocked
    ok = {"intro_paragraph": "The card costs $50.", "body_markdown": "## Fees\n\nFees are low."}
    assert hg.humanize(ok, call=lambda s, u, m: ("{}", {}, 0.0), g37b=True).medium_eligible is True
