"""G23 (Y51 AEO-7) — announce what changed, never the whole site.

IndexNow is the published route into the Bing index, and OpenAI's own docs
name Bing as a ChatGPT Search provider — so this is a direct line into the
answers we are trying to appear in. It is also a protocol that bans keys for
spamming, and "submit all 758 URLs on every deploy" is the spam pattern.

The first run is the subtle case. With no prior state, "which URLs changed" is
unknowable, and the tempting answer — all of them — is exactly the banned one.
So the first run submits nothing and only records hashes.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
_SPEC = importlib.util.spec_from_file_location("indexnow", ROOT / "scripts" / "indexnow-ping.py")
inx = importlib.util.module_from_spec(_SPEC)
sys.modules["indexnow"] = inx
_SPEC.loader.exec_module(inx)

A = "https://defycard.com/reviews/is-redotpay-legit/"
B = "https://defycard.com/geo/best-crypto-card-india/"
C = "https://defycard.com/faq/who-can-get-ether-fi-cash/"

BEFORE = {A: "aaa", B: "bbb", C: "ccc"}


def test_an_unchanged_file_is_not_submitted():
    assert inx.changed_urls(dict(BEFORE), BEFORE) == []


def test_a_changed_file_is_submitted():
    after = {**BEFORE, B: "bbb-EDITED"}
    assert inx.changed_urls(after, BEFORE) == [B]


def test_a_new_page_is_submitted():
    after = {**BEFORE, "https://defycard.com/geo/availability-matrix/": "zzz"}
    assert inx.changed_urls(after, BEFORE) == ["https://defycard.com/geo/availability-matrix/"]


def test_only_the_changed_one_is_submitted_not_its_neighbours():
    after = {**BEFORE, A: "aaa-EDITED"}
    out = inx.changed_urls(after, BEFORE)
    assert out == [A]
    assert B not in out and C not in out


def test_the_first_run_submits_nothing():
    """No previous state means we do not know what changed. Guessing
    'everything' is the pattern IndexNow bans keys for."""
    assert inx.changed_urls(dict(BEFORE), None) == []


def test_a_deleted_page_is_not_submitted():
    after = {A: "aaa", B: "bbb"}
    assert inx.changed_urls(after, BEFORE) == []


def test_a_site_wide_rewrite_is_refused_rather_than_blasted():
    """A migration touching every page is not an edit. The cap is a literal
    here so lifting it in the script alone cannot pass unnoticed."""
    assert inx.MAX_URLS == 200
    big_before = {f"https://defycard.com/p/{i}/": "x" for i in range(758)}
    big_after = {k: "y" for k in big_before}
    assert len(inx.changed_urls(big_after, big_before)) > inx.MAX_URLS


def test_state_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(inx, "STATE", tmp_path / "indexnow-state.json")
    assert inx.load_state() is None            # first run: absent, not empty
    inx.save_state(BEFORE)
    assert inx.load_state() == BEFORE


def test_state_is_content_hashes_not_mtimes():
    """A rebuild that rewrites a file with identical bytes is not a change.
    Hashing the bytes is what makes that true."""
    hashes = inx.current_hashes()
    assert hashes, "no content files found"
    assert all(len(v) == 64 and all(c in "0123456789abcdef" for c in v)
               for v in hashes.values())
    assert hashes == inx.current_hashes()      # stable across calls


def test_the_key_file_is_public_and_self_naming():
    """IndexNow verifies ownership by fetching https://host/<key>.txt and
    expecting the key as its body. It is published on purpose — it is not a
    credential and must never be treated as one."""
    key = inx._key()
    assert key, "no IndexNow key file in seo-site/public/"
    f = ROOT / "seo-site" / "public" / f"{key}.txt"
    assert f.read_text().strip() == key
