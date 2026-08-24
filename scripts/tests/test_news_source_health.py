"""G26 (Y55) — a silent feed is a reportable state, not a shrug.

`news-ingest.py` prints one line per source: "N fetched, M candidates". For six
weeks every run printed "0 candidates" for all three feeds, and that single
number is the same whether

    the feed is dead        (nothing fetched at all), or
    the feed is stale       (fetched, but nothing inside the freshness window), or
    the keyword gate rejected everything it fetched.

Only the third is healthy. The first two are outages that look exactly like
editorial discipline, which is why they can persist unnoticed — the project
ledger already carries the cron-silent-skip signature for this shape.

So the law: every run leaves a machine-readable record per source. A fetch
failure writes `source_failed` with its status; every source writes
`source_result` with the funnel decomposed. stderr does not count — it lands in
a cron log nobody reads.

Expected counts below are literals. They are NOT recomputed from the module
under test: a gate that calls `_passes_keyword_gate` to predict
`_passes_keyword_gate` moves both sides of the comparison under mutation and
stays green (Y49, restated in Y54).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from email.utils import format_datetime
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "news_ingest", ROOT / "scripts" / "news-ingest.py")
ni = importlib.util.module_from_spec(_SPEC)
sys.modules["news_ingest"] = ni
_SPEC.loader.exec_module(ni)


# Three items. Two carry a card term AND a crypto/regulatory term, so they pass
# the production two-list gate; the third is a pure-equities notice that must
# not. Published now, so all three are inside the 36 h freshness window.
def _rss() -> bytes:
    now = format_datetime(datetime.now(timezone.utc))
    items = [
        ("FCA fines a cryptoasset firm over debit card marketing",
         "The regulator issued a penalty concerning a crypto debit card promotion."),
        ("EBA consults on e-money institution rules for stablecoin cards",
         "Guidance for electronic money institution issuers of a stablecoin card."),
        ("ESMA publishes equities post-trade transparency scorecard",
         "Annual review of settlement discipline for listed shares."),
    ]
    body = "".join(
        f"<item><title>{t}</title><link>https://example.org/{i}</link>"
        f"<description>{d}</description><pubDate>{now}</pubDate></item>"
        for i, (t, d) in enumerate(items))
    return (f'<?xml version="1.0"?><rss version="2.0"><channel>'
            f"<title>t</title>{body}</channel></rss>").encode()


class _Resp:
    def __init__(self, status: int, content: bytes = b""):
        self.status_code = status
        self.content = content
        self.headers = {}


@pytest.fixture()
def run(tmp_path, monkeypatch):
    """Run the real main() in dry-run against two stubbed sources and return
    the JSONL records it left behind."""
    cfg = yaml.safe_load((ROOT / "config" / "news_sources.yaml").read_text(encoding="utf-8"))
    src_file = tmp_path / "sources.yaml"

    def _go(dead_status: int = 500, getter=None):
        cfg["sources"] = [
            {"name": "Healthy Feed", "url": "https://healthy.example/rss",
             "type": "rss", "category": "regulator-uk", "priority": 1},
            {"name": "Dead Feed", "url": "https://dead.example/rss",
             "type": "rss", "category": "regulator-us", "priority": 1},
        ]
        src_file.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        monkeypatch.setattr(ni, "SOURCES_FILE", src_file)
        monkeypatch.setattr(ni, "LOG_DIR", tmp_path / "logs")
        monkeypatch.setattr(ni, "STATE_DB", tmp_path / "state" / "news_seen.db")

        def fake_get(url, **kw):
            return _Resp(200, _rss()) if "healthy" in url else _Resp(dead_status)

        monkeypatch.setattr(ni.requests, "get", getter or fake_get)
        monkeypatch.setattr(sys, "argv", ["news-ingest", "--dry-run"])
        assert ni.main() == 0
        logs = list((tmp_path / "logs").glob("*.jsonl"))
        return [json.loads(l) for f in logs
                for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]

    return _go


def _one(recs, event, source):
    hits = [r for r in recs if r.get("event") == event and r.get("source") == source]
    assert len(hits) == 1, f"expected exactly one {event} for {source}, got {hits}"
    return hits[0]


def test_a_dead_feed_is_written_to_the_log_not_only_to_stderr(run):
    """HTTP 500 -> `source_failed` in the JSONL, carrying the status."""
    recs = run(dead_status=500)
    rec = _one(recs, "source_failed", "Dead Feed")
    assert rec["http_status"] == 500
    assert rec["url"] == "https://dead.example/rss"
    # ...and the healthy one is not slandered as failed.
    assert not [r for r in recs
                if r.get("event") == "source_failed" and r.get("source") == "Healthy Feed"]


def test_the_funnel_decomposes_so_zero_candidates_has_a_cause(run):
    """Literals, hand-counted from the fixture above: 3 items fetched, all 3
    inside the freshness window, 2 pass the two-list keyword gate, and neither
    has been seen before, so both become candidates."""
    rec = _one(run(), "source_result", "Healthy Feed")
    assert rec["fetched"] == 3
    assert rec["fresh"] == 3
    assert rec["passed_gate"] == 2
    assert rec["candidates"] == 2


def test_a_dead_feed_still_reports_its_zero_row(run):
    """The failed source must also leave a `source_result`: an absent row and a
    zero row are the same silence this gate exists to remove."""
    rec = _one(run(), "source_result", "Dead Feed")
    assert rec["fetched"] == 0
    assert rec["passed_gate"] == 0


def test_a_transport_error_is_reported_with_its_class(run):
    """A timeout is not an HTTP status — it must still land as `source_failed`,
    naming the exception class."""
    def boom(url, **kw):
        if "healthy" in url:
            return _Resp(200, _rss())
        raise ni.requests.Timeout("read timed out")

    rec = _one(run(getter=boom), "source_failed", "Dead Feed")
    assert rec["error_class"] == "Timeout"
