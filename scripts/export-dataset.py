#!/usr/bin/env python3
"""export-dataset.py — open dataset (CCH-SEO-14) from what the site already backs.

Rows = facts of config/facts/*.yaml with status in facts_lib.PUBLISHABLE_STATUS
and not past their `expires` date, plus allow-list rows of every
config/availability.yaml card that has a `verified` date (what
/geo/availability-matrix/ renders). Nothing is typed by hand here.

    export-dataset.py --write   # write the tree
    export-dataset.py --check   # exit 1 when the tree differs from a fresh export
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import facts_lib as fl  # noqa: E402

ROOT = fl.ROOT
OUT = Path("/home/cryptocardhub/defycard-data")
FACTS_DIR = ROOT / "config" / "facts"
AVAIL_PATH = ROOT / "config" / "availability.yaml"
AFFIL_PATH = ROOT / "config" / "affiliates.yaml"
PSEO_DIR = ROOT / "seo-site" / "src" / "content" / "pseo"
SITE = "https://defycard.com/"
# Our own affiliate-programme terms are not card data (and are no business of readers).
EXCLUDED_CLASSES = {"affiliate"}

CARD_COLUMNS = ["issuer", "card", "fact_id", "class", "applies_to", "value", "unit", "status",
                "verified_at", "expires", "source_url", "source_quote", "confidence"]
AVAIL_COLUMNS = ["card", "jurisdiction", "kind", "available", "verified", "source_url"]


def _slugify(name: str) -> str:  # same rule as slugifyPartnerName in [slug].astro
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")


def _s(v) -> str:
    return "" if v is None else str(v)


def _names() -> dict[str, str]:
    cfg = yaml.safe_load(AFFIL_PATH.read_text(encoding="utf-8")) or {}
    return {k: v.get("name") for k, v in (cfg.get("affiliates") or {}).items()
            if isinstance(v, dict) and v.get("name")}


def fact_rows(today: dt.date, facts_dir: Path | None = None) -> list[dict]:
    facts_dir = facts_dir or FACTS_DIR
    names = _names()
    rows = []
    for path in sorted(facts_dir.glob("*.yaml")):
        issuer = path.stem
        card = names.get(issuer) or issuer.capitalize()
        for f in fl.load(path):
            if f["status"] not in fl.PUBLISHABLE_STATUS or f["class"] in EXCLUDED_CLASSES:
                continue
            exp = f.get("expires")
            if exp and dt.date.fromisoformat(_s(exp)) < today:
                continue
            rows.append({
                "issuer": issuer, "card": card, "fact_id": f["id"], "class": f["class"],
                "applies_to": json.dumps(f.get("applies_to") or {}, sort_keys=True),
                "value": _s(f["value"]), "unit": _s(f.get("unit")), "status": f["status"],
                "verified_at": _s(f["verified_at"]), "expires": _s(exp),
                "source_url": f["source_url"], "source_quote": f["source_quote"][:200],
                "confidence": f["confidence"],
            })
    return sorted(rows, key=lambda r: (r["issuer"], r["fact_id"]))


def availability_rows(avail_path: Path = AVAIL_PATH) -> list[dict]:
    cards = (yaml.safe_load(avail_path.read_text(encoding="utf-8")) or {}).get("cards") or {}
    rows = []
    for c in cards.values():
        if not c or not c.get("verified"):
            continue
        src = (c.get("source") or {}).get("url", "")
        for kind, key in (("country", "countries"), ("us_state", "us_states")):
            for j in sorted(c.get(key) or []):
                rows.append({"card": c["name"], "jurisdiction": j, "kind": kind,
                             "available": "true", "verified": _s(c["verified"]), "source_url": src})
    return rows


def review_links(cards: list[str]) -> list[tuple[str, str]]:
    """(card, url): reviews-bucket page whose slug starts with the card's slug
    (shortest wins), else the /reviews/ index."""
    slugs = []
    for p in PSEO_DIR.glob("*.md"):
        head = p.read_text(encoding="utf-8")[:600]
        if re.search(r'^bucket:\s*"?reviews"?\s*$', head, re.M):
            slugs.append(p.stem)
    out = []
    for card in cards:
        pre = _slugify(card)
        hit = sorted((s for s in slugs if s.startswith(pre)), key=lambda s: (len(s), s))
        out.append((card, f"{SITE}reviews/{hit[0]}/" if hit else f"{SITE}reviews/"))
    return out


def _csv(columns: list[str], rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


README = """# Crypto card fees — open dataset

