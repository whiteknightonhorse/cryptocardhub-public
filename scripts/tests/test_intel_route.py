"""E-07c — `--accept` -> facts.yaml -> render -> intel-route.py (D4).

Every number below is a LITERAL (Y49/Y54: an expectation computed by calling
the code under test moves with the mutation and stays green). `SEVEN` is not
`len(SCR_PAGES)` derived at import time and then trusted — the fixture asserts
the corpus copy really carries the literal on exactly SEVEN pages before any
law is judged, and the laws themselves are stated against the literal 7.

The corpus copy is made from REAL published pages (frontmatter, snapshot
bullets, `generated_at`, the three spellings of `Last verified`), not invented
markdown: the applier's behaviour depends on those shapes, and a hand-written
fixture would prove it works on a document the site does not contain.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


route_mod = _load("intel_route_test", "intel-route.py")
ingest = _load("intel_ingest_test", "intel-ingest.py")
pg = _load("pseo_generate_test", "pseo-generate.py")

sys.path.insert(0, str(ROOT / "scripts"))
import corpus_edit  # noqa: E402
import facts_lib as fl  # noqa: E402

CONTENT = ROOT / "seo-site" / "src" / "content" / "pseo"

# --- LAW: the acceptance's own numbers, as literals -------------------------
SEVEN = 7
EXPECT_TOO_HIGH = 70

FACT_ID = "etherfi.affiliate.payout_currency"
OLD_LITERAL = "SCR"
NEW_LITERAL = "USDC"

# Seven real pages get the literal; two of them (`ether-fi-cash-review`,
# `is-ether-fi-cash-safe`) are in pseo-generate.FROZEN_SLUGS — a frozen page
# must still be CORRECTED by the table (Y69), it may only never be regenerated.
SCR_PAGES = [
    "ether-fi-cash-review",
    "is-ether-fi-cash-safe",
    "how-to-refer-friend-ether-fi",
    "best-crypto-card-2026",
    "are-crypto-cards-safe",
    "anonymous-crypto-card",
    "best-crypto-card-canada",
]
FROZEN_PAGE = "ether-fi-cash-review"
CLEAN_PAGES = [
    "best-crypto-card-brazil",
    "best-crypto-card-australia",
    "are-crypto-cards-legal",
    "best-crypto-card-argentina",
    "are-crypto-cards-regulated",
]
INJECTED = f"\n\nReferral commissions are paid in {OLD_LITERAL}.\n"


@pytest.fixture
def corpus(tmp_path):
    """A tmp copy of 12 real pages, 7 of them carrying the old literal."""
    out = tmp_path / "pseo"
    out.mkdir()
    for slug in SCR_PAGES + CLEAN_PAGES:
        text = (CONTENT / f"{slug}.md").read_text(encoding="utf-8")
        assert OLD_LITERAL not in text, f"{slug} already carries {OLD_LITERAL!r}"
        if slug in SCR_PAGES:
            text += INJECTED
        (out / f"{slug}.md").write_text(text, encoding="utf-8")
    carriers = [p for p in out.glob("*.md")
                if OLD_LITERAL in p.read_text(encoding="utf-8")]
    assert len(carriers) == SEVEN
    return out


@pytest.fixture(autouse=True)
def ledgers(tmp_path, monkeypatch):
    """Redirect both ledgers into tmp — a test must never append to the real
    append-only changelog (its rows become a published page, E-16) or to the
    candidates ledger (its rows are what the weekly caps are counted from, so
    a test run would spend the real week's allowance).

    `intel-ingest.py` loads `intel-route.py` as its OWN module object, so
    patching `route_mod` alone leaves `ingest._route` pointing at a second
    instance holding the real paths — the two accept tests appended to the
    live ledgers for exactly that reason."""
    monkeypatch.setattr(route_mod, "CONTENT", tmp_path / "pseo")
    monkeypatch.setattr(route_mod, "CHANGELOG_PATH", tmp_path / "changelog.jsonl")
    monkeypatch.setattr(route_mod, "CANDIDATES_PATH", tmp_path / "candidates.jsonl")
    monkeypatch.setattr(route_mod, "AEO_DIR", tmp_path / "aeo-empty")
    # E-18: the default event's score (12.0, class affiliate) clears every D4
    # threshold, so every full route() test in this file fires update/faq/video
    # candidates. Without redirecting these too, delivery would write into the
    # REAL page-updates.jsonl / faq-cohorts.json / h1_queue.json on every run.
    monkeypatch.setattr(route_mod, "PAGE_UPDATES_PATH", tmp_path / "page-updates.jsonl")
    monkeypatch.setattr(route_mod, "FAQ_COHORTS_PATH", tmp_path / "faq-cohorts.json")
    monkeypatch.setattr(route_mod, "H1_QUEUE_PATH", tmp_path / "h1_queue.json")
    monkeypatch.setattr(ingest, "_route", route_mod)
    return tmp_path


def test_no_module_in_this_suite_can_reach_the_live_ledgers(ledgers):
    """The fixture above is the law; this states it so a future edit that
    drops one of the patches goes red here instead of in production."""
    live = ROOT / "logs" / "intel"
    live_pseo = ROOT / "logs" / "pseo"
    live_h1 = ROOT / "video-generator" / "state"
    for mod in (route_mod, ingest._route):
        assert mod.CHANGELOG_PATH.parent != live
        assert mod.CANDIDATES_PATH.parent != live
        assert mod.PAGE_UPDATES_PATH.parent != live_pseo
        assert mod.FAQ_COHORTS_PATH.parent != live_pseo
        assert mod.H1_QUEUE_PATH.parent != live_h1
    assert ingest._route is route_mod


def _facts(status: str = "current", value: str = OLD_LITERAL, cls: str = "affiliate"):
    return [{
        "id": FACT_ID, "value": value, "unit": None, "applies_to": {},
        "source_url": "https://www.ether.fi/affiliates",
        "source_quote": "Payouts are made in SCR.",
        "verified_at": date(2026, 9, 1), "expires": None,
        "status": status, "class": cls, "confidence": "issuer_legal",
        "history": [], "promo_restricted": [],
    }]


# The accept path is LINE SURGERY on the yaml (the real file is hand-commented
# and a safe_dump round-trip would reflow all of it), so the fixture has to be
# written in the real file's own indentation — `yaml.safe_dump` emits `- id:`
# at column 0 and would silently prove the surgery works on a shape the repo
# does not contain. `test_the_real_facts_file_has_the_shape_this_fixture_copies`
# below keeps the two from drifting apart.
#
# The SECOND record is the shape that broke the first version of this code: an
# int `value:` and a `history:` written as a BLOCK sequence. 2 of the 18 real
# records look like that, and both of them were unacceptable — the archive
# rows hold YAML dates (`json.dumps` refuses them outright) and replacing the
# `    history:` line alone leaves the block under it orphaned.
BLOCK_FACT_ID = "etherfi.affiliate.physical_card_commission.t1"

FACTS_YAML_FIXTURE = f"""# test fixture — same shape as config/facts/etherfi.yaml
facts:
  - id: {FACT_ID}
    value: "{OLD_LITERAL}"
    unit: null
    applies_to: {{}}
    source_url: "https://www.ether.fi/affiliates"
    source_quote: "Payouts are made in SCR."
    verified_at: 2026-09-01
    expires: null
    status: current
    class: affiliate
    confidence: issuer_legal
    history: []
    promo_restricted: []

  - id: {BLOCK_FACT_ID}
    value: 20
    unit: "%"
    applies_to: {{tier: 1}}
    source_url: "https://www.ether.fi/affiliates"
    source_quote: "Physical Card Commission — Tier 1 20%"
    verified_at: 2026-09-14
    expires: null
    status: current
    class: affiliate
    confidence: issuer_legal
    history:
      - value: "10%-25% (tiered, unresolved)"
        from: 2026-04-01
        to: 2026-09-14
        source_url: "https://www.ether.fi/affiliates"
        event_id: "E-09-2026-09-14"
    promo_restricted: []
"""


def _event(**kw):
    ev = {
        "event_id": "e07ctest01", "ts": "2026-09-14T05:30:00Z",
        "source_id": "etherfi-affiliates", "kind": "changed",
        "url": "https://www.ether.fi/affiliates", "updated_text": None,
        "diff_summary": "-paid in SCR +paid in USDC",
        "extracted": [{"fact_id": FACT_ID, "old": OLD_LITERAL, "new": NEW_LITERAL,
                       "quote": "Payouts are made in USDC on the 15th."}],
        "conflicts": [], "score": 12.0, "decision": "accepted",
        "decided_at": "2026-09-14T09:00:00Z", "outputs": [],
    }
    ev.update(kw)
    return ev


def _snapshot(corpus_dir: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(corpus_dir.glob("*.md"))}


# =============================================================================
# ACCEPTANCE, verbatim from the task
# =============================================================================

def test_scr_to_usdc_changes_exactly_seven_files_and_expect_7_passes(corpus):
    res = route_mod.route(_event(), _facts(), content_dir=corpus,
                          expect=SEVEN, write=True)
    assert res["refused"] is None
    assert res["pages_affected"] == SEVEN
    assert len(res["files"]) == SEVEN
    assert sorted(res["files"]) == sorted(SCR_PAGES)
    for slug in SCR_PAGES:
        text = (corpus / f"{slug}.md").read_text(encoding="utf-8")
        assert OLD_LITERAL not in text
        assert f"paid in {NEW_LITERAL}." in text
    # The five pages that never carried the old literal are byte-identical.
    # (Not "they contain no USDC" — `best-crypto-card-brazil` legitimately
    # mentions USDC in its own prose; the law is that the run did not touch
    # them at all.)
    for slug in CLEAN_PAGES:
        assert (corpus / f"{slug}.md").read_text(encoding="utf-8") == \
            (CONTENT / f"{slug}.md").read_text(encoding="utf-8")


def test_expect_70_is_refused_and_not_one_byte_is_written(corpus, ledgers):
    before = _snapshot(corpus)
    res = route_mod.route(_event(), _facts(), content_dir=corpus,
                          expect=EXPECT_TOO_HIGH, write=True)
    assert res["refused"], "a table that changes 7 pages must not pass --expect 70"
    assert str(EXPECT_TOO_HIGH) in res["refused"]
    assert _snapshot(corpus) == before
    assert not route_mod.CHANGELOG_PATH.exists()
    assert not route_mod.CANDIDATES_PATH.exists()


def test_pages_affected_zero_touches_no_file_but_still_writes_the_changelog(corpus):
    ev = _event(extracted=[{"fact_id": FACT_ID, "old": "a literal no page carries",
                            "new": "whatever", "quote": "q"}])
    before = _snapshot(corpus)
    res = route_mod.route(ev, _facts(), content_dir=corpus, write=True)
    assert res["refused"] is None
    assert res["pages_affected"] == 0
    assert res["files"] == []
    assert _snapshot(corpus) == before
    rows = [json.loads(line) for line in
            route_mod.CHANGELOG_PATH.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["pages_affected"] == 0


def test_a_disputed_fact_is_refused_with_no_edits_and_a_CONFLICT_line(corpus):
    before = _snapshot(corpus)
    res = route_mod.route(_event(), _facts(status="disputed"),
                          content_dir=corpus, expect=SEVEN, write=True)
    assert res["refused"].startswith("disputed:")
    assert res["conflicts"] and res["conflicts"][0].startswith("CONFLICT:")
    assert FACT_ID in res["conflicts"][0]
    assert _snapshot(corpus) == before
    assert not route_mod.CHANGELOG_PATH.exists()


# =============================================================================
# MUTATIONS, verbatim from the task
# =============================================================================

def test_the_ceiling_is_computed_at_ten_percent_of_expect_not_a_loose_default():
    """MUTATION "потолок снят": the refusal in
    `test_expect_70_is_refused_and_not_one_byte_is_written` has to come from
    the 10 % law, not from corpus_edit's own looser `max(2, 10 %)` default.
    At --expect 7 the ceiling is 0: seven pages, exactly."""
    assert int(SEVEN * route_mod.CEILING_FRACTION) == 0
    assert int(EXPECT_TOO_HIGH * route_mod.CEILING_FRACTION) == 7


def test_eight_changed_pages_do_not_pass_expect_seven(corpus):
    """The other side of the same ceiling — a run that overshoots by ONE is
    refused too. `--expect 70` could be caught by any sloppy bound; this one
    can only be caught by the 10 %-of-7-is-0 law."""
    extra = corpus / f"{CLEAN_PAGES[0]}.md"
    extra.write_text(extra.read_text(encoding="utf-8") + INJECTED, encoding="utf-8")
    before = _snapshot(corpus)
    res = route_mod.route(_event(), _facts(), content_dir=corpus,
                          expect=SEVEN, write=True)
    assert res["refused"]
    assert _snapshot(corpus) == before


def test_a_frozen_page_is_corrected_by_the_table_and_never_regenerated(corpus):
    """MUTATION "FROZEN регенерируется": a frozen page must be in the changed
    set (the correction reaches the measured converters too), and the ONLY
    lines that differ in it are the replaced literal, the two dates, and
    E-18's own `what_to_watch` changelog mention (also a table-style append
    to one existing line, never a rewrite of the document). A regeneration
    would rewrite the whole document."""
    with pytest.raises(pg.FrozenPageError):
        pg.assert_not_frozen(FROZEN_PAGE)

    before = (corpus / f"{FROZEN_PAGE}.md").read_text(encoding="utf-8")
    res = route_mod.route(_event(), _facts(), content_dir=corpus,
                          expect=SEVEN, write=True)
    assert FROZEN_PAGE in res["files"]
    after = (corpus / f"{FROZEN_PAGE}.md").read_text(encoding="utf-8")
    assert after != before

    changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
    assert changed, "the frozen page was reported as changed but is byte-identical"
    for old_line, new_line in changed:
        assert (NEW_LITERAL in new_line
                or new_line.startswith("generated_at:")
                or new_line.startswith("what_to_watch:")
                or "Last verified" in new_line), f"unexpected rewrite: {new_line[:80]!r}"
    assert len(before.splitlines()) == len(after.splitlines())


def test_the_changelog_carries_every_field_the_changelog_page_needs(corpus):
    """MUTATION "changelog не пишется": E-16 renders date · fact · was -> now ·
    quote · source link · verified. All six are asserted by name and value."""
    route_mod.route(_event(), _facts(), content_dir=corpus, expect=SEVEN, write=True)
    rows = [json.loads(line) for line in
            route_mod.CHANGELOG_PATH.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["fact_id"] == FACT_ID
    assert row["old"] == OLD_LITERAL
    assert row["new"] == NEW_LITERAL
    assert row["quote"] == "Payouts are made in USDC on the 15th."
    assert row["source_url"] == "https://www.ether.fi/affiliates"
    assert row["date"] == row["verified_at"] == date.today().isoformat()
    assert row["pages_affected"] == SEVEN
    assert sorted(row["pages"]) == sorted(SCR_PAGES)


def test_generated_at_is_bumped_on_changed_pages_and_on_no_others(corpus):
    before = _snapshot(corpus)
    res = route_mod.route(_event(), _facts(), content_dir=corpus,
                          expect=SEVEN, write=True)
    assert res["generated_bumped"] == SEVEN
    for slug in CLEAN_PAGES:
        assert (corpus / f"{slug}.md").read_text(encoding="utf-8") == before[f"{slug}.md"]


def test_a_dry_run_measures_the_same_seven_and_writes_nothing(corpus, ledgers):
    before = _snapshot(corpus)
    res = route_mod.route(_event(), _facts(), content_dir=corpus, write=False)
    assert res["refused"] is None
    assert res["pages_affected"] == SEVEN
    assert _snapshot(corpus) == before
    assert not route_mod.CHANGELOG_PATH.exists()


def test_an_undecided_event_routes_to_nothing(corpus):
    before = _snapshot(corpus)
    res = route_mod.route(_event(decision="pending"), _facts(),
                          content_dir=corpus, expect=SEVEN, write=True)
    assert res["refused"].startswith("not_accepted")
    assert _snapshot(corpus) == before


# =============================================================================
# D4 candidates: thresholds are literals, caps are enforced
# =============================================================================

def test_d4_thresholds_are_the_plans_own_numbers():
    assert route_mod.candidate_kinds(7.9, "affiliate") == []
    assert route_mod.candidate_kinds(8.0, "affiliate") == ["update"]
    assert route_mod.candidate_kinds(12.0, "affiliate") == ["update", "faq", "video"]
    # score >= 12 but a class outside {availability, rate, affiliate}
    assert route_mod.candidate_kinds(12.0, "feature") == ["update", "faq"]


def test_the_fourth_video_topic_of_one_week_is_capped(corpus):
    now = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
    fired = []
    for i in range(4):
        res = route_mod._emit_candidates(
            _event(event_id=f"cap{i}"), "affiliate", ["/reviews/x/"], now, write=True)
        fired += [c for c in res if c["kind"] == "video" and not c["capped"]]
    assert len(fired) == 3, "D4 caps H1 video topics at 3/week"
    rows = [json.loads(line) for line in
            route_mod.CANDIDATES_PATH.read_text(encoding="utf-8").splitlines()]
    capped = [r for r in rows if r["kind"] == "video" and r["capped"]]
    assert len(capped) == 1 and "cap 3/week" in capped[0]["reason"]


# =============================================================================
# E-18: "Changed on <date> ... see changelog" on affected pages
# =============================================================================

def test_append_what_to_watch_round_trips_the_json_array():
    text = '---\nwhat_to_watch: ["a", "b"]\n---\nbody\n'
    new_text, changed = corpus_edit.append_what_to_watch(text, "c")
    assert changed
    m = re.search(r'^what_to_watch:\s*(\[.*\])\s*$', new_text, re.M)
    assert json.loads(m.group(1)) == ["a", "b", "c"]
    assert new_text.count("\n") == text.count("\n"), "a JSON-array field grows on ONE line"


def test_append_what_to_watch_is_a_no_op_when_the_field_is_missing():
    text = '---\ntitle: "x"\n---\nbody\n'
    new_text, changed = corpus_edit.append_what_to_watch(text, "c")
    assert not changed and new_text == text


def test_append_what_to_watch_is_idempotent_for_the_exact_same_line():
    """MUTATION "the `line in items` guard is dropped": re-routing the same
    accepted event twice (or a retried `--write`) would otherwise append the
    same sentence a second time onto a page a human reads."""
    text = '---\nwhat_to_watch: ["a"]\n---\nbody\n'
    once, changed = corpus_edit.append_what_to_watch(text, "a")
    assert not changed and once == text


def test_watch_line_is_inserted_into_every_affected_pages_what_to_watch(corpus):
    res = route_mod.route(_event(), _facts(), content_dir=corpus, expect=SEVEN, write=True)
    assert res["watch_lines"] == SEVEN
    for slug in SCR_PAGES:
        text = (corpus / f"{slug}.md").read_text(encoding="utf-8")
        m = re.search(r'^what_to_watch:\s*(\[.*\])\s*$', text, re.M)
        assert m, f"{slug} lost its what_to_watch field"
        items = json.loads(m.group(1))
        line = next((i for i in items if i.startswith("Changed on ")), None)
        assert line, f"{slug}: no changelog mention was added"
        assert OLD_LITERAL not in line, \
            "a corrected page must not have the stale literal walk back onto it (Y64)"
        assert "Affiliate program" in line
        assert line.endswith("see changelog"), "no changelog page exists yet -- plain text, not a link (G20)"
    for slug in CLEAN_PAGES:
        assert (corpus / f"{slug}.md").read_text(encoding="utf-8") == \
            (CONTENT / f"{slug}.md").read_text(encoding="utf-8")


def test_the_watch_line_links_once_the_changelog_page_actually_exists(corpus):
    """Mirrors `market-digest.py`'s own `_changelog_link()` guard: a
    `[text](url)` only once E-16's page is really on disk (G20 — a link to a
    URL nobody built is a dead internal link)."""
    (corpus / "ether-fi-cash-changelog.md").write_text("stub", encoding="utf-8")
    route_mod.route(_event(), _facts(), content_dir=corpus, expect=SEVEN, write=True)
    text = (corpus / f"{SCR_PAGES[0]}.md").read_text(encoding="utf-8")
    items = json.loads(re.search(r'^what_to_watch:\s*(\[.*\])\s*$', text, re.M).group(1))
    line = next(i for i in items if i.startswith("Changed on "))
    assert "[changelog](/learn/ether-fi-cash-changelog/)" in line


def test_pages_affected_zero_adds_no_watch_line(corpus):
    """A fact quoted nowhere in the corpus has no page to announce itself on."""
    ev = _event(extracted=[{"fact_id": FACT_ID, "old": "a literal no page carries",
                            "new": "whatever", "quote": "q"}])
    res = route_mod.route(ev, _facts(), content_dir=corpus, write=True)
    assert res["watch_lines"] == 0


# =============================================================================
# E-18: delivering D4 candidates into the ledgers their consumers actually read
# =============================================================================

def _seed_open_faq_cohort(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cohorts": [{
        "opened": "2026-09-01", "verdict_due_14": "2026-09-15",
        "verdict_due_28": "2026-09-29", "items": [],
    }]}), encoding="utf-8")


def test_score_13_affiliate_produces_one_of_each_candidate_and_delivers_all(corpus):
    """Acceptance, verbatim: "событие score 13 класса affiliate ⇒ 1
    update-кандидат, 1 FAQ-вопрос, 1 видео-тема"."""
    _seed_open_faq_cohort(route_mod.FAQ_COHORTS_PATH)
    ev = _event(score=13.0)
    res = route_mod.route(ev, _facts(), content_dir=corpus, expect=SEVEN, write=True)
    assert res["refused"] is None
    kinds = {c["kind"] for c in res["candidates"]}
    assert kinds == {"update", "faq", "video"}
    assert all(c["delivered"] for c in res["candidates"]), res["candidates"]

    update_rows = [json.loads(l) for l in
                   route_mod.PAGE_UPDATES_PATH.read_text(encoding="utf-8").splitlines()]
    assert len(update_rows) == 1
    assert update_rows[0]["event"] == "queued" and update_rows[0]["event_id"] == ev["event_id"]

    faq_led = json.loads(route_mod.FAQ_COHORTS_PATH.read_text(encoding="utf-8"))
    items = faq_led["cohorts"][-1]["items"]
    assert len(items) == 1
    assert items[0]["source"] == "intel" and items[0]["question"].endswith("?")
    assert items[0]["slug"] not in {p.stem for p in corpus.glob("*.md")}

    h1 = json.loads(route_mod.H1_QUEUE_PATH.read_text(encoding="utf-8"))
    assert len(h1) == 1 and h1[0]["event_id"] == ev["event_id"]


