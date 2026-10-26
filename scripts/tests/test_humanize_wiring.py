"""CCH-HUM-04 (G37 wiring, Y53/G14 law): run the REAL pg._main with heavy steps stubbed
and read what arrives at write_page. humanize() being correct and unused is the bug shape."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
_SPEC = importlib.util.spec_from_file_location("pseo_generate_wiring", ROOT / "scripts" / "pseo-generate.py")
pg = importlib.util.module_from_spec(_SPEC)
sys.modules["pseo_generate_wiring"] = pg
_SPEC.loader.exec_module(pg)
import humanize_gate as hg  # noqa: E402

TELLS = ["Fees are low — billed yearly.", "Limits are set — by tier.", "Support is open — all week."]
CLEAN = "Fees are low, billed yearly."


def _setup(monkeypatch, tmp_path, call_reply):
    cluster = pg.Cluster(pg.KeywordRow("B1", "B", "Comparison", "wiring test card"), [], "P-Comp", "compare")
    payload = {"title": "T", "description": "D", "body_markdown": " ".join(TELLS)}
    monkeypatch.setattr(pg, "pseo_enabled", lambda: True)
    monkeypatch.setattr(pg, "FLEET_PAUSED_UNTIL_PATH", tmp_path / "no-pause")
    monkeypatch.setattr(pg, "LOG_DIR", tmp_path / "pseo")
    monkeypatch.setattr(pg, "FAQ_LEDGER", tmp_path / "faq-cohorts.json")
    monkeypatch.setattr(pg, "build_queue", lambda *a, **k: [cluster])
    monkeypatch.setattr(pg, "would_overwrite", lambda c: None)
    monkeypatch.setattr(pg, "build_cached_system_prompt", lambda: "sys")
    monkeypatch.setattr(pg, "generate_page",
                        lambda s, c, m: pg.GenerationResult(c, payload, {}, 0.0, 0.0))
    monkeypatch.setattr(pg, "fetch_images_for_article", lambda p: [])
    monkeypatch.setattr(pg, "no_progress_guard",
                        type("_G", (), {"check": staticmethod(lambda *a, **k: False)})())
    monkeypatch.setattr(pg.telegram_lib, "alert", lambda *a, **k: True)
    hpg = hg._load_pg()   # humanize() calls ITS OWN copy of pseo-generate
    monkeypatch.setattr(hpg, "call_claude_cli", call_reply)
    wrote = []
    monkeypatch.setattr(pg, "write_page", lambda r, imgs: wrote.append(r.payload) or tmp_path / "x.md")
    dec = []
    monkeypatch.setattr(pg, "_decrement_daily_count", lambda: dec.append(1))
    return wrote, dec


def test_write_page_receives_rewritten_payload(monkeypatch, tmp_path, capsys):
    def reply(system, user, model=None):
        out = {m.group(1): CLEAN if "Fees" in m.group(2) else m.group(2).replace(" — ", ", ")
               for m in (re.match(r"(\d+) \[[^\]]*\] (.*)$", l) for l in user.split("\n"))}
        return json.dumps(out), {}, 0.01
    wrote, _ = _setup(monkeypatch, tmp_path, reply)
    assert pg._main(["--limit", "1"]) == 0
    assert len(wrote) == 1
    assert "Fees are low, billed yearly." in wrote[0]["body_markdown"]
    assert TELLS[0] not in wrote[0]["body_markdown"]
    (hfile,) = (tmp_path / "humanize").glob("*.jsonl")
    rows = [json.loads(l) for l in hfile.read_text().splitlines()]
    assert rows[0]["event"] == "humanize" and rows[0]["rewritten"] is True
    assert '"event": "humanize"' in capsys.readouterr().out


def test_garbage_reply_refuses_g37_and_returns_slot(monkeypatch, tmp_path, capsys):
    wrote, dec = _setup(monkeypatch, tmp_path, lambda *a, **k: ("not json", {}, 0.0))
    # probabilistic mode: stub the flock gate, the slot is "claimed" by _main itself
    monkeypatch.setattr(pg, "probabilistic_gate", lambda daily_cap: (True, "test"))
    pg._main(["--probabilistic"])
    out = capsys.readouterr().out
    assert wrote == []
    assert any(json.loads(l).get("gate") == "G37" for l in out.splitlines() if l.startswith("{") and '"refused"' in l)
    assert dec.count(1) == 1


def test_blind_audit_runs_after_write_and_not_on_refusal(monkeypatch, tmp_path):
    """CCH-HUM-07: audit row appears only for a written, rewritten page."""
    def reply(system, user, model=None):
        if system == "You are a blind judge.":
            return "tie", {}, 0.001
        out = {m.group(1): m.group(2).replace(" — ", ", ")
               for m in (re.match(r"(\d+) \[[^\]]*\] (.*)$", l) for l in user.split("\n"))}
        return json.dumps(out), {}, 0.01
    monkeypatch.setattr(hg, "audit_sample", lambda slug: True)
    wrote, _ = _setup(monkeypatch, tmp_path, reply)
    assert pg._main(["--limit", "1"]) == 0 and len(wrote) == 1
    (row,) = [json.loads(l) for l in (tmp_path / "humanize" / "audit.jsonl").read_text().splitlines()]
    assert row["verdict"] == "tie"
