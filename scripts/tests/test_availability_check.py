"""G24 (Y51 AEO-1, machine half) — the reconciler finds disagreements and
changes nothing on its own.

`verified` is a load-bearing date: the page shows it, and past 60 days it turns
into a visible overdue badge. So the only thing that may move it forward is a
check that actually passed. Three cases, and two of them must NOT move it:

    lists match        -> bump
    lists differ       -> alert, no bump      (a human decides the new fact)
    page unreadable    -> alert, no bump      (silence is not agreement)

The third is the one that rots quietly. A checker that treats "I could not
read the issuer's page" as "nothing changed" manufactures freshness, which is
exactly what the dated badge exists to prevent.

E-06 adds a fourth move that is still not the machine deciding: `--accept <id>`
replays EXACTLY the diff a human read in Telegram, by id. Nothing else writes.

LAWS ARE LITERALS HERE (Y49). The counts below are typed out, not computed
from the parser under test, so "read one fewer cell" or "drop the anchor
check" moves one side of the comparison only and goes red.

T-0134 (2026-09-17): article 758780 dropped its old ✅/❌ paired table for
three new shapes (flat list, prose refusal sentence, prose comma list), so
this file's fixture and low-level parser tests moved to the 2026-09-17
capture. `verdict_pairs`/`compare_pairs`/`intercom_pairs` are gone — no code
outside this file's own history ever called them (grepped clean).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "etherfi-758780-2026-09-17.html"
_SPEC = importlib.util.spec_from_file_location(
    "availability_check", ROOT / "scripts" / "availability-check.py")
ac = importlib.util.module_from_spec(_SPEC)
sys.modules["availability_check"] = ac
_SPEC.loader.exec_module(ac)

sys.path.insert(0, str(ROOT / "scripts"))
import availability_lib as al  # noqa: E402

# --- LAW: what article 758780 said when it was captured, 2026-09-17 ---------
# 88 countries (flat list, Nigeria included), 2 explicit refusals in prose
# (Belgium, Ireland), 31 US jurisdictions (30 states + District of Columbia)
# in one prose comma list, 0 US refusals (the page states none today).
N_COUNTRIES = 88
N_COUNTRIES_NO = 2
N_US_STATES = 31

COUNTRY_HEADING = "countries where a physical cash card is currently available"
COUNTRY_NO_HEADING = "eligible cash regions where a physical card is not currently available"
US_HEADING = "united states"
US_ANCHOR = "Physical card availability:"


@pytest.fixture(scope="module")
def html():
    assert FIXTURE.is_file(), f"captured fixture missing: {FIXTURE}"
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cfg():
    return al.load()


# --- 1. the parser reads each section in its DECLARED form -----------------

def test_the_country_section_reads_as_a_flat_list_of_88(html):
    stream = ac.parse_intercom_stream(html)
    cells = ac._stream_section(stream, COUNTRY_HEADING)
    found = ac.parse_flat_list(cells)
    assert len(found) == N_COUNTRIES, len(found)
    assert "Nigeria" in found
    assert "Belgium" not in found, "Belgium is a refusal, not an available country"


def test_the_explicit_no_section_reads_belgium_and_ireland_from_prose(html):
    stream = ac.parse_intercom_stream(html)
    cells = ac._stream_section(stream, COUNTRY_NO_HEADING)
    found = ac.parse_prose_no(cells)
    assert found == ["Belgium", "Ireland"], found


def test_the_us_states_section_reads_31_names_from_one_prose_cell(html):
    stream = ac.parse_intercom_stream(html)
    cells = ac._stream_section(stream, US_HEADING)
    found = ac.parse_prose_list(cells, US_ANCHOR)
    assert len(found) == N_US_STATES, found
    assert "District of Columbia" in found
    assert "Mississippi" not in found
    assert not any("Note" in x or "jurisdiction is not listed" in x for x in found)


def test_a_prose_no_section_whose_wording_changed_is_unparseable(html):
    """The refusal sentence is matched by its exact wording, not by keyword —
    a rewrite must read as 'could not check', never as 'nobody is refused'
    (T-0134 law #4: this list is money, see Y50)."""
    stream = ac.parse_intercom_stream(html)
    cells = ac._stream_section(stream, COUNTRY_NO_HEADING)
    doctored = [c.replace("are eligible regions", "used to be eligible regions") for c in cells]
    assert doctored != cells
    assert ac.parse_prose_no(doctored) is None


# --- 2. the config agrees with the captured page ---------------------------

def test_the_checked_in_lists_match_the_captured_issuer_page(html, cfg):
    res = ac.compare(al.card(cfg), html)
    assert res["status"] == "match", res["summary"]


def test_a_removed_country_is_a_difference_not_a_match(html, cfg):
    card = dict(al.card(cfg))
    card["countries"] = [c for c in card["countries"] if c != "Spain"]
    res = ac.compare(card, html)
    assert res["status"] == "differs"
    assert "Spain" in res["summary"]


def test_a_removed_us_state_diffs_rather_than_vanishing(html, cfg):
    """The US list is one prose sentence now, not a signed table row — a
    state dropped from the issuer's sentence must still surface as a diff."""
    doctored = html.replace("Texas, Utah", "Utah")
    assert doctored != html, "fixture shape changed — the Texas mention moved"
    res = ac.compare(al.card(cfg), doctored)
    assert res["status"] == "differs"
    assert "us_states" in res["summary"] and "Texas" in res["summary"]


def test_the_old_pre_restructure_heading_reads_as_could_not_check_not_as_empty(html, cfg):
    """'physical card available' was the heading before the issuer restructured
    the page (T-0134). It no longer exists as an exact heading — must read as
    unparseable, never as an empty list (which would read as 'every
    restriction was lifted')."""
    card = dict(al.card(cfg))
    card["source"] = dict(card["source"])
    card["source"]["sections"] = {
        "countries": {"heading": "physical card available", "form": "list"}}
    res = ac.compare(card, html)
    assert res["status"] == "unparseable"
    assert "physical card available" in res["summary"]


def test_a_list_section_whose_anchor_is_renamed_is_unparseable(html, cfg):
    """The US prose anchor is a literal stated in the YAML. If the issuer
    rewords it, the section must read as unparseable, not as an empty list."""
    card = dict(al.card(cfg))
    doctored = html.replace("Physical card availability:", "Physical card availabilityXX:")
    assert doctored != html
    res = ac.compare(card, doctored)
    assert res["status"] == "unparseable"


def test_the_countries_heading_renamed_on_the_live_page_is_unparseable_not_a_match(html, cfg):
    """The issuer, not our config, renames the heading this time — the exact
    failure mode T-0134 opened with (article 758780's restructure). A parser
    that quietly fell back to 'no entries -> match' here would be the money
    bug this whole file exists to prevent."""
    card = dict(al.card(cfg))
    doctored = html.replace(
        "Countries where a physical Cash card is currently available",
        "Countries relocated heading xyz")
    assert doctored != html
    res = ac.compare(card, doctored)
    assert res["status"] == "unparseable"


# --- 3. unavailable_for: DEFAULT-DENY --------------------------------------
# The four acceptance literals of E-06, typed out.

def test_hungary_is_available():
    assert al.unavailable_for("Hungary") is False


def test_belgium_is_unavailable_by_the_issuers_own_cross():
    assert al.unavailable_for("Belgium") is True


def test_mississippi_is_unavailable_by_absence():
    assert al.unavailable_for("Mississippi") is True


def test_vietnam_is_unavailable_by_absence():
    assert al.unavailable_for("Vietnam") is True


def test_a_jurisdiction_nobody_ever_named_is_unavailable():
    """Default-deny is the whole point of the shape change. Under the old
    blocklist this answered False for every country on earth but nineteen."""
    assert al.unavailable_for("Freedonia") is True


# --- 4. slug <-> allow-list, BOTH directions -------------------------------

def test_every_unavailable_slug_maps_to_a_genuinely_unavailable_jurisdiction(cfg):
    """Direction 1. A slug routed away from ether.fi whose jurisdiction is in
    fact on the allow-list sends a reader who COULD be issued the card to a
    competitor — the Y56 over-correction, money deleted rather than saved."""
    jmap = al.slug_jurisdictions(cfg)
    for slug in al.unavailable_slugs(cfg):
        assert slug in jmap, f"{slug} has no slug_jurisdictions entry"
        name = jmap[slug]["name"]
        assert al.unavailable_for(name, cfg) is True, \
            f"{slug} routes away from ether.fi but {name} IS on the allow-list"


def test_every_mapped_unavailable_jurisdiction_is_routed_away(cfg):
    """Direction 2. A page about a jurisdiction that cannot get the card, still
    pointing at ether.fi, is the Y50 money bug itself."""
    for slug, j in al.slug_jurisdictions(cfg).items():
        if al.unavailable_for(j["name"], cfg):
            assert slug in al.unavailable_slugs(cfg), \
                f"{j['name']} cannot get the card but {slug} still routes to ether.fi"


def test_the_map_names_which_georgia_it_means(cfg):
    """`best-crypto-card-georgia` is the US state, not the country. The kind is
    stated, never inferred from the slug."""
    assert al.slug_jurisdictions(cfg)["best-crypto-card-georgia"]["kind"] == "us_state"


# --- 5. --accept applies EXACTLY the diff of the named alert ----------------

YAML_FIXTURE = """\
source:
  verified: "2020-01-01"
cards:
  etherfi:
    name: "ether.fi Cash"
    countries:
      - Spain
      - Hungary
    countries_explicit_no:
      - Belgium
    verified: "2026-06-01"
  cryptocom:
    name: "Crypto.com Visa"
    countries:
      - Brazil
    verified: "2026-06-02"
unavailable_slugs:
  - best-crypto-card-india
"""


def test_bump_touches_only_the_named_card():
    out = ac.bump_verified(YAML_FIXTURE, "etherfi", "2026-09-14")
    assert '    verified: "2026-09-14"' in out
    assert '    verified: "2026-06-02"' in out      # cryptocom untouched
    assert 'source:\n  verified: "2020-01-01"' in out  # the top-level one untouched


def test_bump_preserves_the_comments_that_explain_the_file():
    text = "# why this file exists\n" + YAML_FIXTURE
    out = ac.bump_verified(text, "etherfi", "2026-09-14")
    assert out.startswith("# why this file exists\n")


def test_bumping_an_unknown_card_raises_rather_than_writing_something():
    with pytest.raises(KeyError):
        ac.bump_verified(YAML_FIXTURE, "gnosispay", "2026-09-14")


def test_replace_list_rewrites_one_field_of_one_card():
    out = ac.replace_list(YAML_FIXTURE, "etherfi", "countries", ["France", "Japan"])
    card = yaml.safe_load(out)["cards"]
    assert card["etherfi"]["countries"] == ["France", "Japan"]
    assert card["etherfi"]["countries_explicit_no"] == ["Belgium"]   # sibling intact
    assert card["cryptocom"]["countries"] == ["Brazil"]              # other card intact


def test_replace_list_refuses_an_inline_list_rather_than_truncating_it():
    """Losing the tail of an availability list is a money bug. An unknown
    shape stops the write; it is never guessed at."""
    text = YAML_FIXTURE.replace(
        "    countries:\n      - Spain\n      - Hungary\n",
        "    countries: [Spain, Hungary]\n")
    with pytest.raises(ValueError):
        ac.replace_list(text, "etherfi", "countries", ["France"])


def _alerts(tmp_path, monkeypatch, rows):
    path = tmp_path / "availability-alerts.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    ypath = tmp_path / "availability.yaml"
    ypath.write_text(YAML_FIXTURE, encoding="utf-8")
    monkeypatch.setattr(ac, "ALERTS", path)
    monkeypatch.setattr(ac, "YAML_PATH", ypath)
    monkeypatch.setattr(ac, "JOURNAL", tmp_path / "fact-journal.jsonl")
    return path, ypath


RIGHT = {"id": "aaaaaaaaaaaa", "card": "etherfi", "status": "differs",
         "summary": "countries: issuer adds ['France']", "source": "x",
         "apply": {"countries": ["Spain", "Hungary", "France"]}}
WRONG = {"id": "bbbbbbbbbbbb", "card": "etherfi", "status": "differs",
         "summary": "countries: issuer removes everything", "source": "x",
         "apply": {"countries": ["Andorra"]}}


def test_accept_applies_exactly_the_named_alert_and_no_other(tmp_path, monkeypatch):
    _, ypath = _alerts(tmp_path, monkeypatch, [RIGHT, WRONG])
    assert ac.accept("aaaaaaaaaaaa") == 0
    card = yaml.safe_load(ypath.read_text())["cards"]["etherfi"]
    assert card["countries"] == ["Spain", "Hungary", "France"]
    assert card["countries"] != ["Andorra"], "the OTHER alert's diff was applied"


def test_accept_bumps_verified_because_the_lists_now_agree(tmp_path, monkeypatch):
    from datetime import date
    _, ypath = _alerts(tmp_path, monkeypatch, [RIGHT])
    ac.accept("aaaaaaaaaaaa")
    cards = yaml.safe_load(ypath.read_text())["cards"]
    assert cards["etherfi"]["verified"] == date.today().isoformat()
    assert cards["cryptocom"]["verified"] == "2026-06-02"


def test_accept_leaves_the_other_alerts_in_place(tmp_path, monkeypatch):
    apath, _ = _alerts(tmp_path, monkeypatch, [RIGHT, WRONG])
    ac.accept("aaaaaaaaaaaa")
    assert [r["id"] for r in json.loads(apath.read_text())] == ["bbbbbbbbbbbb"]


def test_accept_of_an_unknown_id_writes_nothing(tmp_path, monkeypatch):
    _, ypath = _alerts(tmp_path, monkeypatch, [RIGHT])
    before = ypath.read_text()
    assert ac.accept("ffffffffffff") == 1
    assert ypath.read_text() == before


def test_accept_of_an_unreachable_alert_writes_nothing(tmp_path, monkeypatch):
    """An `unreachable` alert carries no `apply` — there is no issuer list to
    replay. Accepting it must not bump `verified` off a check that never ran."""
    row = {"id": "cccccccccccc", "card": "etherfi", "status": "unreachable",
           "summary": "URLError", "source": "x"}
    _, ypath = _alerts(tmp_path, monkeypatch, [row])
    before = ypath.read_text()
    assert ac.accept("cccccccccccc") == 1
    assert ypath.read_text() == before


def test_the_alert_id_is_derived_from_the_diff_it_describes():
    """A re-run that finds a DIFFERENT diff gets a different id, so an
    `--accept` the operator queued yesterday cannot apply today's other diff."""
    assert ac.alert_id("etherfi", "a") == ac.alert_id("etherfi", "a")
    assert ac.alert_id("etherfi", "a") != ac.alert_id("etherfi", "b")
    assert ac.alert_id("etherfi", "a") != ac.alert_id("cryptocom", "a")


# --- 6. plasma (T-0142-08) — `compare_sections` needs no plasma-specific code,
# only the `cards.plasma` block in the YAML, because it is already generic
# over any card whose `source.parser` is `intercom_sections`. -----------------
#
# T-0142-08 attempt 2 (2026-09-25): the fixture below is a REAL captured
# download (curl -sL, HTTP 200, 86983 bytes), not constructed — attempt 1's
# `help.plasma.org` was an NXDOMAIN guess; the real host is
# `intercom.help/plasma-6c43763cd799`, found via the App Store listing's
# (id 6759033493) support link. Same capture also lives at
# taskloop/logs/T-0142-08-plasma-availability-matrix-not-stated/
# plasma-14703938-LIVE-CAPTURE-2026-09-25.html (README documents provenance).
# `scripts/availability-check.py --card plasma --dry-run` against the LIVE
# page (not just this fixture) also returns `match` — run directly, not
# proven by a unit test, since a live network fetch has no place in a gate
# that must stay green offline.

PLASMA_FIXTURE = (Path(__file__).resolve().parent / "fixtures"
                  / "plasma-14703938-2026-09-25.html")


@pytest.fixture(scope="module")
def plasma_html():
    assert PLASMA_FIXTURE.is_file(), f"captured fixture missing: {PLASMA_FIXTURE}"
    return PLASMA_FIXTURE.read_text(encoding="utf-8")


def test_plasma_uses_the_same_generic_intercom_sections_parser(cfg):
    assert al.card(cfg, "plasma")["source"]["parser"] == "intercom_sections"


# LAWS ARE LITERALS (Y49). Article 14703938 names exactly 7 unsupported
# countries, 12 document-blocked countries, 31 supported US states — typed
# out here, not read back from the YAML/parser under test, so a mutation that
# drops one name from either side of the comparison goes red.
PLASMA_N_UNSUPPORTED = 7
PLASMA_N_DOCUMENT_BLOCKED = 12
PLASMA_N_US_STATES = 31


def test_the_unsupported_countries_section_reads_as_a_flat_list_of_7(plasma_html):
    stream = ac.parse_intercom_stream(plasma_html, bold_paragraph_headings=True)
    cells = ac._stream_section(stream, "unsupported countries")
    found = ac.parse_flat_list(cells)
    assert len(found) == PLASMA_N_UNSUPPORTED, found
    assert found == ["Cuba", "Iran", "North Korea", "Russia", "Syria", "Ukraine", "Venezuela"]


def test_the_document_blocked_section_reads_as_a_flat_list_of_12(plasma_html):
    stream = ac.parse_intercom_stream(plasma_html, bold_paragraph_headings=True)
    cells = ac._stream_section(stream, "countries that are blocked but may still be eligible")
    found = ac.parse_flat_list(cells)
    assert len(found) == PLASMA_N_DOCUMENT_BLOCKED, found
    assert "India" in found and "Turkey" in found


def test_the_us_states_section_is_reachable_despite_being_a_bold_paragraph_not_a_heading(plasma_html):
    """The exact defect ruling-1 found in attempt 1: 'Supported US states'
    ships as a plain paragraph whose entire text is one <b>...</b> span, not
    a heading/subheading JSON node — without treating a wholly-bold paragraph
    as a section boundary, this section is unreachable and its 31 cells fold
    into whichever REAL heading precedes it."""
    stream = ac.parse_intercom_stream(plasma_html, bold_paragraph_headings=True)
    cells = ac._stream_section(stream, "supported us states")
    assert cells is not None, "the 'supported us states' section was not found at all"
    found = ac.parse_flat_list(cells)
    assert len(found) == PLASMA_N_US_STATES, found
    assert "Texas" in found and "Arizona" not in found


def test_a_heading_wrapped_in_bold_markup_still_matches_its_declared_name(plasma_html):
    """Plasma's real heading/subheading nodes ship their text as
    '\\u003cb\\u003eUnsupported countries\\u003c/b\\u003e', not plain text like
    ether.fi's — without stripping the markup, `_stream_section`'s exact
    lowercase match against the YAML's literal heading always misses."""
    stream = ac.parse_intercom_stream(plasma_html, bold_paragraph_headings=True)
    heads = [h for h, _ in stream]
    assert "unsupported countries" in heads, heads
    assert not any("\\u003c" in h for h in heads), heads


def test_the_checked_in_plasma_lists_match_the_captured_fixture(plasma_html, cfg):
    res = ac.compare(al.card(cfg, "plasma"), plasma_html)
    assert res["status"] == "match", res["summary"]


def test_removing_texas_from_the_fixture_is_a_difference_not_a_match(plasma_html, cfg):
    doctored = plasma_html.replace(
        '{"type":"paragraph","text":"Texas","class":"no-margin"},', "")
    assert doctored != plasma_html, "fixture shape changed — the Texas cell moved"
    res = ac.compare(al.card(cfg, "plasma"), doctored)
    assert res["status"] == "differs"
    assert "us_states" in res["summary"] and "Texas" in res["summary"]


def test_plasma_countries_explicit_no_is_wired_into_the_checker(cfg):
    """All 7 of the issuer's `countries_explicit_no` names are known now
    (config/facts/plasma.yaml, status: current, resolved from `disputed` in
    T-0142-08 attempt 2) — the section is wired into `source.sections` the
    same as `us_states`/`countries_document_blocked`."""
    sections = al.card(cfg, "plasma")["source"]["sections"]
    assert {"us_states", "countries_explicit_no", "countries_document_blocked"} <= sections.keys()


def test_removing_cuba_from_the_fixture_is_a_difference_not_a_match(plasma_html, cfg):
    doctored = plasma_html.replace(
        '{"type":"paragraph","text":"Cuba","class":"no-margin"},', "")
    assert doctored != plasma_html, "fixture shape changed — the Cuba cell moved"
    res = ac.compare(al.card(cfg, "plasma"), doctored)
    assert res["status"] == "differs"
    assert "countries_explicit_no" in res["summary"] and "Cuba" in res["summary"]


def test_a_document_blocked_country_missing_from_the_fixture_is_a_difference(plasma_html, cfg):
    doctored = plasma_html.replace(
        '{"type":"paragraph","text":"India","class":"no-margin"},', "")
    assert doctored != plasma_html
    res = ac.compare(al.card(cfg, "plasma"), doctored)
    assert res["status"] == "differs"
    assert "countries_document_blocked" in res["summary"] and "India" in res["summary"]