def test_a_faq_candidate_with_no_open_cohort_is_recorded_undelivered(corpus):
    """No cohort exists yet -- E-18 must never OPEN one (that decision belongs
    to `open_next_cohort`'s own tap), so the candidate is honestly marked
    undelivered rather than silently dropped (Y55: a zero carries its cause)."""
    ev = _event(score=13.0)
    res = route_mod.route(ev, _facts(), content_dir=corpus, expect=SEVEN, write=True)
    faq = next(c for c in res["candidates"] if c["kind"] == "faq")
    assert faq["delivered"] is False
    assert faq["delivery_reason"] == "no_open_cohort"
    assert not route_mod.FAQ_COHORTS_PATH.exists()


def test_score_5_delivers_only_the_base_tier_no_candidates(corpus):
    """Acceptance, verbatim: "score 5 ⇒ только changelog" -- D4's row 1 (table
    edit + changelog + the what_to_watch mention) fires for ANY accepted
    event with pages_affected >= 1, independent of score; candidates need
    score >= 8, which 5 never clears."""
    ev = _event(score=5.0)
    res = route_mod.route(ev, _facts(), content_dir=corpus, expect=SEVEN, write=True)
    assert res["refused"] is None
    assert res["candidates"] == []
    assert res["changelog"] == 1
    assert res["watch_lines"] == SEVEN
    assert not route_mod.PAGE_UPDATES_PATH.exists()
    assert not route_mod.FAQ_COHORTS_PATH.exists()
    assert not route_mod.H1_QUEUE_PATH.exists()


