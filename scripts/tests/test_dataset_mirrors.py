"""Gate for CCH-SEO-35 (dataset mirrors). Expected values are literals (Y49)."""
import datetime as dt
import importlib.util
import json
import subprocess
import urllib.parse
from os import environ
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
MIRROR = SCRIPTS / "mirror-dataset.sh"


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ed = _load("export_dataset35", "export-dataset.py")
wr = _load("weekly_report35", "weekly-report.py")
TODAY = dt.date(2026, 11, 11)
BASE = {  # the smallest report render() accepts (same shape as test_weekly_report.py)
    "since": "2026-11-01", "until": "2026-11-08",
    "human_sessions": 100, "raw_sessions": 120, "bot_direct": 20, "by_source": [],
    "clicks_by_source": {}, "clicks_by_partner": {}, "utm_content": {}, "videos_7d": 0,
    "median_views_7d": None, "retention_7d": None,
    "page_updates": {"applied": 0, "judged": 0, "improved": 0},
}


# --- export ----------------------------------------------------------------

def test_readme_front_matter_and_mirrors():
    readme = ed.build(TODAY)["README.md"]
    assert readme.startswith("---\nlicense: cc-by-4.0\n")
    assert "- config_name: cards\n  data_files: cards.csv" in readme
    assert "- config_name: availability\n  data_files: availability.csv" in readme
    assert "## Mirrors" in readme
    assert "https://github.com/defycard-com/crypto-card-fees-data" in readme
    assert "https://huggingface.co/datasets/defycard/crypto-card-fees-data" in readme
    assert "https://www.kaggle.com/datasets/defycard/crypto-card-fees-data" in readme


def test_kaggle_metadata_id_and_license():
    meta = json.loads(ed.build(TODAY)["dataset-metadata.json"])
    assert meta["id"] == "defycard/crypto-card-fees-data"
    assert meta["licenses"] == [{"name": "CC-BY-4.0"}]
    assert 6 <= len(meta["title"]) <= 50 and 20 <= len(meta["subtitle"]) <= 80


def test_check_goes_red_when_metadata_removed(tmp_path):
    assert ed.main(["--write"], tmp_path, TODAY) == 0
    assert ed.main(["--check"], tmp_path, TODAY) == 0
    (tmp_path / "dataset-metadata.json").unlink()
    assert ed.main(["--check"], tmp_path, TODAY) == 1


# --- mirror-dataset.sh -----------------------------------------------------

@pytest.fixture
def tree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "f").write_text("a")
    g = ["git", "-C", str(repo), "-c", "user.name=x", "-c", "user.email=x@x"]
    for c in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "i"]):
        subprocess.run(g + c, check=True)
    return repo


def _run(script, tree, tmp_path, export_cmd, *args, kaggle="true"):
    env = dict(environ, ROOT=str(tmp_path), DATASET_DIR=str(tree),
               LOG_FILE=str(tmp_path / "m.jsonl"), EXPORT_CMD=export_cmd, KAGGLE_BIN=kaggle,
               HOME=str(tmp_path))
    return subprocess.run(["bash", str(script), *args], env=env, capture_output=True, text=True)


def test_stale_tree_dry_run_exits_1_with_cause_and_no_row(tree, tmp_path):
    r = _run(MIRROR, tree, tmp_path, "false", "--dry-run")
    assert r.returncode == 1 and "tree_stale" in r.stderr
    assert not (tmp_path / "m.jsonl").exists()


def test_stale_tree_real_run_writes_a_row_per_target(tree, tmp_path):
    assert _run(MIRROR, tree, tmp_path, "false").returncode == 1
    rows = [json.loads(x) for x in (tmp_path / "m.jsonl").read_text().splitlines()]
    assert [(r["target"], r["ok"], r["cause"]) for r in rows] == [
        ("hf", False, "tree_stale"), ("kaggle", False, "tree_stale")]


def test_dry_run_prints_both_commands_and_writes_no_row(tree, tmp_path):
    r = _run(MIRROR, tree, tmp_path, "true", "--dry-run")
    assert r.returncode == 0 and "DRY[hf]:" in r.stdout and "DRY[kaggle]:" in r.stdout
    assert "export 20" in r.stdout and not (tmp_path / "m.jsonl").exists()


def test_targets_are_independent(tree, tmp_path):
    r = _run(MIRROR, tree, tmp_path, "true")  # no "hf" remote in the temp repo -> hf fails
    assert r.returncode == 1
    rows = {j["target"]: j for j in map(json.loads, (tmp_path / "m.jsonl").read_text().splitlines())}
    assert rows["hf"]["ok"] is False and rows["hf"]["cause"]
    assert rows["kaggle"]["ok"] is True


