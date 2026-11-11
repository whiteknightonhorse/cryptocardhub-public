"""CCH-SEO-27 — pure-logic checks; expected values are literals."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "ig", Path(__file__).resolve().parent.parent / "intent-groups.py")
ig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ig)


def m(url, idx, clicks, inb, ga4=0):
    return {"url": url, "indexed": idx, "bing_clicks": clicks, "inbound": inb, "ga4": ga4}


def test_family_key():
    assert ig.family_key("best-crypto-card-for-usdt") == "usdt"
    assert ig.family_key("crypto-card-for-usdt") == "usdt"
    assert ig.family_key("best-crypto-card-usdt-2026") == "usdt"
    assert ig.family_key("bybit-card-review-2026") == "bybit-card-review"


def test_brand_of():
    assert ig.brand_of("crypto-com-card-review") == "crypto.com"
    assert ig.brand_of("is-ether-fi-cash-safe") == "ether.fi"
    assert ig.brand_of("what-is-a-crypto-card") is None


def test_keeper():
    a, b = m("/r/a/", True, 3, 5), m("/r/b/", True, 3, 9)
    assert ig.keeper([a, b]) == "/r/b/"                      # tie on clicks -> inbound
    assert ig.keeper([m("/r/a/", True, 4, 1), b]) == "/r/a/"  # clicks first
    assert ig.keeper([m("/r/a/", False, 0, 1, 4), m("/r/b/", False, 0, 1, 2)]) == "no proposal"
    assert ig.keeper([m("/r/a/", None, 0, 1, 0)]).startswith("no proposal (Google index")
    assert ig.keeper([m("/r/a/", True, "n/a (x)", 1)]).startswith("no proposal (Bing")
