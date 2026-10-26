"""CCH-HUM-08: report order, frozen flag, --write refusal. Tmp corpus, no model."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("humanize_backfill", ROOT / "scripts" / "humanize-backfill.py")
hb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hb)

FROZEN = "uphold-card-review"   # literal member of pseo-generate FROZEN_SLUGS

CLEAN = "The card charges a fee of five dollars per month. Funding takes one day."
TELLS = ("Whether you're new or not, it's worth noting that this robust card is "
         "really a game-changer — and the landscape — is seamless. ") * 3


def _page(d: Path, slug: str, body: str, model="claude-haiku-5-5"):
    (d / f"{slug}.md").write_text(
        f'---\ntitle: "T"\nbucket: "faq"\nslug: "{slug}"\ngenerated_at: "2026-01-01T00:00:00+00:00"\n'
        f'model: "{model}"\n---\n\n{body}\n', encoding="utf-8")


def test_report_order_and_frozen(tmp_path):
    c = tmp_path / "c"
    c.mkdir()
    _page(c, "clean-page", CLEAN)
    _page(c, "tell-page", TELLS)
    _page(c, FROZEN, TELLS + TELLS)
    out = tmp_path / "r.json"
    assert hb.main(["--report", "--corpus", str(c), "--out", str(out)]) == 0
    rep = json.loads(out.read_text())
    slugs = [r["slug"] for r in rep["pages"]]
    assert slugs[-1] == "clean-page"
    assert set(slugs[:2]) == {"tell-page", FROZEN}
    by = {r["slug"]: r for r in rep["pages"]}
    assert by[FROZEN]["frozen"] is True
    assert by["tell-page"]["frozen"] is False and by["clean-page"]["frozen"] is False
    assert by["clean-page"]["score"] == 0.0 and by["tell-page"]["score"] > 4.0
    assert rep["header"]["pages"] == 3 and rep["header"]["above_threshold"] == 2
    assert rep["header"]["threshold"] == 4.0


def test_write_requires_expect(tmp_path):
    import pytest
    with pytest.raises(SystemExit) as e:
        hb.main(["--write", "--corpus", str(tmp_path)])
    assert e.value.code == 2
