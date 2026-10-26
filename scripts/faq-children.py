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
sys.path.insert(0, str(ROOT / "scripts"))
from slug_lib import slugify, retired_slugs  # noqa: E402 -- LAW #ONE-PLACE canonical slugify
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
    (re.compile(r"\$40", re.I),
     "the physical-card fee is $50 Core / $100 Luxe+Pinnacle / $0 VIP, never $40"),
)

# A question about a competitor's fee or a tax rate can never be backed by the
# issuer fact model (G36), so answering it means a number-free page off-mission.
# Refused at selection even when the question names the issuer: a comparison's
# substance is the competitor's numbers, which the issuer fact model cannot back.
_OFF_ISSUER = re.compile(
    r"\btax(?:es|ation|able)?\b|\b(?:redotpay|coinbase|binance|bybit|nexo|wirex|"
    r"gnosis\s?pay|kast|bitget|revolut|crypto\.com|bitpay|uphold|plutus)\b", re.I)
_ISSUER = re.compile(r"ether\.?fi", re.I)


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


def off_issuer(q: str) -> str | None:
    if _OFF_ISSUER.search(q):
        return "off_issuer"
    return None


def reject_reason(q: str) -> str | None:
    """The one selector law: select() and the writer loop both call it."""
    return (malformed(q) or premise_problem(q) or off_issuer(q)
            or ("retired_slug" if slugify(q) in retired_slugs() else None))


def premise_problem(q: str) -> str | None:
    for rx, why in FALSE_PREMISE:
        if rx.search(q):
            return why
    return None


def _refused_slots() -> dict[str, dict]:
    """slug -> its latest `refused` record across every cohort in the ledger."""
    led = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}
    out: dict[str, dict] = {}
    for c in led.get("cohorts", []):
        for it in c["items"]:
            if it.get("refused"):
                out[it["slug"]] = it["refused"]
    return out


def _retryable(refusal: dict, writer_sha: str | None) -> bool:
    """A delta refusal is a property of the topic: closed for good. A G36
    refusal is a property of the generator: retry once per writer change, never
    on an unchanged (or unknown) writer. Rows written before the ledger kept
    `gate` are G36 if their reasons say unbacked_number."""
    gate = refusal.get("gate") or (
        "G36" if any(str(r).startswith("unbacked_number") for r in refusal.get("reasons", []))
        else "delta")
    return (gate == "G36" and writer_sha is not None
            and refusal.get("writer_sha") != writer_sha)


def select(size: int, writer_sha: str | None = None) -> list[dict]:
    """Questions from the pages agents fetch, richest parent first.

    A child of a cold parent inherits nothing — adjacency exists only where the
    parent's consumption is measured. So the pool is not 2,901; it is the
    questions on fetched pages, in parent fetch order.
    """
    tg = latest_targets()
    existing = {p.stem for p in CONTENT.glob("*.md")}
    refused = _refused_slots()
    out, seen_slug, seen_norm, rejected = [], set(), set(), []
    for row in tg["hot"]:
        for q in questions_of(row["path"]):
            if already_a_page(q, existing):
                continue
            bad = reject_reason(q)
            if bad:
                rejected.append({"question": q, "reason": bad})
                continue
            slug, norm = slugify(q), normalise_q(q)
            if slug in refused and not _retryable(refused[slug], writer_sha):
                continue
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


def cohort_is_consumed(led: dict) -> bool:
    """True if the LAST cohort has nothing left open (every item published or
    refused), or there is no cohort yet. Used by --only-if-consumed (Fable
    2026-09-14, A4 step 5): the tap (tap_is_open, above) answers "has cohort N
    been JUDGED at +14" — a different question from "has cohort N finished
    PRINTING". Cohort 1 sat at open=0 for days with the tap open the whole
    time because nothing ever re-checked whether there was still printing
    capacity left before opening cohort 2 — this is that second check.
    """
    cohorts = led.get("cohorts") or []
    if not cohorts:
        return True
    if cohorts[-1].get("voided"):
        return True
    items = cohorts[-1].get("items", [])
    return all(item.get("published") or item.get("refused") for item in items)


def _faq_verdict_module():
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "faq_verdict", ROOT / "scripts" / "faq-verdict.py")
    _fv = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_fv)
    return _fv


def _writer_sha() -> str:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("pseo_generate_sha", ROOT / "scripts" / "pseo-generate.py")
    _pg = _ilu.module_from_spec(_spec)
    sys.modules[_spec.name] = _pg  # dataclasses in pseo-generate need it registered
    _spec.loader.exec_module(_pg)
    return _pg.writer_sha()