Fees, rates, limits and availability of crypto cards, one row per fact, each with
the issuer page it came from and the date it was last verified. Only facts that
are currently published on {site} are exported: disputed and expired facts are
left out. Coverage is {n_issuers} issuer(s) ({issuers}) — the site reviews more
cards, but a card gets figures here only once it has a verified fact file.

Exported {date}: {n_facts} facts, {n_avail} availability rows.

## Files

- `cards.csv` / `cards.json` — one row per fact.
- `availability.csv` — one row per (card, jurisdiction) the issuer lists as available.

## Columns

`cards.csv`: issuer, card, fact_id, class (rate, fee, limit, availability, feature,
marketing, promo), applies_to (JSON string: tier, band, product...),
value, unit, status (current or beta), verified_at, expires (re-check-by date),
source_url (the issuer's page), source_quote (verbatim, max 200 chars), confidence
(issuer_help, issuer_legal, issuer_blog, secondary). Affiliate-programme facts are
not exported.

`availability.csv`: card, jurisdiction, kind (country or us_state), available
(always true: allow-list only; a jurisdiction not listed is not stated as available),
verified (date the list was compared with the issuer), source_url.

## License

CC BY 4.0 — see `LICENSE`. Attribution is the only condition.

## How to cite

Data from {site} — reviews:
{reviews}

Example: "Source: DefyCard crypto card fees dataset ({site}), exported {date}."
"""

CC_BY_LICENSE = """Creative Commons Attribution 4.0 International (CC BY 4.0)

You are free to share and adapt this dataset for any purpose, even commercially,
provided you give appropriate credit (for example "Data from https://defycard.com/"),
link to the license and indicate if changes were made.

Full legal text: https://creativecommons.org/licenses/by/4.0/legalcode
"""


def build(today: dt.date) -> dict[str, str]:
    facts, avail = fact_rows(today), availability_rows()
    issuers = sorted({r["card"] for r in facts})
    reviews = "\n".join(f"- {c}: {u}" for c, u in review_links(issuers))
    return {
        "cards.csv": _csv(CARD_COLUMNS, facts),
        "cards.json": json.dumps(facts, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        "availability.csv": _csv(AVAIL_COLUMNS, avail),
        "README.md": README.format(site=SITE, n_issuers=len(issuers), issuers=", ".join(issuers),
                                   date=today.isoformat(), n_facts=len(facts),
                                   n_avail=len(avail), reviews=reviews),
        "LICENSE": CC_BY_LICENSE,
    }


def _drift(files: dict[str, str], out: Path) -> list[str]:
    """Files that differ from a fresh export, ignoring the README's export date
    (it moves every day; the data did not)."""
    def norm(name, text):
        return re.sub(r"(?i)exported \d{4}-\d{2}-\d{2}", "exported D", text) if name == "README.md" else text
    return [n for n, t in files.items()
            if not (out / n).exists() or norm(n, (out / n).read_text(encoding="utf-8")) != norm(n, t)]


def write(out: Path, today: dt.date) -> None:
    files = build(today)
    prev = (out / "cards.csv").read_text(encoding="utf-8").count("\n") - 1 if (out / "cards.csv").exists() else 0
    out.mkdir(parents=True, exist_ok=True)
    changed = _drift(files, out)
    for n, t in files.items():
        (out / n).write_text(t, encoding="utf-8")
    if changed:
        now = len(json.loads(files["cards.json"]))
        with (out / "CHANGELOG.md").open("a", encoding="utf-8") as fh:
            fh.write(f"- {today.isoformat()}: {now} facts ({now - prev:+d}), "
                     f"{files['availability.csv'].count(chr(10)) - 1} availability rows\n")


def main(argv=None, out: Path = OUT, today: dt.date | None = None) -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true")
    g.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    today = today or dt.datetime.now(dt.timezone.utc).date()
    if a.check:
        bad = _drift(build(today), out)
        print(json.dumps({"drift": bad}))
        return 1 if bad else 0
    write(out, today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
