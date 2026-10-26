"""T-0142-01 — an H1 video recovers its topic from summary.json before the
sync script gives up and marks it off-pool, and a freshly-uploaded video
isn't dropped just because YouTube hasn't finished processing it yet.

Two defects, same script:

1. `topics.get(topic_id)` only ever looks at topic_pool.json / the VIRAL
   pool. H1 topics come from `targets-<week>.json` and are NEVER in either
   pool — every H1 video was permanently falling into the off-pool branch
   (`target_pseo_slug=null`), so its VideoObject never reached its own pSEO
   page. `summary.json` (written at render time) carries the same topic
   dict enriched at runtime and must be tried BEFORE declaring off-pool.

2. `_is_publishable` required `uploadStatus == "processed"` exactly. A video
   minutes old still reads `uploadStatus=uploaded` (YouTube hasn't finished
   processing) — with the old filter that video vanishes from the sync until
   the next deploy happens to run after processing completes.

A third defect surfaced while fixing (1): `_match_pseo_slug`'s explicit-hint
branch returned the topic's bare `target_pseo_slug` as-is, but both readers
of that field (`[bucket]/[slug].astro`'s cross-collection filter and
`build-video-sitemap.py`) key on the FULL `bucket/slug` path. H1 topics store
bucket and slug as two separate fields, so the bare hint never matched
anything — fixed alongside so the acceptance criterion (VideoObject actually
reaching `dist/`) is provable, not just "a non-null field got written".

THE LAWS ARE LITERALS HERE (Y49): expected values are typed out, not derived
from the module under test.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "sync_yt_videos", ROOT / "scripts" / "sync-yt-videos.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sync_yt_videos"] = mod
    spec.loader.exec_module(mod)
    return mod


syv = _load()


# ---------------------------------------------------------------------------
# Law 1 — uploadStatus in {uploaded, processed} counts as live.
# ---------------------------------------------------------------------------

def test_is_publishable_accepts_uploaded_and_processed():
    uploaded = {"status": {"privacyStatus": "public", "uploadStatus": "uploaded", "madeForKids": False}}
    processed = {"status": {"privacyStatus": "public", "uploadStatus": "processed", "madeForKids": False}}
    assert syv._is_publishable(uploaded) == (True, "ok")
    assert syv._is_publishable(processed) == (True, "ok")


def test_is_publishable_still_rejects_dead_or_bad_states():
    assert syv._is_publishable(None) == (False, "deleted")
    private = {"status": {"privacyStatus": "private", "uploadStatus": "processed", "madeForKids": False}}
    assert syv._is_publishable(private)[0] is False
    failed = {"status": {"privacyStatus": "public", "uploadStatus": "failed", "madeForKids": False}}
    assert syv._is_publishable(failed) == (False, "uploadStatus=failed")
    kids = {"status": {"privacyStatus": "public", "uploadStatus": "processed", "madeForKids": True}}
    assert syv._is_publishable(kids) == (False, "made_for_kids")


# ---------------------------------------------------------------------------
# Law 2 — an explicit bare-slug hint gets its bucket prefixed back on, and
# only survives if the article actually exists (never embed on a 404).
# ---------------------------------------------------------------------------

def test_match_pseo_slug_prefixes_bucket_onto_bare_hint():
    # Real repo article — this exact page is the T-0142-01 acceptance target.
    topic = {"bucket": "faq", "target_pseo_slug": "who-can-get-ether-fi-cash"}
    assert syv._match_pseo_slug(topic, {}) == "faq/who-can-get-ether-fi-cash"


def test_match_pseo_slug_refuses_hint_with_no_matching_article():
    topic = {"bucket": "faq", "target_pseo_slug": "this-page-does-not-exist-nowhere"}
    assert syv._match_pseo_slug(topic, {}) is None


# ---------------------------------------------------------------------------
# Law 3 — end to end: an H1 topic absent from both pools attaches via
# summary.json instead of falling into the off-pool branch.
# ---------------------------------------------------------------------------

def _seed_db(db_path: Path, *, ts: str, topic_id: int, video_id: str) -> None:
    db = sqlite3.connect(str(db_path))
    db.execute("""
        CREATE TABLE publish_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts TIMESTAMP NOT NULL,
          channel TEXT NOT NULL,
          topic_id INTEGER NOT NULL,
          transport TEXT NOT NULL,
          external_id TEXT,
          status TEXT NOT NULL,
          notes TEXT
        )
    """)
    db.execute(
        "INSERT INTO publish_log (ts, channel, topic_id, transport, external_id, status) "
        "VALUES (?, 'youtube', ?, 'youtube_api', ?, 'ok')",
        (ts, topic_id, video_id),
    )
    db.commit()
    db.close()


def _patch_paths(monkeypatch, tmp_path: Path, *, pseo_dir: Path):
    monkeypatch.delenv("YOUTUBE_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)
    monkeypatch.delenv("YOUTUBE_CLIENT_SECRET", raising=False)
    empty_pool = tmp_path / "topic_pool.json"
    empty_pool.write_text("{}", encoding="utf-8")
    empty_viral = tmp_path / "viral_pool.json"
    empty_viral.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(syv, "TOPIC_POOL", empty_pool)
    monkeypatch.setattr(syv, "VIRAL_POOL", empty_viral)
    monkeypatch.setattr(syv, "PSEO_DIR", pseo_dir)


def test_h1_topic_attaches_via_summary_json(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "published.db"
    _seed_db(db_path, ts="2026-09-21T10:00:00Z", topic_id=2626716, video_id="xM8lcMhHOIU")

    output_dir = tmp_path / "output"
    render_dir = output_dir / "youtube" / "2026-09-21" / "topic_2626716"
    render_dir.mkdir(parents=True)
    (render_dir / "summary.json").write_text(json.dumps({
        "topic": {
            "id": 2626716,
            "pool": "h1",
            "bucket": "faq",
            "target_pseo_slug": "who-can-get-ether-fi-cash",
            "primary": "who can get ether.fi cash",
        },
        "title": "CAN YOU GET IT",
    }), encoding="utf-8")

    pseo_dir = tmp_path / "pseo"
    pseo_dir.mkdir()
    (pseo_dir / "who-can-get-ether-fi-cash.md").write_text(
        '---\nbucket: "faq"\nslug: "who-can-get-ether-fi-cash"\n---\n', encoding="utf-8")

    videos_dir = tmp_path / "videos"
    _patch_paths(monkeypatch, tmp_path, pseo_dir=pseo_dir)
    monkeypatch.setattr(syv, "DB", db_path)
    monkeypatch.setattr(syv, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(syv, "VIDEOS_DIR", videos_dir)

    assert syv.main() == 0

    out_json = videos_dir / "xM8lcMhHOIU.json"
    assert out_json.exists(), "video JSON never written — fell into off-pool without a bootstrap fallback?"
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["target_pseo_slug"] == "faq/who-can-get-ether-fi-cash"

    out = capsys.readouterr().out
    assert "h1: attached 1/1" in out


def test_h1_topic_without_summary_json_stays_off_pool(tmp_path, monkeypatch, capsys):
    """Mutation-equivalent of 'remove the summary.json read': with no
    render dir at all (so summary.json is unreachable), the topic can only
    come from topic_pool/viral — which never carries H1 ids — so the video
    MUST fall into the off-pool branch. Pins the behaviour the ruling's first
    mutation ("убрать чтение summary.json, снова off-pool") describes.
    """
    db_path = tmp_path / "published.db"
    _seed_db(db_path, ts="2026-09-21T10:00:00Z", topic_id=2626716, video_id="xM8lcMhHOIU")

    output_dir = tmp_path / "output"  # no render dir created at all
    pseo_dir = tmp_path / "pseo"
    pseo_dir.mkdir()

    videos_dir = tmp_path / "videos"
    _patch_paths(monkeypatch, tmp_path, pseo_dir=pseo_dir)
    monkeypatch.setattr(syv, "DB", db_path)
    monkeypatch.setattr(syv, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(syv, "VIDEOS_DIR", videos_dir)

    assert syv.main() == 0

    out_json = videos_dir / "xM8lcMhHOIU.json"
    assert out_json.exists()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["target_pseo_slug"] is None
    assert data["topic_primary"] == "off-pool"

    out = capsys.readouterr().out
    assert "h1: attached 0/0" in out
