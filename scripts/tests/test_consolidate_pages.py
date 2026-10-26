"""CCH-SEO-09: consolidation law, in a tmp repo fixture. Expected values are literals."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
spec = importlib.util.spec_from_file_location("consolidate_pages", ROOT / "scripts" / "consolidate-pages.py")
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)

ROWS = {"/learn/a-old/": "/learn/a-new/", "/faq/b-old/": "/faq/b-new/"}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    content, dist = tmp_path / "content", tmp_path / "dist"
    content.mkdir()
    for lo, ke in ROWS.items():
        for p in (lo, ke):
            (dist / p.strip("/")).mkdir(parents=True)
            (dist / p.strip("/") / "index.html").write_text("x")
        (content / (lo.strip("/").split("/")[1] + ".md")).write_text("loser")
    red = tmp_path / "redirects.map"
    red.write_text("# header\n/x/y/\t/x/z/\n")
    ledger = tmp_path / "faq-cohorts.json"
    ledger.write_text(json.dumps({"cohorts": [{"items": [
        {"slug": "b-old", "published": {"path": "seo-site/src/content/pseo/b-old.md"}},
        {"slug": "other", "published": {"path": "seo-site/src/content/pseo/other.md"}}]}]}))
    tsv = tmp_path / "c.tsv"
    tsv.write_text("".join(f"{lo}\t{ke}\t2026-10-20\n" for lo, ke in ROWS.items()))
    pg = cp._pg()
    for name, val in dict(CONTENT=content, DIST=dist, REDIRECTS=red, BACKUP=tmp_path / "bk").items():
        monkeypatch.setattr(cp, name, val)
    monkeypatch.setattr(pg, "FAQ_LEDGER", ledger)
    monkeypatch.setattr(cp, "_pg", lambda: pg)
    return dict(content=content, red=red, ledger=ledger, tsv=tsv, bk=tmp_path / "bk")


def test_two_rows_apply(repo):
    assert cp.main(["--expect", "2", "--tsv", str(repo["tsv"])]) == 0
    assert sorted(p.name for p in repo["content"].iterdir()) == []
    assert sorted(p.name for p in repo["bk"].iterdir()) == ["a-old.md", "b-old.md"]
    assert repo["red"].read_text().splitlines()[-2:] == [
        "/learn/a-old/\t/learn/a-new/", "/faq/b-old/\t/faq/b-new/"]
    items = json.loads(repo["ledger"].read_text())["cohorts"][0]["items"]
    assert [i.get("consolidated_into") for i in items] == ["/faq/b-new/", None]


def test_expect_mismatch_refuses_and_writes_nothing(repo):
    before = repo["red"].read_text()
    with pytest.raises(SystemExit, match="--expect 3"):
        cp.main(["--expect", "3", "--tsv", str(repo["tsv"])])
    assert repo["red"].read_text() == before
    assert len(list(repo["content"].iterdir())) == 2


def test_keeper_that_is_a_loser_elsewhere_refuses(repo):
    repo["tsv"].write_text("/learn/a-old/\t/faq/b-old/\t2026-10-20\n/faq/b-old/\t/faq/b-new/\t2026-10-20\n")
    with pytest.raises(SystemExit, match="is a loser in another row"):
        cp.main(["--expect", "2", "--tsv", str(repo["tsv"])])
    assert len(list(repo["content"].iterdir())) == 2


def test_loser_that_is_a_redirect_target_refuses(repo):
    repo["red"].write_text("/q/r/\t/learn/a-old/\n")
    with pytest.raises(SystemExit, match="301 -> 301"):
        cp.main(["--expect", "2", "--tsv", str(repo["tsv"])])


def test_frozen_loser_refuses(repo, monkeypatch):
    pg = cp._pg()
    monkeypatch.setattr(pg, "FROZEN_SLUGS", frozenset({"a-old"}))
    with pytest.raises(pg.FrozenPageError):
        cp.main(["--expect", "2", "--tsv", str(repo["tsv"])])
    assert len(list(repo["content"].iterdir())) == 2


def test_dry_run_writes_nothing(repo):
    before = repo["red"].read_text()
    assert cp.main(["--expect", "2", "--dry-run", "--tsv", str(repo["tsv"])]) == 0
    assert repo["red"].read_text() == before and len(list(repo["content"].iterdir())) == 2
