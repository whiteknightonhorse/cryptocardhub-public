"""CCH-SEO-11 — a retired URL stays retired: announced once to IndexNow, never reprinted."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import slug_lib  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


inx = _load("indexnow_s11", "scripts/indexnow-ping.py")
pg = _load("pseo_generate_s11", "scripts/pseo-generate.py")
fc = _load("faq_children_s11", "scripts/faq-children.py")


def _map(tmp_path, monkeypatch, rows):
    m = tmp_path / "redirects.map"
    m.write_text("# c\n" + "".join(f"{a}\t{b}\n" for a, b in rows))
    monkeypatch.setattr(slug_lib, "REDIRECTS_MAP", m)
    monkeypatch.setattr(inx, "REDIRECTS", m)
    return m


def test_indexnow_redirects_first_run_then_only_new(tmp_path, monkeypatch):
    m = _map(tmp_path, monkeypatch, [("/faq/a-b/", "/faq/a/"), ("/faq/c-d/", "410")])
    sent = []
    monkeypatch.setattr(inx, "CONTENT", tmp_path / "none")
    monkeypatch.setattr(inx, "ROOT", tmp_path)
    monkeypatch.setattr(inx, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(inx, "_key", lambda: "k" * 32)
    monkeypatch.setattr(inx, "submit", lambda urls, key: sent.append(urls) or 200)
    monkeypatch.setattr(sys, "argv", ["x"])
    # state exists but has no "__redirects__"
    (tmp_path / "state.json").write_text(json.dumps({}))
    assert inx.main() == 0
    assert sent == []
    assert json.loads((tmp_path / "state.json").read_text())["__redirects__"] == {
        "/faq/a-b/": "/faq/a/", "/faq/c-d/": "410"}
    m.write_text(m.read_text() + "/faq/e-f/\t/faq/e/\n")
    assert inx.main() == 0
    assert sent == [["https://defycard.com/faq/e-f/"]]
    assert inx.main() == 0 and len(sent) == 1


def test_changed_urls_for_content_unchanged():
    assert inx.changed_urls({"u": "1", "__redirects__": {}}, {"u": "1", "__redirects__": {}}) == []
    assert inx.changed_urls({"u": "2"}, {"u": "1"}) == ["u"]


def test_build_queue_skips_retired_slug(tmp_path, monkeypatch):
    _map(tmp_path, monkeypatch, [("/faq/x-y/", "/faq/x/")])
    monkeypatch.setattr(pg, "existing_slugs", lambda: set())
    mk = lambda kw: pg.Cluster(pg.KeywordRow("A1", "A", "Hub", kw), [], "P", "faq")
    out = pg.build_queue([mk("x y"), mk("z w")], only_new=True, upgrade_thin=False)
    assert [c.primary.keyword for c in out] == ["z w"]


def test_faq_reject_reason_retired_slug(tmp_path, monkeypatch):
    _map(tmp_path, monkeypatch, [("/faq/can-i-use-the-card-in-spain/", "/faq/x/")])
    assert fc.reject_reason("Can I use the card in Spain?") == "retired_slug"
    assert fc.reject_reason("Can I use the card in Italy?") != "retired_slug"


def test_faq_child_queue_skips_retired(tmp_path, monkeypatch):
    _map(tmp_path, monkeypatch, [("/faq/x-y/", "/faq/x/")])
    led = tmp_path / "led.json"
    item = lambda s: {"slug": s, "question": s.replace("-", " "), "parent": "/hub/p/"}
    led.write_text(json.dumps({"cohorts": [{"items": [item("x-y"), item("z-w")]}]}))
    monkeypatch.setattr(pg, "FAQ_LEDGER", led)
    assert [c.faq_slug for c in pg.faq_child_queue()] == ["z-w"]
