"""G-VERIFY (2026-09-01) — the instrument must not sum two populations.

The 654->99 investigation found `aeo-aggregate.py` counted "ChatGPT-User"
etc. as true whenever the UA STRING matched, which is free to forge. This
file pins the fix: CLAIMED (UA match) and VERIFIED (UA match AND source IP
inside that vendor's own published range) are separate fields that must
never be summed into one number.

THE LAWS ARE LITERALS HERE, same discipline as test_aeo_report.py /
test_aeo_targets.py: nothing here reads its expected value out of the module
under test.
"""
from __future__ import annotations

import importlib.util
import ipaddress
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


aeo = _load("aeo_aggregate", "scripts/aeo-aggregate.py")

WEEK = (date(2026, 8, 3), date(2026, 8, 9))

CHATGPT_UA = ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); "
              "compatible; ChatGPT-User/1.0; +https://openai.com/bot")


def _line(ua, path, status, ip="1.2.3.4"):
    return (f'{ip} - - [05/Aug/2026:10:00:00 +0000] "GET {path} HTTP/1.1" '
            f'{status} 1234 "-" "{ua}"')


REAL_OPENAI_IP = "203.0.113.10"           # inside the fake published range below
SPOOFED_GCP_IP = "34.85.172.162"          # a real Google Cloud address, outside it
RANGES = {"ChatGPT-User": [ipaddress.ip_network("203.0.113.0/24")],
          "Claude-User": [], "Perplexity-User": []}


# --- verify_ip ---------------------------------------------------------------

def test_verify_ip_true_inside_the_published_range():
    assert aeo.verify_ip(REAL_OPENAI_IP, "ChatGPT-User", RANGES) is True


def test_verify_ip_false_outside_the_published_range():
    """The exact shape of the bug: a scanner's UA string matches, its IP
    resolves to a generic cloud address that is not in the vendor's range."""
    assert aeo.verify_ip(SPOOFED_GCP_IP, "ChatGPT-User", RANGES) is False


def test_verify_ip_fails_closed_when_no_ranges_for_the_agent():
    """An agent with an empty/missing range list verifies NOTHING — an outage
    must only ever undercount `verified`, never inflate it back to `claimed`."""
    assert aeo.verify_ip(REAL_OPENAI_IP, "Claude-User", RANGES) is False
    assert aeo.verify_ip(REAL_OPENAI_IP, "Perplexity-User", RANGES) is False


def test_verify_ip_false_on_malformed_ip():
    assert aeo.verify_ip("not-an-ip", "ChatGPT-User", RANGES) is False


# --- claimed vs verified never collapse into one number ----------------------

def test_claimed_counts_every_ua_match_verified_counts_only_confirmed_ones():
    lines = [
        _line(CHATGPT_UA, "/reviews/is-redotpay-legit/", 200, ip=REAL_OPENAI_IP),
        _line(CHATGPT_UA, "/reviews/is-redotpay-legit/", 200, ip=SPOOFED_GCP_IP),
    ]
    agg = aeo.aggregate(lines, *WEEK, ranges=RANGES)
    assert agg["ai_fetches"] == 2                    # CLAIMED: both UA strings matched
    assert agg["verified_ai_fetches"] == 1            # VERIFIED: only the real IP
    assert agg["by_agent"]["ChatGPT-User"] == 2
    assert agg["verified_by_agent"]["ChatGPT-User"] == 1
    # the two fields must stay independently readable, not merged
    assert agg["ai_fetches"] != agg["verified_ai_fetches"]
    # nothing in the aggregate is their sum
    assert 3 not in (agg["ai_fetches"], agg["verified_ai_fetches"])


def test_aggregate_without_ranges_carries_no_verified_keys():
    """Existing callers that never pass `ranges` (e.g. every test in
    test_aeo_report.py / test_aeo_targets.py) must see the old, unchanged
    CLAIMED-only shape — no silent verified=0 masquerading as 'checked'."""
    agg = aeo.aggregate([_line(CHATGPT_UA, "/reviews/is-redotpay-legit/", 200)], *WEEK)
    assert "verified_ai_fetches" not in agg
    assert "verified_by_page" not in agg


