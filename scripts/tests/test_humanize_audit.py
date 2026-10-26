"""CCH-HUM-07: blind audit sampling + the weekly line. Literals only (Y49): slugs and
verdicts below were worked out by hand from sha256(slug)[:8]; no network."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import humanize_gate as hg  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("weekly_report_h7", ROOT / "scripts" / "weekly-report.py")
wr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(wr)

ORIG = {"body_markdown": "Original text."}
NEW = {"body_markdown": "Rewritten text."}


def test_sampling_is_deterministic_literal():
    assert hg.audit_sample("a") is True      # h % 10 == 0
    assert hg.audit_sample("f") is True
    assert hg.audit_sample("b") is False     # h % 10 == 6
    assert hg.audit_sample("a") is True


def _run(slug, ans, tmp_path):
    seen = {}

    def call(system, user, model=None):
        seen.update(system=system, user=user)
        return ans, {}, 0.002
    return hg.audit_pair(slug, ORIG, NEW, call=call, log_dir=tmp_path), seen


def test_audit_blind_prompt_and_row(tmp_path):
    row, seen = _run("a", "1", tmp_path)      # 'a': original sits in position 2
    assert seen["system"] == "You are a blind judge."
    assert seen["user"].startswith("Text 1:\nRewritten text.")
    assert row["original_pos"] == 2 and row["verdict"] == "rewrite"
    assert json.loads((tmp_path / "audit.jsonl").read_text())["slug"] == "a"
    row, _ = _run("f", "1", tmp_path)         # 'f': original in position 1
    assert row["original_pos"] == 1 and row["verdict"] == "original"
    assert _run("f", "tie", tmp_path)[0]["verdict"] == "tie"


def test_audit_skips_unsampled_and_garbage(tmp_path):
    assert _run("b", "1", tmp_path)[0] is None
    assert _run("a", "banana", tmp_path)[0] is None


def _fixture(tmp_path, wins, pairs):
    d = tmp_path / "logs" / "humanize"
    d.mkdir(parents=True)
    rows = [{"event": "humanize", "rewritten": True, "refused_reason": None, "flags_before": 10, "flags_after": 2},
            {"event": "humanize", "rewritten": False, "refused_reason": "G37:x", "flags_before": 20, "flags_after": 20}]
    (d / "2026-10-18.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    (d / "2026-01-01.jsonl").write_text(json.dumps(rows[0]) + "\n")   # outside 28d
    au = [{"ts": "2026-10-17T00:00:00+00:00", "verdict": "original" if i < wins else "rewrite"}
          for i in range(pairs)]
    (d / "audit.jsonl").write_text("\n".join(json.dumps(r) for r in au) + "\n")
    return wr._collect_humanize(root=tmp_path, today=date(2026, 10, 19))["humanize"]


def test_weekly_line_renders(tmp_path):
    h = _fixture(tmp_path, 1, 10)
    assert (h["pages"], h["rewritten"], h["refused"]) == (2, 1, 1)
    assert (h["median_flags_before"], h["median_flags_after"]) == (15, 11)
    assert (h["audit_pairs"], h["original_wins"]) == (10, 1)
    text = wr.render({
        "since": "2026-10-12", "until": "2026-10-18", "humanize": h,
        "human_sessions": 1, "raw_sessions": 2, "bot_direct": 1, "by_source": [],
        "clicks_by_source": {}, "clicks_by_partner": {}, "utm_content": {}, "videos_7d": 0,
        "median_views_7d": 0, "retention_7d": 0,
        "pseo": {"pages_7d": 0, "idle_ticks": 0, "pause_ticks": 0, "queue": 0,
                 "clusters_total": 1, "covered": 1, "frozen": 0, "thin": 0}})
    assert "HUMANIZE 28d: 2 pages / 1 rewritten / 1 refused" in text
    assert "1 original wins of 10 pairs" in text


def test_four_of_ten_raises_action_required(tmp_path):
    h = _fixture(tmp_path, 4, 10)
    hdr = wr.action_required({"humanize": h})
    assert hdr and "humanize_lexicon" in hdr and "4 of 10 pairs" in hdr


def test_three_of_ten_is_quiet(tmp_path):
    assert wr.action_required({"humanize": _fixture(tmp_path, 3, 10)}) is None