def test_the_shared_update_cap_is_never_exceeded_across_three_events():
    """D4, and the acceptance's own words: "5/нед общий с Bing" / "общий кап
    5/нед усиления не превышается при 3 событиях". Three Bing applies already
    used 3 of the week's 5 slots (the SAME ledger `page-updates.py` counts
    from); three more intel events asking for an update candidate must not
    push the week's combined total past 5."""
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    week = route_mod._period_key(now, "week")
    route_mod.PAGE_UPDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    route_mod.PAGE_UPDATES_PATH.write_text("\n".join(
        json.dumps({"event": "applied", "week": week, "query": f"bing{i}"})
        for i in range(3)) + "\n", encoding="utf-8")

    delivered = 0
    for i in range(3):
        row = {"kind": "update", "capped": False, "target_path": "/reviews/x/",
              "fact_class": "affiliate", "score": 8.0}
        route_mod.deliver_candidates([row], _event(event_id=f"e{i}"), _facts(), now,
                                     write=True, content_dir=route_mod.CONTENT)
        delivered += int(row["delivered"])

    rows = [json.loads(l) for l in
            route_mod.PAGE_UPDATES_PATH.read_text(encoding="utf-8").splitlines()]
    assert delivered == 2, "only 2 of the week's 5 slots were still free"
    assert len(rows) == 5 == route_mod.UPDATE_SHARED_CAP


