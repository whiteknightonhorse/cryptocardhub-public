#!/usr/bin/env python3
"""faq-children.py — Y66 stream 2: promote a FAQ question to its own page, or refuse.

WHY THIS EXISTS
---------------
The site's topic universe is closed: 442 clusters, 544 pages, queue 0, and the
measured-demand sweep found two or three genuinely new head terms in the whole
of Bing. But 540 pages carry a FAQ block — 3,223 questions, 2,954 distinct —
and 119 of the 120 questions sitting on the twenty pages AI agents actually
fetch have no page of their own.

Those questions are not invented long tail. They are written in human language,
they sit inside documents whose consumption is measured weekly, and a question
is the shape a retriever works in.

THE LINE BETWEEN A CHILD AND A RECOMBINATION (Fable, 2026-08-13)
----------------------------------------------------------------
The parent's FAQ answer is 50-80 words of real facts — a retriever can already
quote it. A child that moves that paragraph to its own URL adds nothing and
splits the citation between two documents. So a child exists ONLY where the
honest full answer does not fit in a FAQ box, and that is enforced, not hoped:

    ANTI-TRANSFER   8-gram shingle overlap with the parent page <= 25%
    DELTA           >= 5 named or numeric facts absent from the parent

Both are literals, mirrored in scripts/tests/test_faq_children.py, and both are
measured against the BUILT page (Y50: gates read dist/, not sources).

A draft that cannot reach the delta is REFUSED with its reason recorded (Y58) —
never padded to length. A high refusal rate is data about the topic, not a
failure of the run.

WHAT THIS SCRIPT DOES NOT DO
----------------------------
It does not delete the parent's FAQ answer. The parent keeps a short version
and links to the child: trading a measured citation for an unmeasured one is
exactly the swap the whole programme exists to avoid.

Usage:
    faq-children.py --cohort 20            # select and show, write nothing
    faq-children.py --cohort 20 --write    # write the cohort ledger
    faq-children.py --check <slug>         # run both gates for one built child
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "seo-site" / "src" / "content" / "pseo"
DIST = ROOT / "seo-site" / "dist"
AEO = ROOT / "logs" / "aeo"
LEDGER = ROOT / "logs" / "pseo" / "faq-cohorts.json"

# --- the laws, as literals -------------------------------------------------
SHINGLE_N = 8               # words per shingle
MAX_PARENT_OVERLAP = 0.25   # above this the child is the parent, reworded
MIN_NEW_FACTS = 5           # named/numeric facts the parent does not carry
COHORT_DEFAULT = 20

_REJECTED: list[dict] = []   # questions refused on a false premise, reported

_FACT = re.compile(
    r"\$[\d,]+(?:\.\d+)?|\b\d{1,3}(?:\.\d+)?\s?%|\b\d{1,4}\s?(?:days?|hours?|"
    r"business days|countries|states|months?|weeks?)\b|\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\b")


def shingles(text: str, n: int = SHINGLE_N) -> set[str]:
    words = re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split()
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def overlap(child_text: str, parent_text: str) -> float:
    """Share of the CHILD's shingles that already exist on the parent.

    Direction matters: normalising by the child means a short child copied
    wholesale from a long parent scores 1.0, which is the case we are hunting.
    """
    c, p = shingles(child_text), shingles(parent_text)
    if not c:
        return 1.0
    return round(len(c & p) / len(c), 4)


def facts(text: str) -> set[str]:
    return {m.strip() for m in _FACT.findall(text) if len(m.strip()) > 1}


def new_facts(child_text: str, parent_text: str) -> set[str]:
    return facts(child_text) - facts(parent_text)


def judge(child_text: str, parent_text: str) -> dict:
    """The two gates, together, with the reason spelled out either way."""
    ov = overlap(child_text, parent_text)
    nf = new_facts(child_text, parent_text)
    reasons = []
    if ov > MAX_PARENT_OVERLAP:
        reasons.append(f"anti_transfer: {ov:.0%} of the child already exists on "
                       f"the parent (limit {MAX_PARENT_OVERLAP:.0%})")
    if len(nf) < MIN_NEW_FACTS:
        reasons.append(f"delta: only {len(nf)} new facts, needs {MIN_NEW_FACTS}")
    return {"overlap": ov, "new_facts": sorted(nf)[:20], "new_fact_count": len(nf),
            "passed": not reasons, "reasons": reasons}


# ---------------------------------------------------------------------------
# cohort selection
# ---------------------------------------------------------------------------

def latest_targets() -> dict:
    files = sorted(AEO.glob("targets-*.json"))
    if not files:
        print("[faq] no targets-*.json — run aeo-aggregate.py --targets first",
              file=sys.stderr)
        raise SystemExit(2)
    return json.loads(files[-1].read_text(encoding="utf-8"))


def page_for(path: str) -> Path:
    return CONTENT / f"{path.strip('/').split('/')[-1]}.md"


def questions_of(path: str) -> list[str]:
    f = page_for(path)
    if not f.exists():
        return []
    m = re.search(r"^faq:\s*(\[.*\])\s*$", f.read_text(encoding="utf-8"), re.M)
    if not m:
        return []
    try:
        return [(q.get("q") or "").strip() for q in json.loads(m.group(1))
                if (q.get("q") or "").strip()]
    except json.JSONDecodeError:
        return []


def slugify(q: str) -> str:
    """Truncate at a word boundary — `...supported-coun` is a URL nobody types
    and nobody links, and it is what a blind [:60] produced."""
    s = re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")
    if len(s) <= 60:
        return s
    cut = s[:60]
    return cut[:cut.rindex("-")] if "-" in cut else cut


def normalise_q(q: str) -> str:
    """Two questions that differ only in wording are ONE page.

    "How long does KYC take?" and "How long does KYC verification take on
    ether.fi?" would compete with each other for the same citation — the
    cannibalisation this whole design exists to prevent, introduced by our own
    cohort builder.
    """
    stop = {"the", "a", "an", "of", "to", "in", "for", "and", "is", "it", "do",
            "does", "can", "i", "my", "you", "your", "with", "on", "are", "if",
            "long", "take", "takes", "get", "use", "using", "need", "there",
            "what", "how", "why", "when", "who", "actually", "really"}
    words = re.sub(r"[^a-z0-9]+", " ", q.lower()).split()
    words = [re.sub(r"(?<=[a-z]{3})s$", "", w) for w in words]
    return frozenset(w for w in words if w and w not in stop)


def already_a_page(q: str, existing: set[str]) -> bool:
    """Loose containment: a page whose slug covers every meaningful token."""
    stop = {"the", "a", "an", "of", "to", "in", "for", "and", "is", "it", "do",
            "does", "can", "i", "my", "you", "your", "with", "on", "are", "if"}
    t = {w for w in re.sub(r"[^a-z0-9]+", " ", q.lower()).split()
         if w and w not in stop}
    if not t:
        return True
    return any(t <= {w for w in s.split("-") if w} for s in existing)


# A question can carry a false premise, and a page built to answer it inherits
# the error. Found in the very first cohort: "What happens if I exceed my
# tier's monthly spend limit?" — the $2,000/$10,000/$50,000 figures are the
# boundaries of the 3% cashback band, not spend limits, which is the defect
# Y64 spent the day removing from 231 pages. Answering that question as asked
# would re-print it.
FALSE_PREMISE = (
    (re.compile(r"\b(?:monthly\s+)?spend(?:ing)?\s+limit", re.I),
     "tier figures are cashback-rate bands, not spend limits (SKILL.md 2.2)"),
    (re.compile(r"\b15\s?%|\bfood promo|\bdining promo", re.I),
     "the 15% food promo expired 2026-02-28"),
    (re.compile(r"refundable", re.I),
     "the issuer never calls the physical-card fee refundable"),
    (re.compile(r"free physical card|first card (?:is )?free", re.I),
     "only VIP gets a $0 physical card"),
)


def malformed(q: str) -> str | None:
    """A question that cannot become a decent page title.

    Found in cohort 1: "How does ether.fi Cash compare in a **redotpay
    review**?" — markdown emphasis inside the question is the writer stuffing a
    keyword, and the page it produces is titled that way.
    """
    if "**" in q or "[" in q or "<<" in q:
        return "markdown/keyword stuffing inside the question"
    if not q.rstrip().endswith("?"):
        return "not a question"
    if len(q) < 15 or len(q) > 120:
        return f"length {len(q)} outside 15..120"
    return None


def premise_problem(q: str) -> str | None:
    for rx, why in FALSE_PREMISE:
        if rx.search(q):
            return why
    return None


def select(size: int) -> list[dict]:
    """Questions from the pages agents fetch, richest parent first.

    A child of a cold parent inherits nothing — adjacency exists only where the
    parent's consumption is measured. So the pool is not 2,901; it is the
    questions on fetched pages, in parent fetch order.
    """
    tg = latest_targets()
    existing = {p.stem for p in CONTENT.glob("*.md")}
    out, seen_slug, seen_norm, rejected = [], set(), set(), []
    for row in tg["hot"]:
        for q in questions_of(row["path"]):
            if already_a_page(q, existing):
                continue
            bad = malformed(q) or premise_problem(q)
            if bad:
                rejected.append({"question": q, "reason": bad})
                continue
            slug, norm = slugify(q), normalise_q(q)
            # The richest parent wins a shared question: it was reached first,
            # and its consumption is the evidence the child inherits.
            #
            # Containment, not equality: "How long does KYC take?" and "How
            # long does KYC verification take on ether.fi?" are one question at
            # two levels of detail. Equality kept both, which would have had two
            # of our own pages competing for a single citation — the exact
            # cannibalisation this design forbids, introduced by the builder.
            if slug in seen_slug or slug in existing or not norm:
                continue
            if any(norm <= s or s <= norm for s in seen_norm):
                continue
            seen_slug.add(slug)
            seen_norm.add(norm)
            out.append({"question": q, "parent": row["path"],
                        "parent_fetches": row["fetches"], "slug": slug})
            if len(out) >= size:
                _REJECTED.extend(rejected)
                return out
    _REJECTED.extend(rejected)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", type=int, default=COHORT_DEFAULT)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="open a cohort even though the previous one is unread "
                         "(records the override; use only with a reason)")
    ap.add_argument("--check", metavar="SLUG",
                    help="judge one built child against its parent")
    a = ap.parse_args()

    if a.check:
        led = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}
        row = next((r for c in led.get("cohorts", []) for r in c["items"]
                    if r["slug"] == a.check), None)
        if not row:
            print(f"[faq] {a.check} is not in the ledger", file=sys.stderr)
            return 2
        child = DIST / a.check / "index.md"
        parent = DIST / row["parent"].strip("/") / "index.md"
        for p in (child, parent):
            if not p.exists():
                print(f"[faq] missing built page {p} — build first", file=sys.stderr)
                return 2
        v = judge(child.read_text(encoding="utf-8"), parent.read_text(encoding="utf-8"))
        print(json.dumps(v, indent=2, ensure_ascii=False))
        return 0 if v["passed"] else 1

    cohort = select(a.cohort)
    if _REJECTED:
        print(f"refused on a false premise: {len(_REJECTED)} "
              f"(answering these as asked would re-print a defect)")
        for r in _REJECTED[:8]:
            print(f"   · {r['question'][:78]}\n     {r['reason']}")
        print()
    print(f"cohort of {len(cohort)} questions, from pages agents fetch:\n")
    for r in cohort:
        print(f"  {r['parent_fetches']:4d} fetches on parent  {r['question'][:88]}")
        print(f"       -> /faq/{r['slug']}/")

    if a.write:
        # The tap. Judging capacity, not printing capacity, is the constraint.
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "faq_verdict", ROOT / "scripts" / "faq-verdict.py")
        _fv = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_fv)
        _open, _why = _fv.tap_is_open()
        if not _open and not a.force:
            print(json.dumps({"event": "cohort_refused", "reason": _why,
                              "ts": date.today().isoformat()}, ensure_ascii=False))
            print(f"\n[faq] REFUSING to open a new cohort: {_why}", file=sys.stderr)
            return 1
        led = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {"cohorts": []}
        _today = date.today()
        led["cohorts"].append({
            "opened": _today.isoformat(),
            # Set at open, not left as None: a due date that appears only when
            # someone remembers to compute it is not a due date.
            "verdict_due_14": (_today + timedelta(days=14)).isoformat(),
            "verdict_due_28": (_today + timedelta(days=28)).isoformat(),
            "items": cohort,
        })
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(json.dumps(led, indent=1, ensure_ascii=False) + "\n",
                          encoding="utf-8")
        print(f"\nwrote {LEDGER} — cohort {len(led['cohorts'])}")
        print("Each row is a HYPOTHESIS. The verdict is measured on the FAMILY "
              "(parent + child fetches) against the parent's own baseline, at "
              "+14 and +28 days. Cohort N+1 does not open until cohort N is read.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
