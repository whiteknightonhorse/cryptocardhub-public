"""CCH-KW-03: supporting-only rows and brand-aware siblings in build_clusters().

gemini-card-review was printed as a Crypto.com review because its two
"supporting" keywords were simply the next two table rows (C22, C23).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
_SPEC = importlib.util.spec_from_file_location("pseo_generate", ROOT / "scripts" / "pseo-generate.py")
pg = importlib.util.module_from_spec(_SPEC)
sys.modules["pseo_generate"] = pg
_SPEC.loader.exec_module(pg)

BANK = """## 3. Bucket C — Reviews (review intent)

| # | Keyword | Vol/mo | Diff | LC | Notes |
|---|---|---|---|---|---|
| C1 | gemini card review | 1,000 | KD 3 | | |
| C2 | is crypto.com card worth it | 500 | KD 9 | | |
| C3 | is crypto.com card legit | 400 | KD 9 | | |
| C4 | gemini credit card | 14,800 | KD 37 | | supporting-only; page = /reviews/gemini-card-review/ |
| C5 | gemini card fees | 90 | KD 1 | | |
| C6 | crypto card complaints | 90 | KD 1 | | |
"""


def _clusters(tmp_path):
    f = tmp_path / "SKILL.md"
    f.write_text(BANK, encoding="utf-8")
    return pg.build_clusters(pg.parse_keyword_bank(f))


def _by_primary(clusters):
    return {c.primary.keyword: [r.keyword for r in c.supporting] for c in clusters}


def test_parser_flags_only_the_supporting_only_row(tmp_path):
    f = tmp_path / "SKILL.md"
    f.write_text(BANK, encoding="utf-8")
    flagged = [r.keyword for r in pg.parse_keyword_bank(f) if r.supporting_only]
    assert flagged == ["gemini credit card"]


def test_a_gemini_primary_is_never_briefed_with_crypto_com_rows(tmp_path):
    sib = _by_primary(_clusters(tmp_path))["gemini card review"]
    assert sib == ["gemini credit card", "gemini card fees"]


def test_a_supporting_only_row_is_never_a_cluster_primary(tmp_path):
    assert "gemini credit card" not in _by_primary(_clusters(tmp_path))


def test_queue_funnel_total_excludes_the_supporting_only_row(tmp_path, monkeypatch):
    monkeypatch.setattr(pg, "existing_slugs", lambda: set())
    assert pg.queue_funnel(_clusters(tmp_path))["clusters_total"] == 5   # 6 rows - 1 supporting-only


def test_brand_of_uses_literal_tokens():
    assert pg.brand_of("etherfi cash review") == "ether.fi"
    assert pg.brand_of("coinbase vs gemini card") == "coinbase"
    assert pg.brand_of("broadcast card") is None
    assert pg.brand_of("best crypto card") is None