def test_a_rehearsal_never_delivers_candidates_to_the_live_ledgers(corpus, tmp_path):
    """MUTATION "delivery_paths ignores content_dir": the same rehearsal law
    `ledger_paths` already enforces for the changelog/candidates ledgers must
    hold for the three NEW delivery targets too."""
    rehearsal = tmp_path / "copy"
    shutil.copytree(corpus, rehearsal)
    ev = _event(score=13.0)
    res = route_mod.route(ev, _facts(), content_dir=rehearsal, expect=SEVEN, write=True)
    assert res["refused"] is None
    assert all(c["delivered"] or c["kind"] == "faq" for c in res["candidates"])

    own = tmp_path / "copy-intel-ledgers"
    assert (own / "page-updates.jsonl").is_file()
    assert (own / "h1_queue.json").is_file()
    assert not route_mod.PAGE_UPDATES_PATH.exists()
    assert not route_mod.FAQ_COHORTS_PATH.exists()
    assert not route_mod.H1_QUEUE_PATH.exists()


# =============================================================================
# --accept / --reject on intel-ingest.py
# =============================================================================

@pytest.fixture
def accept_env(tmp_path, monkeypatch, corpus):
    """Real facts.yaml and events.jsonl shapes, in tmp. `_render_facts` is
    stubbed only so the test does not write the live SKILL.md — the CALL is
    still asserted (E-07c: accept must re-render the writer's own block)."""
    facts_path = tmp_path / "etherfi.yaml"
    facts_path.write_text(FACTS_YAML_FIXTURE, encoding="utf-8")
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        json.dumps(_event(decision="pending", decided_at=None)) + "\n", encoding="utf-8")
    monkeypatch.setattr(ingest, "FACTS_PATH", facts_path)
    monkeypatch.setattr(ingest, "EVENTS_PATH", events_path)
    rendered = []
    monkeypatch.setattr(ingest, "_render_facts", lambda: rendered.append(True))
    return {"facts": facts_path, "events": events_path, "rendered": rendered,
            "corpus": corpus}


