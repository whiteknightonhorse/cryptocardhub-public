"""CCH-HUM-09: backfill --write. Tmp corpus, stubbed model, tmp ledger, no network."""
import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("humanize_backfill_w", ROOT / "scripts" / "humanize-backfill.py")
hb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hb)

FROZEN = "uphold-card-review"   # literal member of pseo-generate FROZEN_SLUGS
BODY = " ".join(f"This robust card {i} is really a game-changer — and the fee is low." for i in range(12))


def _page(d: Path, slug: str, body: str = BODY, model="claude-haiku-5-5"):
    (d / f"{slug}.md").write_text(
        f'---\ntitle: "T — keep"\nh1: "H1 — keep"\ndescription: "D — keep"\nbucket: "faq"\nslug: "{slug}"\n'
        f'supporting_keywords: ["kw one", "kw two"]\nintro_paragraph: "Plain intro."\n'
        f'generated_at: "2026-01-01T00:00:00+00:00"\nmodel: "{model}"\n---\n\n{body}\n', encoding="utf-8")


def stub(system, user, model):
    """Clean copy-edit: replace every numbered sentence with a flag-free one of similar length."""
    out = {}
    for line in user.splitlines():
        m = re.match(r"(\d+) \[", line)
        if m:
            out[m.group(1)] = f"Plan{m.group(1)} costs little and the monthly fee is low."
    return json.dumps(out), {}, 0.01


@pytest.fixture
def env(tmp_path, monkeypatch):
    c = tmp_path / "c"
    c.mkdir()
    monkeypatch.setattr(hb, "LEDGER", tmp_path / "backfill.jsonl")
    monkeypatch.setattr(hb, "CALL", stub)
    sent = []
    monkeypatch.setattr(hb.telegram_lib, "alert", lambda t, **k: sent.append(t) or True)
    monkeypatch.setattr(hb.money_gate, "unbacked", lambda *a, **k: [])
    return c, tmp_path, sent


def run(c, *extra):
    return hb.main(["--write", "--corpus", str(c), *extra])


def test_literals():
    assert hb.DAILY_CAP == 10


def test_cap_honoured_and_rows(env):
    c, tmp, sent = env
    for i in range(11):
        _page(c, f"page-{i:02d}")
    assert run(c, "--expect", "10") == 0
    rows = [json.loads(l) for l in hb.LEDGER.read_text().splitlines()]
    assert len(rows) == 10
    assert all(r["refused_reason"] is None and r["score_after"] < r["score_before"] for r in rows)
    assert set(rows[0]) >= {"ts", "slug", "score_before", "score_after", "accepted", "rejected",
                            "refused_reason", "cost_usd"}
    assert sum("game-changer" not in p.read_text() for p in c.glob("*.md")) == 10
    assert len(sent) == 1 and "written=10" in sent[0]


def test_protected_fields_survive(env):
    c, _, _ = env
    _page(c, "page-a")
    assert run(c, "--expect", "1") == 0
    fm = yaml.safe_load((c / "page-a.md").read_text().split("---\n")[1])
    assert (fm["title"], fm["h1"], fm["description"]) == ("T — keep", "H1 — keep", "D — keep")
    assert fm["supporting_keywords"] == ["kw one", "kw two"]
    assert fm["generated_at"] != "2026-01-01T00:00:00+00:00"


def test_expect_mismatch_refuses_with_zero_writes(env):
    c, tmp, sent = env
    for i in range(3):
        _page(c, f"page-{i}")
    before = {p.name: p.read_bytes() for p in c.glob("*.md")}
    assert run(c, "--expect", "10") == 3
    assert {p.name: p.read_bytes() for p in c.glob("*.md")} == before
    assert not hb.LEDGER.exists() and sent == []


def test_dry_run_calls_nothing(env, monkeypatch):
    c, _, sent = env
    _page(c, "page-a")
    monkeypatch.setattr(hb, "CALL", lambda *a: pytest.fail("model called in dry-run"))
    before = (c / "page-a.md").read_bytes()
    assert run(c, "--expect", "1", "--dry-run") == 0
    assert (c / "page-a.md").read_bytes() == before and not hb.LEDGER.exists() and sent == []


def test_frozen_and_sonnet_skipped(env):
    c, _, _ = env
    _page(c, FROZEN)
    _page(c, "hand-written", model="claude-sonnet-5")
    _page(c, "page-a")
    before = {n: (c / f"{n}.md").read_bytes() for n in (FROZEN, "hand-written")}
    assert run(c, "--expect", "1") == 0
    assert {n: (c / f"{n}.md").read_bytes() for n in before} == before
    assert [json.loads(l)["slug"] for l in hb.LEDGER.read_text().splitlines()] == ["page-a"]


def test_second_run_skips_ledgered_page(env, monkeypatch):
    c, _, _ = env
    _page(c, "page-a")
    assert run(c, "--expect", "1") == 0
    monkeypatch.setattr(hb, "CALL", lambda *a: pytest.fail("model called twice for one page"))
    assert run(c, "--expect", "0") == 0
    assert len(hb.LEDGER.read_text().splitlines()) == 1


def test_g36_unbacked_refused_file_untouched(env, monkeypatch):
    c, _, _ = env
    _page(c, "page-a")
    before = (c / "page-a.md").read_bytes()
    monkeypatch.setattr(hb.money_gate, "unbacked", lambda *a, **k: ["$99"])
    assert run(c, "--expect", "1") == 0
    assert (c / "page-a.md").read_bytes() == before
    row = json.loads(hb.LEDGER.read_text())
    assert row["refused_reason"].startswith("G36:unbacked")


def test_emit_frontmatter_round_trip_real_pages():
    pg = hb.hg._load_pg()
    files = [ROOT / "seo-site/src/content/pseo" / n for n in
             ("best-crypto-card-2026.md",  # unquoted YAML timestamp
              "binance-card-review.md", "crypto-card-no-kyc.md")]
    for f in files:
        if not f.exists():
            pytest.skip(f"corpus page missing: {f.name}")
        d = yaml.safe_load(hb._page_updates().split_frontmatter(f.read_text(encoding="utf-8"))[0].strip()[3:-3])
        assert yaml.safe_load(pg.emit_frontmatter(d)[3:-3]) == d
