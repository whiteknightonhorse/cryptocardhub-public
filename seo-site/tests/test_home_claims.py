"""CCH-MONEY-03 — the homepage country count comes from config/availability.yaml, never typed.

Runs against the BUILT site (dist/index.html). 88 on 2026-11-24 (86 + 2 virtual-only).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

SEO = Path(__file__).resolve().parent.parent
ROOT = SEO.parent
INDEX = SEO / "dist" / "index.html"


@pytest.fixture(scope="module")
def html():
    if not INDEX.exists():
        pytest.skip("dist/ not built")
    return INDEX.read_text()


def test_typed_76_is_gone(html):
    assert "76 countries" not in html


def test_count_equals_yaml_sum(html):
    card = yaml.safe_load((ROOT / "config/availability.yaml").read_text())["cards"]["etherfi"]
    want = len(card["countries"]) + len(card.get("countries_virtual_only") or [])
    m = re.search(r"<strong>(\d+) countries</strong>", html)
    assert m, "no '<N> countries' on the homepage"
    assert int(m.group(1)) == want


def test_sum_includes_virtual_only_list():
    card = yaml.safe_load((ROOT / "config/availability.yaml").read_text())["cards"]["etherfi"]
    assert "Belgium" in card["countries_virtual_only"]


def test_fx_clause_has_no_beta_label(html):
    assert "0% FX on EUR" in html
    assert "(beta)" not in html
