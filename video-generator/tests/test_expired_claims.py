"""G35 — a voiceover cannot be corrected after upload, so an expired claim
stops before the upload.

The 2026-08-11 render of `who-can-get-ether-fi-cash` says "up to 15 % on food"
four times in its narration. It was never published — the audit of the channel
found 210 videos, 0 descriptions and 0 of 207 comments carrying it — but the
render sits on disk fully built, one `--publish --slug` away from shipping.
Deleting a published video is constitutionally banned here, so the only place
this can be caught is before the upload.

THE LAW: the refusal patterns come from the FACT FILE, not from this test and
not from a constant in the publisher. A row that carries EXPIRED plus a
percentage becomes a refusal. That way the guard cannot drift away from the
facts it guards — when the promo is re-declared or a new one lapses, the guard
follows without an edit.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

VG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VG))

from pipeline import longform as lf  # noqa: E402


def test_the_patterns_come_from_the_fact_file():
    """Not a hardcoded 15: the guard reads whatever the fact file marks."""
    pats = lf.expired_claim_patterns()
    assert pats, "no expired claim found in SKILL.md — has the marker changed?"
    assert any("15" in why for _rx, why in pats)


def test_a_script_stating_the_expired_promo_is_refused(tmp_path):
    d = tmp_path / "render"
    d.mkdir()
    (d / "script.json").write_text(json.dumps({
        "sections": [{"voice": "Cashback is up to 3 % standard and up to 15 % "
                               "on dining and groceries."}]}), encoding="utf-8")
    with pytest.raises(RuntimeError) as e:
        lf.refuse_expired_claims(d, "some-slug")
    msg = str(e.value)
    assert "REFUSING to publish" in msg
    assert "expired" in msg.lower()
    assert "voiceover cannot be edited" in msg


def test_the_same_claim_in_the_description_is_refused(tmp_path):
    d = tmp_path / "render"
    d.mkdir()
    (d / "description.txt").write_text(
        "Earn up to 15 % cashback on groceries with ether.fi.", encoding="utf-8")
    with pytest.raises(RuntimeError):
        lf.refuse_expired_claims(d, "some-slug")


def test_a_clean_render_passes(tmp_path):
    d = tmp_path / "render"
    d.mkdir()
    (d / "script.json").write_text(json.dumps({
        "sections": [{"voice": "Cashback is up to 3 % on the first band of "
                               "monthly spend, then 1 %, then 0.5 %."}]}),
        encoding="utf-8")
    (d / "description.txt").write_text("A clean description.", encoding="utf-8")
    lf.refuse_expired_claims(d, "some-slug")     # must not raise


def test_an_unrelated_fifteen_percent_is_not_refused(tmp_path):
    """The pattern requires the food context — `15 % of applicants` is prose,
    not the expired promo, and a guard that blocks it would teach people to
    disable the guard."""
    d = tmp_path / "render"
    d.mkdir()
    (d / "script.json").write_text(json.dumps({
        "sections": [{"voice": "About 15 % of applicants fail KYC on the "
                               "first attempt."}]}), encoding="utf-8")
    lf.refuse_expired_claims(d, "some-slug")     # must not raise


def test_the_guard_is_wired_into_publish_one():
    """Y53, the lesson that keeps arriving: a correct helper nobody calls is
    still a broken pipeline. The mutation this catches is 'guard written,
    never invoked'."""
    src = (VG / "pipeline" / "longform.py").read_text(encoding="utf-8")
    body = src[src.index("def publish_one("):]
    body = body[:body.index("\ndef ", 1)] if "\ndef " in body[1:] else body
    assert "refuse_expired_claims(" in body, \
        "publish_one does not call the guard"
    assert body.index("refuse_expired_claims(") < body.index("pub_yt.upload("), \
        "the guard must run BEFORE the upload, not after"
