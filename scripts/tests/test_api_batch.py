"""CCH-BATCH-01: batch routing chosen, subscription fallback kept, collector reaches the real output function."""
import importlib.util, json, stat, sys
from pathlib import Path

SC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SC))
import api_batch as ab  # noqa: E402

FAKE = '''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]; d = os.environ["API_BATCH_DIR"]
open(d + "/calls", "a").write(a[0] + "\\n")
if a[0] in ("estimate", "submit") and os.environ.get("FAKE_REFUSE"):
    print("api-batch refused: batch estimate $9.00 over the $5 cap", file=sys.stderr); sys.exit(75)
if a[0] == "submit":
    open(d + "/last.jsonl", "w").write(open(a[a.index("--jobs") + 1]).read()); print("msgbatch_X")
elif a[0] == "estimate": print("estimate $0.0001 for 1 requests")
elif a[0] == "poll": print("ended")
elif a[0] == "collect":
    cid = json.loads(open(d + "/last.jsonl").readline())["custom_id"]
    open(a[a.index("--out") + 1], "w").write(json.dumps({"custom_id": cid, "result": {"type": "succeeded",
        "message": {"content": [{"type": "text", "text": os.environ.get("FAKE_TEXT", "1")}]}}}) + "\\n")
'''


def setup(tmp_path, monkeypatch, jobs="judge page-updates intel"):
    b = tmp_path / "api-batch-fake"
    b.write_text(FAKE)
    b.chmod(b.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(ab, "BIN", str(b))
    monkeypatch.setenv("API_BATCH_DIR", str(tmp_path))
    monkeypatch.setattr(ab, "_refused", False)
    (tmp_path / "jobs").write_text(jobs)


def test_submit_state_and_no_prompt_text(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    assert ab.submit("judge", "aud-1", {"slug": "s"}, "SYS", "SECRET USER TEXT", 16, "claude-haiku-5-5")
    assert (tmp_path / "calls").read_text().split() == ["estimate", "submit"]      # estimate first
    req = json.loads((tmp_path / "last.jsonl").read_text())
    assert req["custom_id"] == "aud-1" and req["params"]["max_tokens"] == 16
    assert "SECRET" not in (tmp_path / "judge" / "state.json").read_text()
    assert not list((tmp_path / "judge").glob("*.jsonl"))                        # request file not kept
    assert ab.submit("judge", "aud-1", {}, "S", "U", 16, "m")                     # pending: never twice
    assert (tmp_path / "calls").read_text().split() == ["estimate", "submit"]


def test_refusal_falls_back_once(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_REFUSE", "1")
    assert ab.submit("judge", "aud-1", {}, "S", "U", 16, "m") is False
    assert "batch refused: " in capsys.readouterr().err
    assert ab.enabled("judge") is False                                           # no second attempt this run
    assert "submit" not in (tmp_path / "calls").read_text().split()               # nothing submitted


def test_off_without_jobs_file(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, jobs="")
    assert ab.enabled("judge") is False


def test_judge_routing_and_collect_writes_real_row(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_TEXT", "2")
    import humanize_gate as hg
    monkeypatch.setattr(hg, "audit_sample", lambda s: True)
    hg._load_pg()                       # load pseo-generate from the real ROOT first
    monkeypatch.setattr(hg, "ROOT", tmp_path)
    p = {"intro_paragraph": "a"}
    assert hg.audit_pair("some-slug", p, p) is None                               # batch path chosen, no CLI call
    assert (tmp_path / "judge" / "state.json").exists()
    assert hg.collect_batches() == 1
    row = json.loads((tmp_path / "logs" / "humanize" / "audit.jsonl").read_text())
    assert row["slug"] == "some-slug" and row["verdict"] in ("original", "rewrite")
    assert json.loads((tmp_path / "judge" / "state.json").read_text())["batches"] == {}


def test_intel_routes_and_uses_collected_only_for_same_diff(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    spec = importlib.util.spec_from_file_location("intel_ingest_t", SC / "intel-ingest.py")
    ii = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ii)
    src = {"id": "src1", "url": "https://x"}

    def no_cli(*a, **k):
        raise AssertionError("subscription used")
    monkeypatch.setattr(ii, "call_claude_cli", no_cli)
    try:
        ii.extract_facts(src, "diff-A")
        assert False
    except ab.Deferred:
        pass
    item = ab.cid("intel", "src1")
    ii._COLLECTED[item] = (ii.sha256("diff-A"), "[]")
    assert ii.extract_facts(src, "diff-A") == []
    monkeypatch.setattr(ab, "_refused", True)                                     # refusal: subscription path
    ii._COLLECTED[item] = (ii.sha256("old"), "[]")
    monkeypatch.setattr(ii, "call_claude_cli", lambda *a, **k: ("[1]", {}, 0.0))
    assert ii.extract_facts(src, "diff-B") == [1]


def test_page_updates_routes_and_collector_applies(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    spec = importlib.util.spec_from_file_location("page_updates_t", SC / "page-updates.py")
    pu = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pu)

    def no_cli(**k):
        raise AssertionError("subscription used")
    monkeypatch.setattr(pu.pg, "call_claude_cli", no_cli)
    monkeypatch.setattr(pu, "load_facts", lambda: "")
    try:
        pu.write_update_section("q", "body")
        assert False
    except ab.Deferred:
        pass
    seen = {}
    monkeypatch.setattr(pu, "apply_updates", lambda dry_run, writer: seen.update(t=writer("q", "body")))
    monkeypatch.setenv("FAKE_TEXT", "### New\ntext")
    assert pu.collect_batches() == 1 and seen["t"] == "### New\ntext"
