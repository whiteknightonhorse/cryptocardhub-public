"""E-07b — intel-ingest.py: sources, diffs, extraction, conflicts.

Fixtures are real HTML captured 2026-09-14 (scripts/tests/fixtures/etherfi-
*-2026-09-14.html) — rule 14.09, not invented forms. `last_hash` literals
below are computed ONCE from those fixtures via this module's own
canon_text()/sha256() and then pasted as literals (Y49: the priming setup may
call the code under test — that only sets up the "already matches" state the
law is judged against; the LAW itself, "hash match -> 0 events, hash mismatch
-> 1 event + 1 model call", is asserted independently of how the hash was
computed).

The one deliberately WRONG hash (`_WRONG_HASH`, a sentinel that cannot equal
any real sha256 of real content) is what makes the affiliates source look
"changed" — a literal, not a derived value.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

_SPEC = importlib.util.spec_from_file_location("intel_ingest", ROOT / "scripts" / "intel-ingest.py")
mod = importlib.util.module_from_spec(_SPEC)
sys.modules["intel_ingest"] = mod
_SPEC.loader.exec_module(mod)  # type: ignore[union-attr]

sys.path.insert(0, str(ROOT / "scripts"))
import facts_lib as fl  # noqa: E402

# --- URLs, exactly as config/intel_sources.yaml states them ----------------
URL_COLLECTION = "https://help.ether.fi/en/collections/592872-credit-cards"
URL_758780 = "https://help.ether.fi/en/articles/758780-in-which-countries-is-the-cash-card-available"
URL_262374 = "https://help.ether.fi/en/articles/262374-how-does-cashback-work"
URL_303623 = "https://help.ether.fi/en/articles/303623-what-are-the-transaction-limits-and-fees-for-personal-cash-credit-cards"
URL_758757 = "https://help.ether.fi/en/articles/758757-what-s-the-fx-markup-on-cash-transactions"
URL_AFFILIATES = "https://www.ether.fi/affiliates"

# --- LAW: hashes of the captured fixtures, 2026-09-14 (literals) -----------
HASH_COLLECTION = "6c9a73ed1b60d7178f1acd3c85657652a6d17d10aa6b524fdcef0e46b5f656d6"
HASH_758780 = "30e7206a1f628a54c4f350ec99b8249dfb31e2fc5279f7a3db80f1b23320ce84"
HASH_262374 = "82f3a41f60afb33562540f20b020dadf368b0c6d22d36e5379efa0efff3aaf4e"
HASH_303623 = "3801d1af4a370c25fcec46eaf9ecc85198df38d5705b01134c24e31676f8f733"
HASH_758757 = "de2405ef1379318f66506150a756ccaa7ded05dcabd4eed5ba87cb6eab6ab25f"
N_COLLECTION_IDS = 40
_WRONG_HASH = "0" * 64  # cannot equal a real sha256; forces a "changed" read
_COLLECTION_IDS = ["262374", "262378", "262380", "262387", "262392", "262393", "303623",
                    "304368", "304372", "304385", "304388", "304414", "311478", "316496",
                    "326983", "329136", "330661", "376356", "396925", "429651", "456494",
                    "490457", "546375", "558503", "584661", "586099", "586110", "596673",
                    "604957", "626522", "636570", "670639", "685354", "685724", "685844",
                    "737281", "758757", "825861", "827177", "829813"]


@pytest.fixture(autouse=True)
def _no_real_telegram(monkeypatch):
    """run(dry_run=False) fires telegram_lib.alert() for any non-empty event
    list — every test here goes through run(), and this repo's own .env
    carries live TG_BOT_TOKEN/TG_CHAT_ID, so an unmocked test suite sends real
    messages on every pytest invocation. Autouse so no test can forget it."""
    sent: list[str] = []
    monkeypatch.setattr(mod.telegram_lib, "alert", lambda text, prefix="[cryptocardhub]": sent.append(text) or True)
    return sent


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


RAW = {
    URL_COLLECTION: _fixture("etherfi-592872-2026-09-14.html"),
    URL_758780: _fixture("etherfi-758780-2026-09-14.html"),
    URL_262374: _fixture("etherfi-262374-2026-09-14.html"),
    URL_303623: _fixture("etherfi-303623-2026-09-14.html"),
    URL_758757: _fixture("etherfi-758757-2026-09-14.html"),
    URL_AFFILIATES: _fixture("etherfi-affiliates-2026-09-14.html"),
}


def _yaml_config(*, affiliates_hash: str) -> str:
    """Hand-written, same shape as config/intel_sources.yaml. The five
    non-affiliates sources are primed with their REAL fixture hash/id-set (so
    they read as unchanged); `affiliates_hash` controls the one source under
    test."""
    ids_literal = "[" + ", ".join(f'"{i}"' for i in _COLLECTION_IDS) + "]"
    return f"""\
