"""CCH-SEO-13 — description backfill. Expected values are LITERALS (Y49)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
spec = importlib.util.spec_from_file_location("ytb", ROOT / "scripts/yt-description-backfill.py")
ytb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ytb)


def _vids(tmp_path):
    (tmp_path / "aaa.json").write_text(json.dumps({"target_pseo_slug": "hub/best-stablecoin-card"}))
    (tmp_path / "bbb.json").write_text(json.dumps({"target_pseo_slug": None}))
    return tmp_path


def test_page_path(tmp_path):
    d = _vids(tmp_path)
    assert ytb.page_path("aaa", d) == "/hub/best-stablecoin-card/"
    assert ytb.page_path("bbb", d) == "/videos/bbb/"
    assert ytb.page_path("zzz", d) is None


def test_needs_link_other_utm_is_false():
    desc = "x https://defycard.com/hub/best-stablecoin-card/?utm_source=youtube&utm_medium=shorts&utm_content=q"
    assert ytb.needs_link(desc, "/hub/best-stablecoin-card/") is False
    assert ytb.needs_link(desc, "/videos/aaa/") is True


def test_medium_for():
    assert ytb.medium_for("PT45S") == "shorts"
    assert ytb.medium_for("PT3M") == "shorts"
    assert ytb.medium_for("PT3M20S") == "longform"
    assert ytb.medium_for("PT3M2S") == "dead_zone"


def test_new_description_appends_verbatim():
    out = ytb.new_description("orig\n#Shorts", "/videos/bbb/", "shorts", "bbb")
    assert out == ("orig\n#Shorts\n\n👉 Full breakdown: https://defycard.com/videos/bbb/"
                   "?utm_source=youtube&utm_medium=shorts&utm_content=yt-backfill-bbb")


def test_classify_actions(tmp_path):
    d = _vids(tmp_path)
    base = {"published": "2026-01-01", "duration": "PT30S", "privacy": "public", "description": "hi"}
    assert ytb.classify({**base, "id": "aaa"}, d)["action"] == "update"
    assert ytb.classify({**base, "id": "zzz"}, d)["action"] == "no_page"
    assert ytb.classify({**base, "id": "aaa", "privacy": "unlisted"}, d)["action"] == "not_public"
    assert ytb.classify({**base, "id": "aaa", "duration": "PT3M2S"}, d)["action"] == "dead_zone"
    assert ytb.classify({**base, "id": "aaa", "description": "defycard.com/hub/best-stablecoin-card/"}, d)["action"] == "skip_has_link"
    assert ytb.classify({**base, "id": "aaa", "description": "x" * 4990}, d)["action"] == "too_long"


class _Req:
    def __init__(self, fn): self.fn = fn
    def execute(self): return self.fn()


class _Videos:
    def __init__(self, calls, fail_after=None):
        self.calls, self.fail_after = calls, fail_after
    def update(self, part, body):
        def run():
            if self.fail_after is not None and len(self.calls) >= self.fail_after:
                class Quota(Exception):
                    resp = type("R", (), {"status": 403})()
                    content = b'{"error":{"errors":[{"reason":"quotaExceeded"}]}}'
                raise Quota()
            self.calls.append((part, body))
        return _Req(run)


class _YT:
    def __init__(self, fail_after=None):
        self.calls = []
        self._v = _Videos(self.calls, fail_after)
    def videos(self): return self._v


SNIP = {"title": "T", "tags": ["a"], "categoryId": "22", "defaultLanguage": "en", "description": "orig"}


def _items(n):
    items = [{"id": f"v{i}", "description": "orig", "snippet": dict(SNIP)} for i in range(n)]
    rows = [{"id": f"v{i}", "path": "/videos/x/", "medium": "shorts", "action": "update"} for i in range(n)]
    return items, rows


def test_update_body_is_full_snippet(tmp_path):
    items, rows = _items(1)
    yt = _YT()
    ytb.apply_updates(yt, items, rows, 5, tmp_path / "log.jsonl")
    part, body = yt.calls[0]
    assert part == "snippet" and body["id"] == "v0"
    assert body["snippet"]["title"] == "T" and body["snippet"]["tags"] == ["a"]
    assert body["snippet"]["categoryId"] == "22" and body["snippet"]["defaultLanguage"] == "en"
    assert body["snippet"]["description"].startswith("orig\n\n👉 Full breakdown:")
    assert len((tmp_path / "log.jsonl").read_text().splitlines()) == 1


def test_max_stops_at_n(tmp_path):
    items, rows = _items(4)
    yt = _YT()
    assert ytb.apply_updates(yt, items, rows, 2, tmp_path / "l.jsonl") == (2, 2, False)
    assert len(yt.calls) == 2


def test_quota_stop(tmp_path):
    items, rows = _items(4)
    yt = _YT(fail_after=1)
    assert ytb.apply_updates(yt, items, rows, 4, tmp_path / "l.jsonl") == (1, 3, True)