def test_mutation_skip_check_step_is_caught(tree, tmp_path):
    """Mutation 1: the --check step deleted. The stale-tree gate must go red."""
    src = MIRROR.read_text()
    start = src.index("if ! $EXPORT_CMD")
    end = src.index("\nfi\n", start) + 4
    mutant = tmp_path / "mutant.sh"
    mutant.write_text(src[:start] + src[end:])
    r = _run(mutant, tree, tmp_path, "false", "--dry-run")
    assert not (r.returncode == 1 and "tree_stale" in r.stderr), "gate is blind to a skipped check"


# --- weekly-report ---------------------------------------------------------

def _log(root, *rows):
    d = root / "logs" / "dataset"
    d.mkdir(parents=True)
    (d / "mirror.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def _sent_text(monkeypatch, report, body):
    sent = {}

    class R:
        def read(self):
            return b""
    monkeypatch.setattr(wr.urllib.request, "urlopen",
                        lambda url, data=None, timeout=None: sent.update(data=data) or R())
    monkeypatch.setenv("TG_BOT_TOKEN", "t")
    monkeypatch.setenv("TG_CHAT_ID", "c")
    wr.send(report, body)
    return urllib.parse.parse_qs(sent["data"].decode())["text"][0]


def test_empty_log_and_failed_row_head_the_sent_text(tmp_path, monkeypatch):
    out = wr._collect_dataset_mirrors(tmp_path, TODAY)
    text = _sent_text(monkeypatch, out, "BODY")
    assert text.startswith("ACTION REQUIRED") and "dataset_mirror.hf: no mirror run logged" in text
    _log(tmp_path, {"ts": "2026-11-10T06:55:00Z", "target": "hf", "ok": False, "cause": "Permission denied (publickey)"},
         {"ts": "2026-11-10T06:55:01Z", "target": "kaggle", "ok": True, "cause": ""})
    text = _sent_text(monkeypatch, wr._collect_dataset_mirrors(tmp_path, TODAY), "BODY")
    assert "dataset_mirror.hf: Permission denied (publickey)" in text
    assert "dataset_mirror.kaggle" not in text


def test_stale_ok_is_missing_and_fresh_ok_is_a_date(tmp_path):
    _log(tmp_path, {"ts": "2026-10-20T06:55:00Z", "target": "hf", "ok": True, "cause": ""},
         {"ts": "2026-11-09T06:55:00Z", "target": "kaggle", "ok": True, "cause": ""})
    out = wr._collect_dataset_mirrors(tmp_path, TODAY)["dataset_mirror"]
    assert isinstance(out["hf"], wr.Missing) and "last ok 2026-10-20" in out["hf"].why
    assert out["kaggle"] == {"ok_date": "2026-11-09"}


def test_ok_line_reaches_the_report_text(tmp_path):
    _log(tmp_path, {"ts": "2026-11-09T06:55:00Z", "target": "hf", "ok": True, "cause": ""},
         {"ts": "2026-11-09T06:55:01Z", "target": "kaggle", "ok": True, "cause": ""})
    report = wr._collect_dataset_mirrors(tmp_path, TODAY)
    assert "DATASET MIRRORS: hf 2026-11-09 · kaggle 2026-11-09" in wr.render(BASE | report)
    assert not wr.action_required(report)


def test_collect_wires_mirrors_even_when_ga4_is_down(monkeypatch):
    monkeypatch.setattr(wr, "_ga4_client", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(wr, "_collect_cabinet", lambda: {"cabinet": wr.Missing("cabinet", "x")})
    monkeypatch.setattr(wr, "_collect_dataset_mirrors", lambda: {"dataset_mirror": {"hf": "SENTINEL"}})
    assert wr.collect("2026-11-01", "2026-11-08")["dataset_mirror"] == {"hf": "SENTINEL"}


def test_mutation_line_built_but_not_appended_is_caught(tmp_path):
    """Mutation 2: the DATASET MIRRORS line removed from render(). The ok-line gate must go red."""
    src = (SCRIPTS / "weekly-report.py").read_text()
    head = 'L.append("DATASET MIRRORS: " + " · ".join('
    assert head in src
    mutant = tmp_path / "weekly_mutant.py"
    mutant.write_text(src.replace(head, '_x = (" · ".join(', 1))
    spec = importlib.util.spec_from_file_location("weekly_mutant", mutant)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    _log(tmp_path, {"ts": "2026-11-09T06:55:00Z", "target": "hf", "ok": True, "cause": ""})
    rep = BASE | m._collect_dataset_mirrors(tmp_path, TODAY)
    assert "DATASET MIRRORS:" not in m.render(rep)