sources:
  - id: etherfi-collection-cash
    kind: intercom_collection
    url: "{URL_COLLECTION}"
    parser: intercom_collection_ids
    watch_fields: ["article_ids"]
    confidence: issuer_help
    poll: daily
    last_hash: "{HASH_COLLECTION}"
    last_updated_text: null
    last_ok: "2026-09-13"
    last_article_ids: {ids_literal}

  - id: etherfi-758780-availability
    kind: intercom_article
    url: "{URL_758780}"
    parser: intercom_stream
    watch_fields: ["physical card available"]
    confidence: issuer_help
    poll: daily
    last_hash: "{HASH_758780}"
    last_updated_text: "2026-09-13T05:00:50Z"
    last_ok: "2026-09-13"

  - id: etherfi-262374-cashback
    kind: intercom_article
    url: "{URL_262374}"
    parser: intercom_stream
    watch_fields: ["cashback rate"]
    confidence: issuer_help
    poll: daily
    last_hash: "{HASH_262374}"
    last_updated_text: "2026-09-12T04:42:08Z"
    last_ok: "2026-09-13"

  - id: etherfi-303623-limits-fees
    kind: intercom_article
    url: "{URL_303623}"
    parser: intercom_stream
    watch_fields: ["atm limit"]
    confidence: issuer_help
    poll: daily
    last_hash: "{HASH_303623}"
    last_updated_text: "2026-09-09T18:20:00Z"
    last_ok: "2026-09-13"

  - id: etherfi-758757-fx-markup
    kind: intercom_article
    url: "{URL_758757}"
    parser: intercom_stream
    watch_fields: ["fx markup"]
    confidence: issuer_help
    poll: daily
    last_hash: "{HASH_758757}"
    last_updated_text: "2026-08-01T08:15:27Z"
    last_ok: "2026-09-13"

  - id: etherfi-affiliates-page
    kind: html
    url: "{URL_AFFILIATES}"
    parser: html_visible_text
    watch_fields: ["payout_currency", "payout_cadence", "physical_card_commission"]
    confidence: issuer_legal
    poll: daily
    last_hash: "{affiliates_hash}"
    last_updated_text: null
    last_ok: "2026-09-13"
