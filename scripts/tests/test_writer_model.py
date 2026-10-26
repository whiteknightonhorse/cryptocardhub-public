"""The writer model is ONE constant (pseo-generate.py); news-ingest reuses it."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_writer_model_is_one_haiku_5_5_constant(monkeypatch):
    monkeypatch.delenv("WRITER_MODEL", raising=False)
    pg = _load("pseo_generate", "scripts/pseo-generate.py")
    ni = _load("news_ingest_wm", "scripts/news-ingest.py")
    assert pg.DEFAULT_MODEL == "claude-haiku-5-5"
    assert ni.DEFAULT_MODEL is sys.modules["pseo_generate"].DEFAULT_MODEL
    assert "haiku" in pg.DEFAULT_MODEL


def test_video_write_script_model_is_haiku_5_5_literal(monkeypatch):
    monkeypatch.delenv("WRITER_MODEL", raising=False)
    sys.path.insert(0, str(ROOT / "video-generator"))
    from pipeline import write_script
    assert write_script.WRITER_MODEL == "claude-haiku-5-5"
