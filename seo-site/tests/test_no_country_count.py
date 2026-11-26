"""No page may state ether.fi's retired "76 countries" figure."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(
    r"(?<![0-9-])~?76\+?[- ]+(countr|nation|region|jurisdiction|supported|shipping|eligible)"
)
MESSAGE = (
    "76 is ether.fi's retired country count; no issuer has a sourced 76. "
    "If an issuer publishes 76, add the fact to config/facts/ and exempt it here by fact id."
)


def offenders(root):
    root = Path(root)
    return sorted(
        str(p)
        for name in ("index.html", "index.md")
        for p in root.rglob(name)
        if PATTERN.search(p.read_text(encoding="utf-8", errors="replace"))
    )


def test_dist_has_no_76_country_count():
    assert offenders(ROOT / "dist") == [], MESSAGE


def test_controls(tmp_path):
    pages = {
        "a": "ships to 76 countries",
        "b": "76+ supported countries",
        "c": "Visa works in 176 countries",
        "d": "see /faq/is-there-a-list-of-all-76-supported-countries/",
    }
    for slug, text in pages.items():
        (tmp_path / slug).mkdir()
        (tmp_path / slug / "index.html").write_text(text)
    got = {Path(p).parent.name for p in offenders(tmp_path)}
    assert got == {"a", "b"}, MESSAGE