def open_next_cohort(size: int = COHORT_DEFAULT, force: bool = False,
                     only_if_consumed: bool = False, writer_sha: str | None = None) -> dict:
    """Open the next cohort onto the ledger, gates identical to `--write`.

    Single source of truth for the ledger mutation (LAW #ONE-PLACE, same
    discipline as the shared slugify()): the CLI's `--write` path and
    pseo-generate.py's auto-trigger (2026-09-14, A4 step 5 rework — see its
    call site) both call this, so there is exactly one place that decides
    whether a cohort is allowed to open.

    -> {"event": "opened", "cohort": N, "size": M} or
       {"event": "cohort_refused", "reason": "<tap reason>" | "cohort_not_consumed"}
    """
    led = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {"cohorts": []}

    # The tap. Judging capacity, not printing capacity, is the constraint.
    is_open, why = _faq_verdict_module().tap_is_open(led)
    if not is_open and not force:
        return {"event": "cohort_refused", "reason": why}
    if only_if_consumed and not cohort_is_consumed(led):
        return {"event": "cohort_refused", "reason": "cohort_not_consumed"}

    cohort = select(size, writer_sha)
    today = date.today()
    led.setdefault("cohorts", []).append({
        "opened": today.isoformat(),
        # Set at open, not left as None: a due date that appears only when
        # someone remembers to compute it is not a due date.
        "verdict_due_14": (today + timedelta(days=14)).isoformat(),
        "verdict_due_28": (today + timedelta(days=28)).isoformat(),
        "items": cohort,
    })
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(led, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"event": "opened", "cohort": len(led["cohorts"]), "size": len(cohort)}


def void_cohort(n: int, reason: str, ruling: str = "CCH-FAQ-G36-0929.ruling-2") -> dict:
    """Mark cohort N (1-based) void: a ledger property, nothing is deleted.
    Same lock file as pseo-generate._faq_ledger_txn, so it cannot interleave
    with a cron tick. Refuses a cohort that is already judged or voided."""
    import fcntl
    with open(LEDGER.with_suffix(".json.lock"), "a+") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            led = json.loads(LEDGER.read_text(encoding="utf-8"))
            cohorts = led.get("cohorts", [])
            if not 1 <= n <= len(cohorts):
                raise ValueError(f"no cohort {n}")
            c = cohorts[n - 1]
            for k in ("verdict_14", "verdict_28", "voided"):
                if c.get(k):
                    raise ValueError(f"cohort {n} already has {k}")
            c["voided"] = {"on": date.today().isoformat(), "ruling": ruling, "reason": reason}
            LEDGER.write_text(json.dumps(led, indent=1, ensure_ascii=False) + "\n",
                              encoding="utf-8")
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
    return c["voided"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", type=int, default=COHORT_DEFAULT)
    ap.add_argument("--void", type=int, metavar="N",
                    help="void cohort N (needs --reason); refuses a judged or voided cohort")
    ap.add_argument("--reason", help="why cohort N is void (with --void)")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="open a cohort even though the previous one is unread "
                         "(records the override; use only with a reason)")
    ap.add_argument("--only-if-consumed", action="store_true",
                    help="cron-safe auto-open: refuse (cohort_not_consumed) unless "
                         "the tap is open AND the last cohort has finished printing "
                         "(every item published or refused). Without this flag, "
                         "behaviour is unchanged from before 2026-09-14.")
    ap.add_argument("--check", metavar="SLUG",
                    help="judge one built child against its parent")
    a = ap.parse_args()

    if a.void:
        if not a.reason:
            print("[faq] --void needs --reason", file=sys.stderr)
            return 2
        try:
            rec = void_cohort(a.void, a.reason)
        except ValueError as e:
            print(f"[faq] REFUSING to void: {e}", file=sys.stderr)
            return 1
        print(json.dumps({"event": "voided", "cohort": a.void, **rec}, ensure_ascii=False))
        return 0

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

    if not a.write:
        cohort = select(a.cohort, _writer_sha())
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
        return 0

    result = open_next_cohort(size=a.cohort, force=a.force, only_if_consumed=a.only_if_consumed,
                              writer_sha=_writer_sha())
    if result["event"] == "cohort_refused":
        print(json.dumps({**result, "ts": date.today().isoformat()}, ensure_ascii=False))
        print(f"\n[faq] REFUSING to open a new cohort: {result['reason']}", file=sys.stderr)
        return 1
    print(f"cohort of {result['size']} questions written as cohort {result['cohort']}")
    print(f"\nwrote {LEDGER} — cohort {result['cohort']}")
    print("Each row is a HYPOTHESIS. The verdict is measured on the FAMILY "
          "(parent + child fetches) against the parent's own baseline, at "
          "+14 and +28 days. Cohort N+1 does not open until cohort N is read.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
