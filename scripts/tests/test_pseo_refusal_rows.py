"""KW-04b: a refused bank page leaves a row in logs/pseo/<date>.jsonl, and cools."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_money_gate as tmg  # noqa: E402  (reuses its _wire/_one_cluster harness)

pg = tmg.pg

_BODY = "Gemini pays $999 a month on groceries.\n\n<<AFFILIATE_BUTTON>>\n"


def _run(monkeypatch, tmp_path):
    payload = dict(tmg._PAYLOAD, body_markdown=_BODY)
    tmg._wire(monkeypatch, tmp_path, payload)
    monkeypatch.setattr(pg, "STATE_DIR", tmp_path / "state")
    assert pg.main(["--limit", "1"]) == 1
    today = datetime.now(timezone.utc).date().isoformat()
    rows = [json.loads(x) for x in (tmp_path / "logs" / f"{today}.jsonl").read_text().splitlines()]
    return [r for r in rows if r.get("event") == "refused"]


def test_g36_refusal_row_is_in_the_jsonl_with_context(monkeypatch, tmp_path):
    (row,) = _run(monkeypatch, tmp_path)
    assert row["gate"] == "G36"
    assert "unbacked_number:$999" in row["reasons"]
    assert any("$999" in w for w in row["context"])


def test_refused_bank_slug_cools_for_the_conveyor_only(monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path)
    monkeypatch.undo()          # real build_queue back; cluster comes from the real bank
    monkeypatch.setattr(pg, "FAQ_LEDGER", tmp_path / "faq-cohorts.json")
    clusters = [tmg._one_cluster()]
    slug = "money-gate-wiring-probe"
    monkeypatch.setattr(pg, "existing_slugs", lambda: set())
    assert slug in pg._bank_refused_recently()
    assert pg.build_queue(clusters, False, False, skip_refused=True) == []
    assert pg.build_queue(clusters, False, False, skip_refused=False) == clusters
