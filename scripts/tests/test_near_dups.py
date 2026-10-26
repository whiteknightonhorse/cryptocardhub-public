"""CCH-SEO-08 — near-dup audit. Expected values are LITERALS (Y49)."""
from __future__ import annotations

import functools
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


nd = _load("near_dups", "scripts/near-dups.py")
fc = _load("faq_children", "scripts/faq-children.py")


def _w(prefix, a, b):
    return " ".join(f"{prefix}{i}" for i in range(a, b + 1))


def _page(path, pk, h1, body, gen="2026-05-01"):
    return {"path": path, "url": f"https://defycard.com/x/{path}/", "primary_keyword": pk,
            "h1": h1, "generated_at": gen, "body": body}


A = _page("a", "binance card fees", "Binance Card Fees", _w("a", 1, 40))
B = _page("b", "binance card fees and limits", "Binance Card Limits", _w("b", 1, 40))
C = _page("c", "ether.fi cash travel", "Travel With Ether.fi Cash", _w("c", 1, 40))
D = _page("d", "visa platinum perks", "Platinum Visa Perks", _w("c", 1, 20) + " " + _w("d", 21, 40))


def test_four_pages_give_exactly_two_pairs_with_literal_scores():
    got = nd.find_pairs([A, B, C, D], fc.overlap)
    assert [(p["a"]["path"], p["b"]["path"], p["jaccard"], p["overlap"]) for p in got] == [
        ("a", "b", 0.6667, 0.0),
        ("c", "d", 0.0, 0.3939),
    ]


def _pair(a, b):
    return {"a": a, "b": b, "jaccard": 1.0, "overlap": 0.0}


def _keeper(a, b, human, gsc):
    return nd.pick(_pair(a, b), human, gsc)[0]["path"]


def test_keeper_rule_table():
    old = _page("old", "k", "k", "x", gen="2026-01-01")
    new = _page("new", "k", "k", "x", gen="2026-06-01")
    ok, nk = old["url"].rstrip("/"), new["url"].rstrip("/")
    # human sessions beat impressions
    assert _keeper(old, new, {ok: 1, nk: 5}, {ok: {"impressions": 900}, nk: {"impressions": 1}}) == "new"
    # human tie -> impressions
    assert _keeper(old, new, {ok: 2, nk: 2}, {ok: {"impressions": 10}, nk: {"impressions": 40}}) == "new"
    # all tie -> the older generated_at, whichever side it is on
    assert _keeper(new, old, {}, {}) == "old"
    assert _keeper(old, new, {}, {}) == "old"


def test_instrument_failure_keeps_the_row_with_cause():
    def boom():
        raise RuntimeError("x")
    data, err = nd.guarded(boom)
    assert (data, err) == ({}, "RuntimeError: x")
    md = nd.render(nd.find_pairs([A, B], fc.overlap), {}, {}, {}, {"ga4": err, "gsc": None, "index": None}, "2026-10-19")
    assert "human n/a (RuntimeError: x)" in md
    assert "| 1 | /x/a/ | /x/b/ |" in md


def test_human_by_page_applies_the_direct_engaged_rule():
    rows = [("/p/", "google", 10, 8), ("/p/", "(direct)", 5, 2)]
    assert nd.human_by_page(rows) == {"https://defycard.com/p": 12}