def test_the_real_facts_file_has_the_shape_this_fixture_copies():
    """The accept path finds a fact by `  - id: <x>` and rewrites `    value:`
    in place. If the real file is ever re-indented, the fixture above would
    keep passing against a shape the repo no longer has."""
    real = fl.DEFAULT_FACTS_PATH.read_text(encoding="utf-8")
    assert "\n  - id: etherfi." in real
    assert "\n    value:" in real
    assert "\n    verified_at:" in real
    assert "\n    history: []" in real
    # ...and the OTHER shape, the one the fixture's second record copies.
    assert "\n    history:\n      - value:" in real, \
        "no real fact carries a block history — the fixture claims one does"
    assert "\n  - id: etherfi.affiliate.physical_card_commission.t1\n    value: 20\n" in real


def test_accept_moves_the_value_files_the_old_one_and_stamps_today(accept_env):
    res = ingest.decide("e07ctest01", "accepted", expect=SEVEN,
                        content_dir=accept_env["corpus"])
    assert res["event"] == "accepted"
    assert accept_env["rendered"] == [True], "accept must re-render SKILL.md"

    fact = yaml.safe_load(accept_env["facts"].read_text(encoding="utf-8"))["facts"][0]
    assert fact["value"] == NEW_LITERAL
    assert str(fact["verified_at"]) == date.today().isoformat()
    assert len(fact["history"]) == 1
    assert fact["history"][0]["value"] == OLD_LITERAL
    assert fact["history"][0]["from"] == date(2026, 9, 1)
    assert fact["history"][0]["to"] == date.today()
    assert fact["history"][0]["event_id"] == "e07ctest01"
    fl.load(accept_env["facts"])  # the rewritten file still validates

    rows = [json.loads(line) for line in
            accept_env["events"].read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2 and rows[1]["decision"] == "accepted"
    assert rows[1]["decided_at"]
    assert any(o["kind"] == "changelog" for o in rows[1]["outputs"])


def test_accept_refused_by_the_ceiling_leaves_facts_yaml_untouched(accept_env):
    """The pre-flight law: a corpus refusal must not leave the truth layer
    saying USDC while seven published pages still say SCR."""
    before_facts = accept_env["facts"].read_text(encoding="utf-8")
    before_corpus = _snapshot(accept_env["corpus"])
    res = ingest.decide("e07ctest01", "accepted", expect=EXPECT_TOO_HIGH,
                        content_dir=accept_env["corpus"])
    assert res["event"] == "refused"
    assert accept_env["facts"].read_text(encoding="utf-8") == before_facts
    assert _snapshot(accept_env["corpus"]) == before_corpus
    assert accept_env["rendered"] == []
    rows = accept_env["events"].read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1, "a refused accept must not append a decision"


def test_reject_records_the_decision_and_changes_nothing_else(accept_env):
    before_facts = accept_env["facts"].read_text(encoding="utf-8")
    before_corpus = _snapshot(accept_env["corpus"])
    res = ingest.decide("e07ctest01", "rejected")
    assert res["event"] == "rejected"
    assert accept_env["facts"].read_text(encoding="utf-8") == before_facts
    assert _snapshot(accept_env["corpus"]) == before_corpus
    rows = [json.loads(line) for line in
            accept_env["events"].read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2 and rows[1]["decision"] == "rejected"


def test_an_event_cannot_be_decided_twice(accept_env):
    ingest.decide("e07ctest01", "rejected")
    again = ingest.decide("e07ctest01", "accepted", content_dir=accept_env["corpus"])
    assert again["event"] == "refused"
    assert again["reason"] == "already_rejected"


def test_accept_of_a_disputed_fact_writes_nowhere(accept_env, monkeypatch):
    text = accept_env["facts"].read_text(encoding="utf-8").replace(
        "status: current", "status: disputed")
    accept_env["facts"].write_text(text, encoding="utf-8")
    before_corpus = _snapshot(accept_env["corpus"])
    res = ingest.decide("e07ctest01", "accepted", expect=SEVEN,
                        content_dir=accept_env["corpus"])
    assert res["event"] == "refused"
    assert res["reason"].startswith("disputed:")
    assert accept_env["facts"].read_text(encoding="utf-8") == text
    assert _snapshot(accept_env["corpus"]) == before_corpus


# =============================================================================
# the two shapes the real facts.yaml has that the first fixture did not
# =============================================================================

def test_accept_survives_the_real_facts_file_including_its_block_histories():
    """MUTATION "history rewritten as a one-line flow list": the real file is
    the document this code has to work on, so the rewrite is run against its
    actual bytes and the RESULT is parsed back. Both failures the old code had
    are stated here: `json.dumps` cannot serialize the YAML dates already in
    those archives, and replacing only the `    history:` line leaves the block
    beneath it orphaned so nothing loads at all.

    Read-only: `apply_accept_to_facts` is pure, nothing is written to disk."""
    facts = fl.load(fl.DEFAULT_FACTS_PATH)
    # ruling-3 (CCH-FAQ-G36-0929): the four flipped string rows carry block
    # histories too, as does the float 0.1 row; this law is about int values
    # (`value: 20` must not become "25"), so strings and floats are excluded.
    with_block = [f for f in facts if f["history"] and isinstance(f["value"], int)]
    assert [f["id"] for f in with_block] == [
        "etherfi.affiliate.physical_card_commission.t1",
        "etherfi.fee.physical_card.core",
    ], "the block-history records moved; this law names them as literals"

    ev = _event(event_id="blockprobe", extracted=[
        {"fact_id": f["id"], "old": str(f["value"]), "new": "25", "quote": "q"}
        for f in with_block])
    text = fl.DEFAULT_FACTS_PATH.read_text(encoding="utf-8")
    after = ingest.validate_facts_text(
        ingest.apply_accept_to_facts(text, ev, facts), ev)

    by_id = {f["id"]: f for f in after}
    for f in with_block:
        got = by_id[f["id"]]
        # int stays int: `value: 20` must not become the string "25".
        assert got["value"] == 25 and isinstance(got["value"], int)
        assert got["verified_at"] == date.today()
        assert len(got["history"]) == len(f["history"]) + 1
        # the archival row is untouched, dates still dates
        assert got["history"][0] == f["history"][0]
        assert isinstance(got["history"][0]["to"], date)
        assert got["history"][-1]["value"] == f["value"]
        assert got["history"][-1]["event_id"] == "blockprobe"
    # every other record is byte-identical prose: the surgery touched 2 of 18
    assert len(after) == len(facts)


def test_an_unloadable_rewrite_is_refused_before_facts_yaml_is_written(accept_env,
                                                                      monkeypatch):
    """MUTATION "валидация перед записью снята": the truth layer may not be
    replaced by a file that does not parse. facts.yaml is read by the writer,
    the renderer and the router — discovering the breakage after the write
    kills all three at once."""
    before = accept_env["facts"].read_text(encoding="utf-8")
    monkeypatch.setattr(ingest, "apply_accept_to_facts",
                        lambda *a, **k: "facts:\n  - id: x\n   value: [oops\n")
    res = ingest.decide("e07ctest01", "accepted", expect=SEVEN,
                        content_dir=accept_env["corpus"])
    assert res["event"] == "refused"
    assert res["reason"].startswith("facts_rewrite_invalid")
    assert accept_env["facts"].read_text(encoding="utf-8") == before
    assert accept_env["rendered"] == []
    assert len(accept_env["events"].read_text(encoding="utf-8").splitlines()) == 1


# =============================================================================
# a rehearsal on a corpus COPY keeps its own ledgers
# =============================================================================

def test_a_rehearsal_on_a_copy_never_appends_to_the_live_ledgers(corpus, tmp_path):
    """MUTATION "ledger_paths ignores content_dir": `--content <copy>` is the
    dress rehearsal. The changelog it would append to is the source of a
    published page (E-16) and the candidates ledger is what the weekly caps
    are counted from, so a rehearsal writing there publishes a change nobody
    accepted and spends the real week's allowance."""
    rehearsal = tmp_path / "copy"
    shutil.copytree(corpus, rehearsal)
    res = route_mod.route(_event(), _facts(), content_dir=rehearsal,
                          expect=SEVEN, write=True)
    assert res["refused"] is None and res["pages_affected"] == SEVEN

    own = tmp_path / "copy-intel-ledgers"
    assert res["changelog_path"] == str(own / "changelog.jsonl")
    assert (own / "changelog.jsonl").is_file()
    assert (own / "candidates.jsonl").is_file()
    assert not route_mod.CHANGELOG_PATH.exists()
    assert not route_mod.CANDIDATES_PATH.exists()
    # and the live corpus is the one path that keeps the live ledgers
    assert route_mod.ledger_paths(route_mod.CONTENT) == (
        route_mod.CHANGELOG_PATH, route_mod.CANDIDATES_PATH)


def test_neither_env_nor_crontab_is_ever_written(corpus):
    """Plan rule (sandbox): no step of E-07c may touch `.env` or the crontab."""
    for src in ("intel-route.py", "intel-ingest.py", "corpus_edit.py"):
        text = (ROOT / "scripts" / src).read_text(encoding="utf-8")
        for banned in ("crontab -", '"/.env"', 'ROOT / ".env"', "load_dotenv"):
            assert banned not in text, f"{src} mentions {banned!r}"
