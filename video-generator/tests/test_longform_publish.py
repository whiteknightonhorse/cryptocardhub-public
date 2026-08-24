"""G17-longform — the laws of publishing a long-form video.

Every expectation is a literal. The tests drive the REAL entry points and
intercept at the `pub_yt.upload` boundary, because a correct helper nobody
calls is still a broken pipeline (Y53).

Nothing here touches the production publish_log: `dedup.log_publish` and the
row reader are both stubbed, so a test run can never invent a publication.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline import dedup, longform


@pytest.fixture
def rendered(tmp_path, monkeypatch):
    """A believable rendered long-form directory, with real footage."""
    def _make(slug: str = "is-redotpay-legit", day: str = "2026-08-11"):
        d = tmp_path / "longform" / day / slug
        (d / "media").mkdir(parents=True)
        (d / "media" / "clip_00.mp4").write_bytes(b"x")
        (d / "final.mp4").write_bytes(b"x")
        (d / "description.txt").write_text(
            "Watch the full guide: https://defycard.com/reviews/is-redotpay-legit/"
            "?utm_source=youtube&utm_medium=longform&utm_content=yo-20260811-90\n"
            "0:00 Quick answer\n", encoding="utf-8")
        (d / "summary.json").write_text(json.dumps({
            "slug": slug, "title": "Is RedotPay Legit?",
            "final_path": str(d / "final.mp4"), "published": False,
        }), encoding="utf-8")
        monkeypatch.setattr(longform, "out_dir", lambda s, day=None: d)
        return d
    return _make


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("VG_LOG_DIR", str(tmp_path / "logs"))
    # A gate must never read the operator's production config: with
    # VG_LF_PRIVACY=public set in .env these tests went red for a reason that
    # had nothing to do with the code. Each test states the value it means.
    monkeypatch.delenv("VG_LF_PRIVACY", raising=False)
    monkeypatch.setattr(longform, "IDS_PATH", tmp_path / "longform_ids.json")
    monkeypatch.setattr(dedup, "log_publish", lambda *a, **k: None)
    monkeypatch.setattr(longform, "_published_rows", lambda: [])


def _capture(monkeypatch) -> dict:
    seen: dict = {}

    def _fake_upload(**kw):
        seen.update(kw)
        return {"video_id": "TESTVIDEO01", "url": "https://youtu.be/TESTVIDEO01"}

    monkeypatch.setattr(longform.pub_yt, "upload", _fake_upload)
    return seen


def _log_lines(tmp_path) -> list[dict]:
    out: list[dict] = []
    for f in (tmp_path / "logs").glob("*.jsonl"):
        out += [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    return out


# --- law 1: the boundary ----------------------------------------------------

def test_upload_boundary_carries_the_longform_contract(rendered, monkeypatch):
    rendered()
    seen = _capture(monkeypatch)
    longform.publish_one("is-redotpay-legit", day="2026-08-11")

    assert "utm_medium=longform" in seen["description"]
    assert "#Shorts" not in seen["description"]
    assert seen["privacy"] == "unlisted"
    assert "longform" in str(seen["video_path"])


def test_privacy_follows_the_environment_not_a_literal(rendered, monkeypatch):
    rendered()
    monkeypatch.setenv("VG_LF_PRIVACY", "public")
    seen = _capture(monkeypatch)
    longform.publish_one("is-redotpay-legit", day="2026-08-11")
    assert seen["privacy"] == "public"


def test_default_privacy_is_unlisted(monkeypatch):
    """With nothing set, the reversible state wins — deletion is forbidden, so
    a mistake must be hideable. The operator may override to public; that is a
    decision, and a decision has to be made explicitly rather than inherited."""
    monkeypatch.delenv("VG_LF_PRIVACY", raising=False)
    assert longform.privacy() == "unlisted"


# --- law 2: one per UTC day, and the skip says why --------------------------

def test_second_run_same_day_is_a_noop_with_a_reason(monkeypatch, tmp_path):
    today = datetime.now(timezone.utc).date().isoformat()
    monkeypatch.setattr(longform, "_published_rows",
                        lambda: [(f"{today}T09:00:00+00:00", 90, "VID")])
    out = longform.publish_next()
    assert out == {"skipped": "already_published_today"}
    reasons = [r.get("reason") for r in _log_lines(tmp_path)]
    assert "already_published_today" in reasons


# --- law 3: the id registry -------------------------------------------------

def test_pilot_ids_are_exactly_ninety_to_ninetyfour():
    assert [longform.video_id_for(s) for s in longform.PILOT_SLUGS] == [90, 91, 92, 93, 94]


def test_a_new_slug_is_minted_from_one_hundred_thousand():
    assert longform.video_id_for("best-crypto-card-japan") == 100000
    assert longform.video_id_for("best-crypto-card-japan") == 100000  # stable
    assert longform.video_id_for("crypto-card-fees-explained") == 100001


# --- law 4: one page, one long video, and no silent skips -------------------

def test_publishing_a_slug_twice_is_refused(rendered, monkeypatch):
    rendered()
    monkeypatch.setattr(longform, "slug_published", lambda slug: True)
    _capture(monkeypatch)
    with pytest.raises(RuntimeError, match="already has a long-form video"):
        longform.publish_one("is-redotpay-legit", day="2026-08-11")


def test_an_exhausted_queue_refuses_loudly(monkeypatch, tmp_path):
    monkeypatch.setattr(longform, "queue_slugs", lambda: [])
    with pytest.raises(RuntimeError, match="no unpublished long-form candidates"):
        longform.publish_next()
    reasons = [r.get("reason") for r in _log_lines(tmp_path)]
    assert "queue_exhausted" in reasons


def test_a_stale_queue_refuses_rather_than_publishing_old_rankings(monkeypatch, tmp_path):
    q = tmp_path / "longform_queue.json"
    q.write_text(json.dumps({"built_on": "2026-01-01", "slugs": ["x"]}), encoding="utf-8")
    monkeypatch.setattr(longform, "QUEUE_PATH", q)
    with pytest.raises(RuntimeError, match="stale queue"):
        longform.queue_slugs()
