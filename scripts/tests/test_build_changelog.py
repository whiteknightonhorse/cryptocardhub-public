"""E-16 — the ether.fi Cash changelog page, or nothing at all.

`scripts/intel-route.py` already writes every field this page needs to
`logs/intel/changelog.jsonl` the moment an accepted event is routed (date,
fact, old -> new, quote, source URL, verified date, pages touched). This
script only sorts and renders that ledger into the ONE page at
`/learn/ether-fi-cash-changelog/`.

THE LAW THAT MAKES IT NOT IMITATION: an empty ledger publishes NO page — same
refusal shape as `market-digest.py`'s quiet month (Y55: a zero carries its
cause). Literals here, not imports of the module's constants (Y49): the
minimum item count, the bucket and the slug are typed out again.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "build_changelog", ROOT / "scripts" / "build-changelog.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_changelog"] = mod
    spec.loader.exec_module(mod)
    return mod


bc = _load()

ROWS = [
    {"date": "2026-09-01", "event_id": "e1", "fact_id": "etherfi.fee.card_issuance.t1",
     "fact_class": "fee", "old": "$40 refundable", "new": "$50 non-refundable",
     "quote": "The Core card carries a one-time $50 issuance fee.",
     "source_url": "https://www.ether.fi/cash/fees", "verified_at": "2026-09-01",
     "pages_affected": 3, "pages": ["card-fees", "ether-fi-cash-review", "card-costs"]},
    {"date": "2026-09-10", "event_id": "e2", "fact_id": "etherfi.affiliate.payout.t1",
     "fact_class": "affiliate", "old": "SCR", "new": "USDC",
     "quote": "Payouts are made in USDC on the 15th.",
     "source_url": "https://www.ether.fi/affiliates", "verified_at": "2026-09-10",
     "pages_affected": 7, "pages": ["affiliate-payouts"]},
]


def test_an_empty_ledger_publishes_nothing(tmp_path, monkeypatch, capsys):
    """The whole point. No accepted events, no page — and the refusal says so.

    Goes through `main()` itself, not `refuse()` directly (Y53: verify the
    wiring, not the helper) — a mutation that disables the ledger-empty check
    inside `main()` must fail this test even though `refuse()` itself still
    works correctly in isolation."""
    ledger = tmp_path / "changelog.jsonl"          # never created: empty ledger
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    monkeypatch.setattr(bc, "CHANGELOG_PATH", ledger)
    monkeypatch.setattr(bc, "CONTENT", content_dir)
    monkeypatch.setattr(bc, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(sys, "argv", ["build-changelog.py", "--write"])
    rc = bc.main()
    assert rc == 0
    out = capsys.readouterr().out
    record = json.loads(out.strip().splitlines()[-1])
    assert record["event"] == "changelog_refused"
    assert record["items"] == 0
    assert record["reason"]
    assert not list(content_dir.glob("*.md"))


def test_one_ledger_entry_is_enough_to_publish():
    assert bc.MIN_ITEMS == 1


def test_two_accepted_entries_render_as_two_entries():
    page = bc.build(ROWS)
    body = page["body_markdown"]
    assert body.count("- **Verified:**") == 2
    for r in ROWS:
        assert r["date"] in body
        assert r["old"] in body
        assert r["new"] in body
        assert r["quote"] in body
        assert r["source_url"] in body
        assert r["verified_at"] in body


def test_the_page_has_one_fixed_url(monkeypatch):
    page = bc.build(ROWS)
    assert page["slug"] == "ether-fi-cash-changelog"
    assert page["bucket"] == "learn"


def test_generated_at_is_the_latest_events_date_not_now():
    """AEO-5's rule restated: `dateModified` must follow the ledger's own
    dates, never a rebuild's wall-clock time, or every deploy would claim a
    change that did not happen."""
    page = bc.build(ROWS)
    assert page["generated_at"] == "2026-09-10"


def test_a_second_run_over_the_same_ledger_is_byte_identical(tmp_path, monkeypatch):
    """MUTATION target: if `generated_at` (or anything else) were stamped
    from `datetime.now()`, two runs a second apart would differ and the site
    would announce a change on every deploy that touched nothing."""
    monkeypatch.setattr(bc, "CONTENT", tmp_path)
    p1 = bc.write_page(bc.build([dict(r) for r in ROWS]))
    text1 = p1.read_text(encoding="utf-8")
    p2 = bc.write_page(bc.build([dict(r) for r in ROWS]))
    text2 = p2.read_text(encoding="utf-8")
    assert text1 == text2


def test_the_written_page_is_valid_frontmatter(tmp_path, monkeypatch):
    """Goes through the same collection schema as every other page."""
    monkeypatch.setattr(bc, "CONTENT", tmp_path)
    path = bc.write_page(bc.build([dict(r) for r in ROWS]))
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    import re
    for line in text.split("\n"):
        m = re.match(r"^(\w+): (.*)$", line)
        if m and m.group(2)[:1] in '[{"':
            json.loads(m.group(2))          # raises if malformed
    for required in ("title", "description", "h1", "slug", "bucket",
                     "primary_keyword", "intro_paragraph", "schema_article"):
        assert f"{required}:" in text, required


def test_reassigning_the_module_ledger_path_is_honoured_by_main(tmp_path, monkeypatch, capsys):
    """Y54 in build-changelog's own body, caught by hand before this test
    existed: `read_rows`'s path used to be a default argument value, frozen at
    import time — reassigning `bc.CHANGELOG_PATH` (the way a rehearsal or a
    test isolates itself from the live ledger) was silently ignored and
    `main()` kept reading the real `logs/intel/changelog.jsonl`."""
    ledger = tmp_path / "changelog.jsonl"
    with ledger.open("w", encoding="utf-8") as fh:
        for r in ROWS:
            fh.write(json.dumps(r) + "\n")
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    monkeypatch.setattr(bc, "CHANGELOG_PATH", ledger)
    monkeypatch.setattr(bc, "CONTENT", content_dir)
    monkeypatch.setattr(sys, "argv", ["build-changelog.py", "--write"])
    rc = bc.main()
    assert rc == 0
    files = list(content_dir.glob("*.md"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8").count("- **Verified:**") == 2


def test_a_disabled_pseo_flag_skips_before_anything_is_read(monkeypatch, capsys):
    monkeypatch.setenv("PSEO_ENABLED", "false")
    monkeypatch.setattr(sys, "argv", ["build-changelog.py"])
    rc = bc.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "pseo_disabled" in out
