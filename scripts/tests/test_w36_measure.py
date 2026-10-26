"""w36-measure.py self-check — the smallest thing that fails if the classify/
contamination/host-parsing logic breaks. No fixtures beyond a tmp corpus dir.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


w36 = _load("w36_measure_test_target", "scripts/w36-measure.py")


def test_normalize_host():
    assert w36.normalize_host("www.defycard.com") == "defycard.com"
    assert w36.normalize_host("DefyCard.com") == "defycard.com"
    assert w36.normalize_host("aipush.app") == "aipush.app"


def test_parse_line_host_requires_the_field():
    legacy = ('1.2.3.4 - - [02/Sep/2026:10:00:00 +0000] "GET /faq/x/ HTTP/1.1" '
             '200 100 "-" "ChatGPT-User/1.0"')  # no host= — pre-2026-09-01 09:15 shape
    assert w36.parse_line_host(legacy, w36.non_root) is None

    tagged = ('1.2.3.4 - - [02/Sep/2026:10:00:00 +0000] "GET /faq/x/ HTTP/1.1" '
             '200 100 "-" "ChatGPT-User/1.0" host=www.defycard.com')
    rec = w36.parse_line_host(tagged, w36.non_root)
    assert rec is not None
    assert rec["host"] == "defycard.com"
    assert rec["agent"] == "ChatGPT-User"
    assert rec["date"] == "2026-09-02"

    other_site = ('1.2.3.4 - - [02/Sep/2026:10:00:00 +0000] "GET /faq/x/ HTTP/1.1" '
                 '200 100 "-" "ChatGPT-User/1.0" host=apibase.pro')
    rec2 = w36.parse_line_host(other_site, w36.non_root)
    assert rec2["host"] == "apibase.pro"  # parsed, but caller must filter by host


def test_non_root_excludes_the_collision_page():
    assert w36.non_root("/") is False
    assert w36.non_root("/faq/some-question/") is True


def test_classify_plateau():
    values = [12, 20, 30, 15, 25, 18, 22]  # all in [10,45]
    out = w36.classify(values)
    assert out["candidate_branch"] == "plateau_holds_10_45"


def test_classify_descent():
    values = [113, 64, 54, 33, 25, 12, 3]  # trending toward zero, last day tiny
    out = w36.classify(values)
    assert out["candidate_branch"] == "descent_continues_to_zero"


def test_classify_step():
    values = [60, 58, 62, 59, 5, 4, 6]  # sharp, sustained break to a new flat level
    out = w36.classify(values)
    assert out["candidate_branch"] == "step_to_new_flat_level"


def test_contamination_flags_a_salvo_not_ordinary_cadence():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        since = date(2026, 9, 2)
        until = date(2026, 9, 8)
        control_since = since - timedelta(days=7)
        # ordinary cadence in the control week: 5 files touched
        for i in range(5):
            p = d / f"control-{i}.md"
            p.write_text("x")
            ts = (control_since + timedelta(days=i)).isoformat()
            import os
            os.utime(p, (
                __import__("time").mktime(date.fromisoformat(ts).timetuple()),
                __import__("time").mktime(date.fromisoformat(ts).timetuple()),
            ))
        clean = w36.contamination_check(since, until, corpus_dir=d)
        assert clean["status"] == "clean"
        assert clean["window_files_touched"] == 0

        # now salvo: 60 files touched inside the measurement window itself
        import os
        for i in range(60):
            p = d / f"salvo-{i}.md"
            p.write_text("x")
            ts = since + timedelta(days=i % 7)
            os.utime(p, (
                __import__("time").mktime(ts.timetuple()),
                __import__("time").mktime(ts.timetuple()),
            ))
        dirty = w36.contamination_check(since, until, corpus_dir=d)
        assert dirty["status"] == "contaminated"
        assert dirty["window_files_touched"] == 60


if __name__ == "__main__":
    test_normalize_host()
    test_parse_line_host_requires_the_field()
    test_non_root_excludes_the_collision_page()
    test_classify_plateau()
    test_classify_descent()
    test_classify_step()
    test_contamination_flags_a_salvo_not_ordinary_cadence()
    print("test_w36_measure self-check OK")