"""


EXTRACTED_STUB = [
    {"fact_id": "etherfi.affiliate.payout_currency", "old": None, "new": "USDC",
     "quote": "Rewards are paid in USDC directly into your Vault with Cash on the 15th of the following month."},
    {"fact_id": "etherfi.affiliate.physical_card_commission.t4", "old": "20%", "new": "25%",
     "quote": "Physical card commission Tier 4 25%"},
]


def _fake_fetch(calls_log):
    def fetch(url: str, timeout: int = 30) -> str:
        calls_log.append(url)
        if url not in RAW:
            raise AssertionError(f"test fetch stub asked for unknown url: {url}")
        return RAW[url]
    return fetch


def _fake_call_claude_cli(calls_log, extracted=None):
    payload = json.dumps(extracted if extracted is not None else EXTRACTED_STUB)

    def call_claude_cli(system_prompt, user_message, model=mod.MODEL, timeout_s=180):
        calls_log.append(user_message)
        return payload, {}, 0.01
    return call_claude_cli


def _setup(tmp_path, monkeypatch, *, affiliates_hash: str):
    cfg_path = tmp_path / "intel_sources.yaml"
    cfg_path.write_text(_yaml_config(affiliates_hash=affiliates_hash), encoding="utf-8")
    monkeypatch.setattr(mod, "SOURCES_PATH", cfg_path)
    monkeypatch.setattr(mod, "EVENTS_PATH", tmp_path / "events.jsonl")
    monkeypatch.setattr(mod, "SNAPSHOT_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(mod, "CONTENT_DIR", tmp_path / "no-content")  # empty -> score's pages_affected = 0
    monkeypatch.setattr(mod, "AEO_DIR", tmp_path / "no-aeo")
    fetch_calls: list[str] = []
    monkeypatch.setattr(mod, "fetch", _fake_fetch(fetch_calls))
    return cfg_path, fetch_calls


# =============================================================================
# ACCEPTANCE (plan's own words)
# =============================================================================

def test_affiliates_change_yields_changed_event_with_required_extracted_facts(tmp_path, monkeypatch):
    cfg_path, fetch_calls = _setup(tmp_path, monkeypatch, affiliates_hash=_WRONG_HASH)
    model_calls: list[str] = []
    monkeypatch.setattr(mod, "call_claude_cli", _fake_call_claude_cli(model_calls))

    events = mod.run(dry_run=False)

    changed = [e for e in events if e["kind"] == "changed"]
    assert len(changed) == 1, events
    ev = changed[0]
    assert ev["source_id"] == "etherfi-affiliates-page"
    assert ev["decision"] == "pending"
    got = {(item["fact_id"], item["new"]) for item in ev["extracted"]}
    assert {("etherfi.affiliate.payout_currency", "USDC"),
            ("etherfi.affiliate.physical_card_commission.t4", "25%")} <= got

    # Model called EXACTLY once for the whole run (five sources unchanged).
    assert len(model_calls) == 1

    # Event actually landed on disk, and the source's state advanced.
    lines = mod.EVENTS_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["kind"] == "changed"
    import yaml
    new_cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    aff = next(s for s in new_cfg["sources"] if s["id"] == "etherfi-affiliates-page")
    assert aff["last_hash"] != _WRONG_HASH
    assert aff["last_ok"] == mod.date.today().isoformat()


def test_all_sources_unchanged_yields_zero_events_and_zero_model_calls(tmp_path, monkeypatch):
    real_affiliates_hash = mod.sha256(mod.canon_text("html", RAW[URL_AFFILIATES]))
    _setup(tmp_path, monkeypatch, affiliates_hash=real_affiliates_hash)
    model_calls: list[str] = []
    monkeypatch.setattr(mod, "call_claude_cli", _fake_call_claude_cli(model_calls))

    events = mod.run(dry_run=False)

    assert events == []
    assert len(model_calls) == 0
    assert not mod.EVENTS_PATH.exists() or mod.EVENTS_PATH.read_text(encoding="utf-8") == ""


def test_a_404_writes_fetch_failed_not_silence(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, affiliates_hash=_WRONG_HASH)

    def flaky_fetch(url, timeout=30):
        if url == URL_303623:
            raise RuntimeError("HTTP Error 404: Not Found")
        return RAW[url]
    monkeypatch.setattr(mod, "fetch", flaky_fetch)
    monkeypatch.setattr(mod, "call_claude_cli", _fake_call_claude_cli([]))

    events = mod.run(dry_run=False)

    failed = [e for e in events if e["kind"] == "fetch_failed"]
    assert len(failed) == 1
    assert failed[0]["source_id"] == "etherfi-303623-limits-fees"
    assert failed[0]["decision"] is None
    # Not treated as "no change": state must NOT advance for this source.
    import yaml
    cfg_path = mod.SOURCES_PATH
    new_cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    row = next(s for s in new_cfg["sources"] if s["id"] == "etherfi-303623-limits-fees")
    assert row["last_hash"] == HASH_303623, "fetch failure must not silently bump state"


# =============================================================================
# MUTATION 1 (plan): "hash не сравнивается -> 2 вызова модели -> красный"
# =============================================================================
# The primary acceptance test above proves the count is 1 when only ONE
# source really changed. This test proves the hash-compare gate is the thing
# holding that count down: prime every non-collection source with the SAME
# wrong sentinel (i.e. simulate "the stored hash never matches, as if it were
# never compared") and show the model call count tracks the number of
# sources that read as changed, not a fixed 1. A version of process_source()
# that dropped the `prev_hash != new_hash` check would make BOTH tests above
# read "changed" for every source regardless -- this test is what turns red
# in that world (5 calls expected here would silently also become 5 in the
# "only affiliates really changed" test, which asserts == 1 and would catch
# it directly; this test independently pins the mechanism).

def test_multiple_wrong_hashes_call_the_model_once_per_mismatch_not_once_total(tmp_path, monkeypatch):
    cfg_path, _ = _setup(tmp_path, monkeypatch, affiliates_hash=_WRONG_HASH)
    # Force every intercom_article source to also read as "changed".
    text = cfg_path.read_text(encoding="utf-8")
    for h in (HASH_758780, HASH_262374, HASH_303623, HASH_758757):
        text = text.replace(f'last_hash: "{h}"', f'last_hash: "{_WRONG_HASH}"', 1)
    cfg_path.write_text(text, encoding="utf-8")

    model_calls: list[str] = []
    monkeypatch.setattr(mod, "call_claude_cli", _fake_call_claude_cli(model_calls))
    events = mod.run(dry_run=False)

    # 5 non-collection sources, all wrong -> 5 changed events, 5 model calls.
    # (If hash comparison were not load-bearing, this and the "==1" test above
    # would read identically -- they do not, which is the point.)
    assert len([e for e in events if e["kind"] == "changed"]) == 5
    assert len(model_calls) == 5


# =============================================================================
# MUTATION 2 (plan): "fetch_failed не пишется -> красный"
# =============================================================================
# Covered functionally by test_a_404_writes_fetch_failed_not_silence above.
# This test pins the STRUCTURAL guarantee: fetch_failed is written from the
# generic `except Exception` in process_source(), so ANY fetch failure -- not
# just a 404 -- produces the event and the run does not abort. Removing that
# except branch (the mutation) would raise straight through run(), which is
# an even louder failure than "0 events" -- this test would go red either way
# (RuntimeError instead of a clean list).

def test_every_source_failing_still_produces_one_fetch_failed_event_each(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, affiliates_hash=_WRONG_HASH)

    def always_fails(url, timeout=30):
        raise TimeoutError("simulated network failure")
    monkeypatch.setattr(mod, "fetch", always_fails)

    events = mod.run(dry_run=False)
    assert len(events) == 6
    assert all(e["kind"] == "fetch_failed" for e in events)
    assert all(e["decision"] is None for e in events)


# =============================================================================
# extract_failed: a model failure on ONE source must not abort the run for
# every other source (ruling-1 on w1-07b-intel-ingest-py: extract_facts
# raising propagated straight through process_source() and out of run()'s
# for-loop, so a single Sonnet failure lost every event and every state
# update, even for sources already processed successfully in that same loop
# iteration).
# =============================================================================

def test_extract_failure_writes_event_and_does_not_abort_other_sources(tmp_path, monkeypatch):
    cfg_path, _ = _setup(tmp_path, monkeypatch, affiliates_hash=_WRONG_HASH)
    # Force 758780 to ALSO read as changed, so it is a second source processed
    # in the same loop -- proves a later failure doesn't erase an earlier
    # source's already-returned event/state-update.
    text = cfg_path.read_text(encoding="utf-8")
    text = text.replace(f'last_hash: "{HASH_758780}"', f'last_hash: "{_WRONG_HASH}"', 1)
    cfg_path.write_text(text, encoding="utf-8")

    def flaky_extract(system_prompt, user_message, model=mod.MODEL, timeout_s=180):
        if "758780" in user_message:  # url embedded in extract_facts' user_msg
            raise RuntimeError("claude CLI exit 1: stderr='api_error'")
        return json.dumps(EXTRACTED_STUB), {}, 0.01
    monkeypatch.setattr(mod, "call_claude_cli", flaky_extract)

    events = mod.run(dry_run=False)

    failed = [e for e in events if e["kind"] == "extract_failed"]
    changed = [e for e in events if e["kind"] == "changed"]
    assert len(failed) == 1 and failed[0]["source_id"] == "etherfi-758780-availability"
    assert failed[0]["decision"] is None
    assert len(changed) == 1 and changed[0]["source_id"] == "etherfi-affiliates-page"

    import yaml
    new_cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in new_cfg["sources"]}
    # The source whose extraction failed must NOT advance -- next run has to
    # see the same real change again, not a hash that already "saw" it.
    assert by_id["etherfi-758780-availability"]["last_hash"] == _WRONG_HASH
    # The source that succeeded must still advance despite the sibling's failure.
    assert by_id["etherfi-affiliates-page"]["last_hash"] != _WRONG_HASH

    # No snapshot written for the failed source (next run must re-diff the
    # same real change, not an empty one); the succeeded source DID snapshot.
    assert not (mod.SNAPSHOT_DIR / "etherfi-758780-availability.txt").exists()
    assert (mod.SNAPSHOT_DIR / "etherfi-affiliates-page.txt").exists()


# =============================================================================
# dry-run must write NOTHING TO DISK (ruling-2 on w1-07b-intel-ingest-py):
# `_save_snapshot` used to run unconditionally, so a `--dry-run` right before
# the real cron run silently consumed the one real diff a human would ever
# see -- the next real run re-diffed a now-identical snapshot and sent Sonnet
# an empty diff for a change that had genuinely happened.
# =============================================================================

def test_dry_run_never_advances_snapshot_or_state_and_a_real_run_after_it_still_sees_the_change(
    tmp_path, monkeypatch,
):
    cfg_path, _ = _setup(tmp_path, monkeypatch, affiliates_hash=_WRONG_HASH)
    # Pre-seed a snapshot as if a prior REAL run had already captured OLDER
    # text -- different from what the fixture returns now, so there is an
    # actual diff for extract_facts to be called with.
    mod.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    old_text = "OLD AFFILIATES SNAPSHOT -- payout in BTC, no USDC mention at all"
    (mod.SNAPSHOT_DIR / "etherfi-affiliates-page.txt").write_text(old_text, encoding="utf-8")

    model_calls: list[str] = []
    monkeypatch.setattr(mod, "call_claude_cli", _fake_call_claude_cli(model_calls))

    mod.run(dry_run=True)
    assert len(model_calls) == 1
    assert "OLD AFFILIATES SNAPSHOT" in model_calls[0], "dry-run preview must see the real diff"
    # Snapshot on disk must be byte-identical to what it was before the dry
    # run -- this is the mutation that must go red: reintroducing an
    # unconditional _save_snapshot() would overwrite it here.
    assert (mod.SNAPSHOT_DIR / "etherfi-affiliates-page.txt").read_text(encoding="utf-8") == old_text
    import yaml
    cfg_after_dry = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    aff = next(s for s in cfg_after_dry["sources"] if s["id"] == "etherfi-affiliates-page")
    assert aff["last_hash"] == _WRONG_HASH, "dry-run must not advance state either"

    model_calls.clear()
    mod.run(dry_run=False)
    assert len(model_calls) == 1
    # The real run's diff must STILL contain the actual change -- proving the
    # dry run above did not silently consume it by overwriting the snapshot.
    assert "OLD AFFILIATES SNAPSHOT" in model_calls[0]


# =============================================================================
# Unit-level laws (literals — Y49)
# =============================================================================

def test_collection_ids_literal():
    ids = mod._collection_article_ids(RAW[URL_COLLECTION])
    assert len(ids) == N_COLLECTION_IDS
    assert "330661" in ids   # the disputed daily-spend-limit article — visible, NOT auto-tracked
    assert "758780" not in ids  # a different collection


def test_fx_markup_article_with_no_leading_heading_is_not_dropped():
    """758757 has no heading block before its first paragraph -- the bug this
    module's own docstring documents finding in availability-check.py's
    parse_intercom_stream. Regression pin."""
    text = mod.canon_text("intercom_article", RAW[URL_758757])
    assert "0 to 0.5%" in text
    assert len(text) > 0


def test_conflicts_flags_a_value_that_disagrees_with_the_standing_fact():
    facts = fl.load()  # real config/facts/etherfi.yaml
    extracted = [
        {"fact_id": "etherfi.affiliate.physical_card_commission.t4", "old": "20%", "new": "30%",
         "quote": "..."},  # disagrees with the real fact (25%)
        {"fact_id": "etherfi.rate.cashback_lock_days", "old": "7", "new": "7", "quote": "..."},  # matches
    ]
    conflicts = mod.find_conflicts(extracted, facts)
    ids = {c["fact_id"] for c in conflicts}
    assert ids == {"etherfi.affiliate.physical_card_commission.t4"}


def test_conflicts_does_not_flag_a_value_that_agrees_once_units_are_stripped():
    """ruling-3: `find_conflicts` compared `str(value)` against `new` and
    ignored `unit` entirely — 16 of 18 real facts carry a unit, so a CORRECT
    extraction like '25%' against `value: 25, unit: '%'` false-conflicted on
    every single one, which (D4) kicks a true fact to `disputed` and drops it
    out of publish. Literal law, not derived from the function under test."""
    facts = fl.load()  # real config/facts/etherfi.yaml
    extracted = [
        {"fact_id": "etherfi.affiliate.physical_card_commission.t4", "old": "20%", "new": "25%",
         "quote": "..."},  # agrees with the real fact (value: 25, unit: "%")
    ]
    assert mod.find_conflicts(extracted, facts) == []


def test_pages_affected_bucket_literal():
    assert mod.pages_affected_bucket(0) == 0
    assert mod.pages_affected_bucket(1) == 1
    assert mod.pages_affected_bucket(10) == 1
    assert mod.pages_affected_bucket(11) == 2
    assert mod.pages_affected_bucket(100) == 2
    assert mod.pages_affected_bucket(101) == 3


def test_score_is_zero_when_the_old_literal_appears_on_no_page(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CONTENT_DIR", tmp_path / "empty")
    monkeypatch.setattr(mod, "AEO_DIR", tmp_path / "empty-aeo")
    assert mod.score("availability", "a literal that appears nowhere") == 0.0


# =============================================================================
# PSEO_ENABLED gate (D3: intel-ingest.py reads it, same law as E-02)
# =============================================================================

def test_pseo_disabled_skips_before_any_fetch(monkeypatch):
    monkeypatch.setenv("PSEO_ENABLED", "false")
    calls: list[str] = []
    monkeypatch.setattr(mod, "fetch", lambda url, timeout=30: calls.append(url) or "")
    rc = mod.main([])
    assert rc == 0
    assert calls == [], "fetch must never run while PSEO_ENABLED=false"


# =============================================================================
# T-CCH-INTEL-429-1005: --from-file + transition-only Telegram
# =============================================================================

_AFF = "etherfi-affiliates-page"
_429 = "HTTPError: HTTP Error 429: Too Many Requests"


def _no_fetch(url, timeout=30):
    raise AssertionError("fetch must not be called")


def test_from_file_runs_the_same_pipeline_without_fetching(tmp_path, monkeypatch):
    cfg_path, _ = _setup(tmp_path, monkeypatch, affiliates_hash=_WRONG_HASH)
    monkeypatch.setattr(mod, "fetch", _no_fetch)
    monkeypatch.setattr(mod, "pseo_enabled", lambda: True)
    model_calls: list[str] = []
    monkeypatch.setattr(mod, "call_claude_cli", _fake_call_claude_cli(model_calls))
    cap = tmp_path / "captures"
    cap.mkdir()
    monkeypatch.setattr(mod, "CAPTURES_DIR", cap)
    saved = cap / "etherfi-affiliates-page-2026-10-05.html"
    saved.write_text(RAW[URL_AFFILIATES], encoding="utf-8")

    assert mod.main(["--from-file", _AFF, str(saved)]) == 0

    rows = [json.loads(l) for l in mod.EVENTS_PATH.read_text(encoding="utf-8").splitlines()]
    assert [r["kind"] for r in rows] == ["changed"]
    assert len(model_calls) == 1
    import yaml
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    aff = next(s for s in cfg["sources"] if s["id"] == _AFF)
    assert aff["last_ok"] == mod.date.today().isoformat()


def test_from_file_refuses_a_path_outside_captures(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, affiliates_hash=_WRONG_HASH)
    monkeypatch.setattr(mod, "fetch", _no_fetch)
    monkeypatch.setattr(mod, "pseo_enabled", lambda: True)
    cap = tmp_path / "captures"
    cap.mkdir()
    monkeypatch.setattr(mod, "CAPTURES_DIR", cap)
    outside = tmp_path / "elsewhere.html"
    outside.write_text(RAW[URL_AFFILIATES], encoding="utf-8")

    assert mod.main(["--from-file", _AFF, str(outside)]) == 1
    assert not mod.EVENTS_PATH.exists()


def _seed_standing(summary=_429):
    mod.EVENTS_PATH.write_text(json.dumps({
        "event_id": "seed", "ts": "2026-10-04T05:30:05Z", "source_id": _AFF,
        "kind": "fetch_failed", "diff_summary": summary}) + "\n", encoding="utf-8")


def _affiliates_raising(exc):
    def fetch(url, timeout=30):
        if url == URL_AFFILIATES:
            raise exc
        return RAW[url]
    return fetch


def test_standing_same_cause_is_in_the_ledger_but_not_in_telegram(tmp_path, monkeypatch, _no_real_telegram):
    import urllib.error
    _setup(tmp_path, monkeypatch, affiliates_hash=_WRONG_HASH)
    _seed_standing()
    monkeypatch.setattr(mod, "fetch", _affiliates_raising(
        urllib.error.HTTPError(URL_AFFILIATES, 429, "Too Many Requests", {}, None)))

    mod.run(dry_run=False)

    rows = [json.loads(l) for l in mod.EVENTS_PATH.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2 and rows[1]["kind"] == "fetch_failed"
    assert not any(_AFF in t for t in _no_real_telegram)


def test_standing_but_new_cause_is_sent(tmp_path, monkeypatch, _no_real_telegram):
    _setup(tmp_path, monkeypatch, affiliates_hash=_WRONG_HASH)
    _seed_standing()
    monkeypatch.setattr(mod, "fetch", _affiliates_raising(TimeoutError("timed out")))

    mod.run(dry_run=False)

    assert any(f"[intel] fetch_failed {_AFF}: TimeoutError" in t for t in _no_real_telegram)


def test_recovery_after_a_standing_failure_is_announced(tmp_path, monkeypatch, _no_real_telegram):
    real = mod.sha256(mod.canon_text("html", RAW[URL_AFFILIATES]))
    _setup(tmp_path, monkeypatch, affiliates_hash=real)
    _seed_standing()

    mod.run(dry_run=False)

    assert any(l.startswith(f"[intel] recovered {_AFF}: readable again after ")
               for t in _no_real_telegram for l in t.splitlines())
