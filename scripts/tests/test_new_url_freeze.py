"""DEFY-SEO-1110 B8 / CCH-SEO-30: while the freeze is on, no generator creates
a new URL, opens a FAQ cohort, or adds a bank row. Expected values are literals.
conftest's autouse fixture skips this module, so the flag is the committed one (True)."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
import flags  # noqa: E402


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / fname)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pg = _load("pseo_generate", "pseo-generate.py")
fc = _load("faq_children", "faq-children.py")
news = _load("news_ingest", "news-ingest.py")
digest = _load("market_digest", "market-digest.py")
promo = _load("promote_candidates", "promote-candidates.py")
chlog = _load("build_changelog", "build-changelog.py")
wr = _load("weekly_report", "weekly-report.py")


def _boom(*a, **k):
    raise AssertionError("must not be reached while new URLs are frozen")


def _skip_row(out: str) -> dict:
    rows = [json.loads(l) for l in out.splitlines() if l.startswith("{")]
    return next(r for r in rows if r.get("reason") == "new_urls_frozen")


def _assert_row(out: str):
    r = _skip_row(out)
    assert r == {"event": "skip", "reason": "new_urls_frozen", "since": "2026-11-11",
                 "review": "2027-01-10", "by": "DEFY-SEO-1110 B8, operator 2026-11-11"}


def test_committed_literals():
    src = (SCRIPTS / "flags.py").read_text(encoding="utf-8")
    assert re.search(r"^NEW_URLS_FROZEN = True$", src, re.M)
    assert re.search(r'^NEW_URL_FREEZE_REVIEW = "2027-01-10"$', src, re.M)
    assert flags.new_urls_frozen() is True


# --- a. pseo-generate -------------------------------------------------------

@pytest.mark.parametrize("argv", [["--faq-children"], ["--only-new"],
                                  ["--faq-children", "--probabilistic"]])
def test_pseo_generate_entry_skips(argv, monkeypatch, capsys, tmp_path):
    ledger = fc.LEDGER
    before = ledger.read_bytes() if ledger.exists() else None
    monkeypatch.setattr(pg, "CONTENT_DIR", tmp_path)
    monkeypatch.setattr(pg, "call_claude_cli", _boom)
    monkeypatch.setattr(pg, "probabilistic_gate", _boom)
    monkeypatch.setattr(pg, "build_queue", _boom)
    monkeypatch.setattr(pg, "faq_child_queue_with_reopen", _boom)
    assert pg.main(argv) == 0
    _assert_row(capsys.readouterr().out)
    assert list(tmp_path.iterdir()) == []
    assert (ledger.read_bytes() if ledger.exists() else None) == before


def test_pseo_generate_loop_refuses_new_file(monkeypatch, capsys, tmp_path):
    cluster = SimpleNamespace(primary=SimpleNamespace(keyword="brand new keyword here"),
                              url_segment="x", pattern="p")
    monkeypatch.setattr(pg, "CONTENT_DIR", tmp_path)
    monkeypatch.setattr(pg, "build_queue", lambda *a, **k: [cluster])
    monkeypatch.setattr(pg, "build_cached_system_prompt", lambda: "sys")
    monkeypatch.setattr(pg, "CLAUDE_BIN", sys.executable)
    monkeypatch.setattr(pg, "generate_page", _boom)
    monkeypatch.setattr(pg, "call_claude_cli", _boom)
    monkeypatch.setattr(pg, "no_progress_guard", SimpleNamespace(check=lambda *a, **k: None))
    assert pg.main(["--upgrade-thin", "--limit", "1"]) == 0
    _assert_row(capsys.readouterr().out)
    assert list(tmp_path.iterdir()) == []


def test_write_page_new_path_raises_existing_path_writes(monkeypatch, tmp_path):
    class Reached(Exception):
        pass

    def _past_guard(*a, **k):
        raise Reached

    monkeypatch.setattr(pg, "CONTENT_DIR", tmp_path)
    monkeypatch.setattr(pg, "localize_images", _past_guard)
    res = SimpleNamespace(payload={}, cluster=SimpleNamespace(
        primary=SimpleNamespace(keyword="freeze probe keyword")))
    with pytest.raises(pg.NewUrlFrozen):
        pg.write_page(res, [])
    (tmp_path / "freeze-probe-keyword.md").write_text("old", encoding="utf-8")
    with pytest.raises(Reached):  # guard passed; an existing target is written as before
        pg.write_page(res, [])


# --- b. faq-children --------------------------------------------------------

def test_open_next_cohort_refused_even_forced(monkeypatch, tmp_path):
    led = tmp_path / "faq-cohorts.json"
    monkeypatch.setattr(fc, "LEDGER", led)
    monkeypatch.setattr(fc, "select", _boom)
    assert fc.open_next_cohort(force=True) == {"event": "cohort_refused",
                                               "reason": "new_urls_frozen"}
    assert not led.exists()


# --- c. news-ingest / d. market-digest / e. promote / sixth: changelog -------

def test_news_ingest_skips_before_any_fetch(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["news-ingest.py", "--dry-run"])
    monkeypatch.setattr(news, "_fetch_rss", _boom)
    monkeypatch.setattr(news, "_call_claude", _boom)
    assert news.main() == 0
    _assert_row(capsys.readouterr().out)


def test_market_digest_skips(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["market-digest.py", "--write"])
    monkeypatch.setattr(digest, "write_page", _boom)
    assert digest.main() == 0
    _assert_row(capsys.readouterr().out)


def test_promote_candidates_promotes_nothing(monkeypatch, capsys, tmp_path):
    led = tmp_path / "keyword-promotions.jsonl"
    monkeypatch.setattr(sys, "argv", ["promote-candidates.py"])
    monkeypatch.setattr(promo, "LEDGER", led)
    monkeypatch.setattr(promo, "BANK", tmp_path / "no-bank.md")  # a read would raise
    assert promo.main() == 0
    _assert_row(capsys.readouterr().out)
    rows = [json.loads(l) for l in led.read_text().splitlines()]
    assert [(r["event"], r["reason"]) for r in rows] == [("nothing_eligible", "new_urls_frozen")]


def test_changelog_sixth_producer_skips_when_page_absent(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sys, "argv", ["build-changelog.py", "--write"])
    monkeypatch.setattr(chlog, "CONTENT", tmp_path)
    monkeypatch.setattr(chlog, "write_page", _boom)
    assert chlog.main() == 0
    _assert_row(capsys.readouterr().out)


# --- weekly-report ----------------------------------------------------------

def _report(today: date):
    return {
        "since": "2026-11-09", "until": "2026-11-16",
        "human_sessions": 1, "raw_sessions": 1, "bot_direct": 0,
        "by_source": [], "clicks_by_source": {}, "clicks_by_partner": {},
        "utm_content": {}, "videos_7d": 0,
        "median_views_7d": wr.Missing("median_views_7d", "no videos in window"),
        "retention_7d": wr.Missing("retention_7d", "no retention samples"),
        "pseo": {"pages_7d": 0, "idle_ticks": 0, "pause_ticks": 0, "queue": None},
        "freeze": wr.freeze_phase(today, 7),
    }


def _transmitted(report, monkeypatch) -> str:
    sent = []
    monkeypatch.setenv("TG_BOT_TOKEN", "x")
    monkeypatch.setenv("TG_CHAT_ID", "y")
    monkeypatch.setattr(wr.urllib.request, "urlopen", lambda url, data, timeout: sent.append(data)
                        or SimpleNamespace(read=lambda: b""))
    wr.send(report, wr.render(report))
    import urllib.parse
    return "\n".join(urllib.parse.parse_qs(d.decode())["text"][0] for d in sent)


FREEZE_LINE = ("FREEZE: new URLs frozen since 2026-11-11 (DEFY-SEO-1110 B8), "
               "review 2027-01-10, 7 ticks skipped this week")


def test_weekly_report_before_review_has_line_and_no_action(monkeypatch):
    text = _transmitted(_report(date(2026, 11, 16)), monkeypatch)
    assert FREEZE_LINE in text
    assert "new_url_freeze" not in text


def test_weekly_report_after_review_has_action_required(monkeypatch):
    text = _transmitted(_report(date(2027, 1, 11)), monkeypatch)
    assert FREEZE_LINE in text
    head = text.split("\n\n")[0]
    assert head.startswith("ACTION REQUIRED") and "new_url_freeze" in head
    assert "review due since 2027-01-10: lift or extend - operator decision (DEFY-SEO-1110 B8)" in head


def test_collect_pseo_counts_freeze_skip_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ROOT", tmp_path)
    d = tmp_path / "logs" / "pseo"
    d.mkdir(parents=True)
    row = json.dumps(flags.freeze_skip_row())
    (d / "2026-11-12.jsonl").write_text(row + "\n" + row + "\n", encoding="utf-8")
    (d / "faq-2026-11-12.jsonl").write_text(row + "\n", encoding="utf-8")
    out = wr._collect_pseo("2026-11-09", "2026-11-16")
    assert out["pseo"]["freeze_ticks"] == 3


def test_weekly_report_flag_off_no_line_no_action(monkeypatch):
    monkeypatch.setattr(flags, "NEW_URLS_FROZEN", False)
    rep = _report(date(2027, 2, 1))
    assert rep["freeze"] is None
    text = _transmitted(rep, monkeypatch)
    assert "FREEZE:" not in text
    assert "new_url_freeze" not in text


def test_weekly_report_freeze_line_survives_pseo_failure(monkeypatch):
    rep = _report(date(2026, 11, 16))
    rep["pseo"] = wr.Missing("pseo", "collector failed")
    assert FREEZE_LINE in _transmitted(rep, monkeypatch)