# --- fetch_bot_ranges: cache, live fetch, fail-closed on outage -------------

def test_fetch_bot_ranges_fails_closed_with_no_cache_and_a_failing_fetcher(tmp_path, monkeypatch):
    monkeypatch.setattr(aeo, "RANGES_DIR", tmp_path)

    def boom(url):
        raise OSError("network is down")

    nets, meta = aeo.fetch_bot_ranges("ChatGPT-User", fetcher=boom)
    assert nets == []
    assert meta["source"] == "unavailable"


def test_fetch_bot_ranges_uses_a_fresh_cache_without_refetching(tmp_path, monkeypatch):
    monkeypatch.setattr(aeo, "RANGES_DIR", tmp_path)
    cache = tmp_path / "chatgpt_user.json"
    tmp_path.mkdir(exist_ok=True)
    cache.write_text(json.dumps({"prefixes": [{"ipv4Prefix": "203.0.113.0/24"}]}),
                      encoding="utf-8")

    calls = []

    def spy(url):
        calls.append(url)
        raise AssertionError("must not hit the network when the cache is fresh")

    nets, meta = aeo.fetch_bot_ranges("ChatGPT-User", fetcher=spy, now=cache.stat().st_mtime + 60)
    assert calls == []
    assert meta["source"] == "cache"
    assert len(nets) == 1


def test_fetch_bot_ranges_refetches_when_cache_is_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(aeo, "RANGES_DIR", tmp_path)
    cache = tmp_path / "chatgpt_user.json"
    tmp_path.mkdir(exist_ok=True)
    cache.write_text(json.dumps({"prefixes": [{"ipv4Prefix": "203.0.113.0/24"}]}),
                      encoding="utf-8")
    far_future = cache.stat().st_mtime + aeo.RANGES_MAX_AGE_DAYS * 86400 + 1

    def fresh(url):
        return {"prefixes": [{"ipv4Prefix": "198.51.100.0/24"}]}

    nets, meta = aeo.fetch_bot_ranges("ChatGPT-User", fetcher=fresh, now=far_future)
    assert meta["source"] == "live"
    assert str(nets[0]) == "198.51.100.0/24"


# --- build_targets prefers verified, falls back for pre-verification data --

def test_build_targets_prefers_verified_by_page_when_present():
    hot_real = "/faq/who-can-get-ether-fi-cash/"
    scanner_inflated = "/reviews/spoofed-page/"
    existing = {hot_real, scanner_inflated}
    agg = {
        "by_page": {scanner_inflated: 500, hot_real: 9},          # claimed: scanner wins
        "verified_by_page": {hot_real: 9},                         # verified: scanner drops out
    }
    tg = aeo.build_targets(agg, {}, existing)
    assert tg["hot"] == [{"path": hot_real, "fetches": 9}]
    assert tg["cold"] == [scanner_inflated]


def test_build_targets_falls_back_to_claimed_when_verified_key_absent():
    """Pre-verification history rows (or any caller that never verifies) keep
    working exactly as before — this is what test_aeo_targets.py already
    exercises without knowing about `verified_by_page` at all."""
    agg = {"by_page": {"/faq/x/": 9}}
    tg = aeo.build_targets(agg, {}, {"/faq/x/"})
    assert tg["hot"] == [{"path": "/faq/x/", "fetches": 9}]


def test_an_all_unverified_week_is_not_silently_rewritten_back_to_claimed():
    """verified_by_page present but EMPTY (every claim failed IP verification)
    must produce zero hot pages, not fall back to the inflated claimed list."""
    agg = {"by_page": {"/reviews/spoofed/": 500}, "verified_by_page": {}}
    tg = aeo.build_targets(agg, {}, {"/reviews/spoofed/"})
    assert tg["hot"] == []
    assert tg["cold"] == ["/reviews/spoofed/"]
