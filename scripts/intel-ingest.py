#!/usr/bin/env python3
"""intel-ingest.py — daily diff-watch over ether.fi's own primary sources.

Schema/spec: FABLE_DEFYCARD_PART2.md section D2/E-07b (5.1: primary sources
only, no X; 6.3: extraction model is Sonnet, not Haiku — money numbers go to
publish without a human review per operator decision 6.2, so the model doing
the reading has to be the stronger one).

FLOW
----
For every source in config/intel_sources.yaml:
  1. fetch (same UA/timeout as availability-check.py — reused, not
     reimplemented: `_ac.fetch`).
  2. reduce the fetch to a CANONICAL text (strips build ids, CSS vars, related-
     article widgets etc. — the volatile chrome around the same content would
     otherwise make every fetch look "changed"). sha256 of that text is the
     comparison key, never the raw HTML.
  3. compare against the source's own stored `last_hash`:
       no stored hash yet   -> BASELINE. Nothing to diff against, so this is
                                not a `changed` event — it just primes state.
                                (Same reasoning as availability-check.py's
                                "unreadable != unchanged": the reverse case
                                matters too — "never checked before" must not
                                be reported as a fact having changed.)
       hash matches         -> healthy, unchanged. 0 events, 0 model calls.
       hash differs          -> `intercom_collection` kind: diff the article-id
                                SET, emit `new_article`/`removed` per id, no
                                model call (D2: "коллекция: ... ⇒
                                new_article/removed" is membership bookkeeping,
                                not a fact to extract).
                                every other kind: unified-diff old vs new
                                canonical text, call Sonnet once to extract
                                facts per the D2 schema, cross-check against
                                config/facts/etherfi.yaml for conflicts, score
                                (D4), write one `changed` event with
                                decision: pending.
  4. a fetch failure writes `fetch_failed` ALWAYS (Y55: a zero must carry its
     cause) and never touches that source's stored state — an unreadable page
     is not "unchanged", same law as case 1 above, opposite direction.
  5. a model-extraction failure (case 3's Sonnet call raising) writes
     `extract_failed` the same way, for the same reason, and is likewise NOT
     fatal to the run — one source's model call raising must not abort every
     other source's processing (that would silently drop already-computed
     events and updates for sources handled earlier in the loop). Neither
     this source's stored hash nor its snapshot advances, so the next run
     re-diffs the SAME real change instead of losing it: advancing the hash
     here would make the page look "seen" without ever having been read.

`--accept <event_id>` (E-07c) is the human's one-command decision and the ONLY
path by which a fetched reading is allowed to become published copy:

    facts.yaml  (new value, old value into history[], verified_at = today)
      -> facts-render.py  (the writer's own SKILL.md block)
      -> intel-route.py   (literal corpus edit with a ceiling, changelog,
                           candidates — D4)
      -> events.jsonl     (a second, appended record: decision accepted)

`intel-route.route()` runs TWICE: once with `write=False` as a pre-flight, and
only then for real. A ceiling refusal must not be able to leave `facts.yaml`
saying USDC while seven published pages still say SCR — that split between the
truth layer and the corpus is the exact shape of Y50/Y53/Y54.

`--reject <event_id>` appends the decision and does nothing else.

No source below may be swapped for a new URL by this script (plan rule:
"никаких новых URL, кроме E-16"). Discovering an unfamiliar article id inside
the tracked collection is reported as `new_article` and nothing more — adding
it as a tracked source is a human edit to config/intel_sources.yaml, exactly
like `--accept` is a human decision for a fact.

CLI:
    intel-ingest.py              # real run: fetch, write events + state
    intel-ingest.py --dry-run    # fetch, print, write NOTHING TO DISK

`--dry-run` still calls extract_facts()/Sonnet on a hash mismatch: the whole
point of a dry run is to preview what a real run would decide, and a preview
with `extracted: []` on every "changed" line is not a preview of anything.
"write nothing" is a promise about the filesystem, not the network —
snapshot, state (last_hash/last_updated_text/last_ok/last_article_ids),
events.jsonl and Telegram are ALL skipped in dry-run (ruling-2 on
w1-07b-intel-ingest-py: `_save_snapshot` used to run unconditionally, so a
`--dry-run` immediately before the real cron run silently consumed the one
real diff a human would ever have seen — the next real run re-diffed the
now-identical snapshot and sent the model an empty diff for a change that
had genuinely happened).
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import html as _html
import importlib.util
import json
import math
import re
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path
from textwrap import indent
from typing import Any, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import corpus_edit as ce  # noqa: E402
import facts_lib as fl  # noqa: E402
from flags import pseo_enabled  # noqa: E402 -- LAW #ONE-PLACE kill-switch (D3: intel-ingest reads it)
import telegram_lib  # noqa: E402

# LAW #ONE-PLACE-ish: don't reimplement the Intercom article JSON parser or the
# fetch() helper — availability-check.py already has both, tested against the
# same issuer HTML shape this script reads.
_AC_SPEC = importlib.util.spec_from_file_location(
    "availability_check_for_intel", ROOT / "scripts" / "availability-check.py")
_ac = importlib.util.module_from_spec(_AC_SPEC)
_AC_SPEC.loader.exec_module(_ac)  # type: ignore[union-attr]


def _load_sibling(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# Hyphenated filenames aren't importable names. `intel_route` does NOT import
# back into this module — the dependency runs one way only, so this stays a
# plain module-level load.
_route = _load_sibling("intel_route", "intel-route.py")
_facts_render = _load_sibling("facts_render", "facts-render.py")
_pg = _load_sibling("pseo_generate", "pseo-generate.py")  # the ONE claude CLI bridge

SOURCES_PATH = ROOT / "config" / "intel_sources.yaml"
FACTS_PATH = fl.DEFAULT_FACTS_PATH
EVENTS_PATH = ROOT / "logs" / "intel" / "events.jsonl"
SNAPSHOT_DIR = ROOT / "logs" / "intel" / "snapshots"
# --from-file reads ONLY from here (provenance by location, not by event schema)
CAPTURES_DIR = ROOT / "logs" / "intel" / "captures"
CONTENT_DIR = ROOT / "seo-site" / "src" / "content" / "pseo"
AEO_DIR = ROOT / "logs" / "aeo"

MODEL = "claude-sonnet-5"
# Compliance gate, mirror of pseo-generate.py's Haiku gate in the opposite
# direction: extraction of unreviewed money copy (6.2/6.3) must never
# silently downgrade to a cheaper, weaker model.
assert "sonnet" in MODEL.lower(), f"intel-ingest extraction model must be Sonnet class; got {MODEL!r}"

EXTRACTION_SYSTEM_PROMPT = """You extract structured facts from a unified diff of an ether.fi issuer page.

Rules:
- Read ONLY the +/- lines given to you below. Never invent a number, currency,
  date, or percentage that is not literally present in the diff text.
- Output a JSON array and NOTHING else -- no prose, no markdown code fence.
- Each element: {"fact_id": "<dot.path>", "old": "<value from a '-' line, or null>",
  "new": "<value from a '+' line>", "quote": "<verbatim quote, <=200 chars, from a '+' line>"}.
- fact_id follows the dot-path convention already used in
  config/facts/etherfi.yaml (e.g. etherfi.affiliate.payout_currency,
  etherfi.affiliate.physical_card_commission.t4).
- If the diff shows no factual change (only cosmetic/markup/build-id noise),
  return [].
"""

CLASS_WEIGHT = {
    "availability": 5, "rate": 4, "fee": 4, "limit": 4,
    "affiliate": 3, "feature": 2,
    # D4 only names "marketing/blog 1" explicitly; `promo` isn't enumerated.
    # Treated at the same low tier rather than defaulting to affiliate/rate
    # weight, since an unexpired promo is still marketing copy, not a
    # standing product term.
    "marketing": 1, "promo": 1,
}


# =============================================================================
# fetch + canonical text (one function per source `kind`)
# =============================================================================

def fetch(url: str, timeout: int = 30) -> str:
    """Same UA/timeout as availability-check.py (plan's own words)."""
    return _ac.fetch(url, timeout=timeout)


_INTERCOM_TEXT_RE = re.compile(
    r'"type":"(?:sub)?heading","text":"([^"]*)"'
    r'|"type":"paragraph","text":"([^"]*)","class":"no-margin"')


def _canon_intercom(html_text: str) -> str:
    """Every heading/subheading/paragraph (incl. table-cell paragraphs, which
    Intercom nests the same way) in document order — ignores buildId, CSS
    custom properties, related-article widgets, all of which change without
    the article's own content changing.

    Deliberately NOT `availability_check.parse_intercom_stream`: that parser
    only appends a paragraph under the heading most recently seen (`elif
    out:`), so an article with no heading at all before its first paragraph —
    article 758757 (FX markup) is exactly that shape, verified against the
    live fixture 2026-09-14 — loses its entire body. availability-check.py's
    own job (grouping cells under a named section like "physical card
    available") genuinely needs that structure; this one only needs a stable
    text blob to hash and diff, so a flat, order-preserving extraction is the
    correct tool here, not the same one reused for a different job."""
    parts = []
    for m in _INTERCOM_TEXT_RE.finditer(html_text):
        parts.append(m.group(1) if m.group(1) is not None else m.group(2))
    return "\n".join(parts)


_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _canon_html(html_text: str) -> str:
    """Visible rendered text only. `<script>`/`<style>` are stripped first —
    Next.js SSR pages (affiliates, gitbook) embed the SAME table a second time
    as an escaped React server-component payload inside a <script> tag, plus a
    build id that changes on every deploy with no content change. Hashing
    that blob would make the page look "changed" on every unrelated deploy."""
    text = _SCRIPT_STYLE_RE.sub(" ", html_text)
    text = _TAG_RE.sub(" ", text)
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _canon_rss(xml_text: str) -> str:
    root = ET.fromstring(xml_text)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        items.append(f"{pub}\t{title}\t{link}")
    return "\n".join(items)


_CANON = {"intercom_article": _canon_intercom, "html": _canon_html, "rss": _canon_rss}


def canon_text(kind: str, raw: str) -> str:
    fn = _CANON.get(kind)
    if fn is None:
        raise ValueError(f"no canonical-text parser for kind {kind!r}")
    return fn(raw)


def _collection_article_ids(html_text: str) -> list[str]:
    """Sorted, deduped article ids linked from an Intercom collection page."""
    return sorted(set(re.findall(r"/en/articles/(\d+)-", html_text)))


_LAST_UPDATED_RE = re.compile(r'"lastUpdatedDate":"([^"]+)"')


def _intercom_updated_text(html_text: str) -> Optional[str]:
    m = _LAST_UPDATED_RE.search(html_text)
    return m.group(1) if m else None


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# =============================================================================
# snapshots (the actual OLD TEXT to diff against — last_hash alone can't
# reconstruct what changed, only THAT it changed)
# =============================================================================

def _load_snapshot(source_id: str) -> Optional[str]:
    p = SNAPSHOT_DIR / f"{source_id}.txt"
    return p.read_text(encoding="utf-8") if p.is_file() else None


def _save_snapshot(source_id: str, text: str) -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    (SNAPSHOT_DIR / f"{source_id}.txt").write_text(text, encoding="utf-8")


def unified_diff_text(old: Optional[str], new: str, limit: int = 6000) -> str:
    diff = "\n".join(difflib.unified_diff(
        (old or "").splitlines(), new.splitlines(), lineterm="", n=2))
    return diff[:limit]


# =============================================================================
# model call (subprocess pattern from pseo-generate.py / councilreview-runner.py
# — Max-plan `claude --print`, no API key; see .claude/CLAUDE.md model routing)
# =============================================================================

def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def call_claude_cli(
    system_prompt: str, user_message: str, model: str = MODEL, timeout_s: int = 180,
) -> tuple[str, dict, float]:
    """Invoke Claude Code CLI in --print mode. Returns (text, usage, cost_usd).
    Raises RuntimeError on subprocess failure or an empty result."""
    return _pg.call_claude_cli(system_prompt, user_message, model, timeout_s)


def extract_facts(source: dict, diff_text: str) -> list[dict]:
    """The ONE call site that talks to the model. Tests monkeypatch
    `call_claude_cli` and count calls against it."""
    user_msg = f"Source: {source['id']} ({source['url']})\n\nUnified diff:\n{diff_text}\n"
    text, _usage, _cost = call_claude_cli(EXTRACTION_SYSTEM_PROMPT, user_msg)
    data = json.loads(_strip_code_fences(text))
    if not isinstance(data, list):
        raise RuntimeError(f"model returned non-list JSON: {type(data).__name__}")
    return data


_LEADING_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?")
_PLAIN_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def _normalize_value_str(s: Any) -> str:
    """Collapse formatting noise, not meaning: `$`, `,`, internal spaces and
    case are never what a real disagreement hinges on. `facts.yaml` stores a
    fact as `value: 25, unit: "%"`; a diff quote is a single string ("25%",
    "$40", "USDC"). Without this, EVERY fact carrying a unit (16 of 18 rows in
    etherfi.yaml, ruling-3) would compare `"25"` against `"25%"` and false-
    conflict on its own correct value, kicking a true fact to `disputed` and
    out of publish (D4) on every single extraction."""
    return str(s).strip().lower().replace("$", "").replace(",", "").replace(" ", "")


def _values_agree(cur: dict, new_raw: Any) -> bool:
    cur_str = _normalize_value_str(f"{cur.get('value')}{cur.get('unit') or ''}")
    new_str = _normalize_value_str(new_raw)
    if cur_str == new_str:
        return True
    # Extraction omitted the unit entirely (e.g. "7" for `value: 7, unit:
    # "days"`) -- that's the model dropping a suffix, not a disagreement.
    # Only the number is a claim; compare it against the fact's own leading
    # number.
    if _PLAIN_NUM_RE.match(new_str):
        m = _LEADING_NUM_RE.match(cur_str)
        return bool(m) and m.group(0) == new_str
    return False


def find_conflicts(extracted: list[dict], facts: list[dict]) -> list[dict]:
    """Extracted items that contradict a fact already on file (any status) —
    D2: a cross-source disagreement is what turns a fact `disputed`, and this
    is the only signal that matters: it doesn't need to know WHICH two sources
    disagree, only that this fetch's reading doesn't match the standing
    record. Nothing here writes to facts.yaml — that's --accept (E-07c)."""
    by_id = {f["id"]: f for f in facts}
    out = []
    for item in extracted:
        cur = by_id.get(item.get("fact_id"))
        if cur is not None and not _values_agree(cur, item.get("new")):
            out.append(item)
    return out


def _fact_class(item: dict, facts: list[dict]) -> Optional[str]:
    for f in facts:
        if f["id"] == item.get("fact_id"):
            return f["class"]
    return None


# =============================================================================
# score (D4)
# =============================================================================

def _bucket_slug_paths_containing(literal: str) -> set[str]:
    """`/bucket/slug/` for every published pSEO page whose source .md still
    contains `literal` verbatim — same bucket-from-frontmatter, slug-from-
    filename convention as aeo-aggregate.py's article_paths()."""
    paths: set[str] = set()
    if not literal or not CONTENT_DIR.is_dir():
        return paths
    for f in CONTENT_DIR.glob("*.md"):
        text = f.read_text(encoding="utf-8", errors="replace")
        if literal not in text:
            continue
        path = ce.page_path_from_text(text, f.stem)
        if path:
            paths.add(path)
    return paths


def pages_affected_bucket(n: int) -> int:
    if n <= 0:
        return 0
    if n <= 10:
        return 1
    if n <= 100:
        return 2
    return 3


def _latest_weekly_aggregate() -> dict:
    files = sorted(AEO_DIR.glob("*-W*.json"))
    if not files:
        return {}
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def fetch_weight(paths: set[str], agg: Optional[dict] = None) -> float:
    agg = agg if agg is not None else _latest_weekly_aggregate()
    verified_by_page = agg.get("verified_by_page") or {}
    total = sum(verified_by_page.get(p, 0) for p in paths)
    return 1 + math.log10(1 + total)


def score(fact_class: Optional[str], old_literal: Optional[str]) -> float:
    """`class_weight * pages_affected_bucket * fetch_weight` (D4)."""
    weight = CLASS_WEIGHT.get(fact_class or "", 1)
    paths = _bucket_slug_paths_containing(old_literal or "")
    bucket = pages_affected_bucket(len(paths))
    return round(weight * bucket * fetch_weight(paths), 3)


# =============================================================================
# events
# =============================================================================

def make_event(
    source: dict, kind: str, *, url: Optional[str] = None,
    extracted: Optional[list[dict]] = None, conflicts: Optional[list[dict]] = None,
    score_value: Optional[float] = None, diff_summary: str = "",
) -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "event_id": uuid.uuid4().hex[:12],
        "ts": now,
        "source_id": source["id"],
        "kind": kind,
        "url": url or source.get("url"),
        "updated_text": source.get("last_updated_text"),
        "diff_summary": diff_summary,
        "extracted": extracted or [],
        "conflicts": conflicts or [],
        "score": score_value,
        "decision": None if kind in ("fetch_failed", "extract_failed") else "pending",
        "decided_at": None,
        "outputs": [],
    }


def _append_event(ev: dict) -> None:
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev, ensure_ascii=False) + "\n")


# =============================================================================
# per-source processing
# =============================================================================

def process_source(
    source: dict, facts: list[dict], *, dry_run: bool = False,
    raw: Optional[str] = None,
) -> tuple[list[dict], dict]:
    """Returns (events, state_updates). `state_updates` is empty on fetch
    failure — an unreadable page is not "unchanged" (same law as
    availability-check.py, opposite direction: never manufacture freshness,
    and never manufacture staleness either).

    `dry_run` gates the ONE disk write this function makes directly (the
    snapshot) — `run()` already gates state/events/Telegram itself, but the
    snapshot save used to happen here unconditionally, so a `--dry-run` would
    silently advance the "old text to diff against" without ever advancing
    `last_hash` alongside it (ruling-2)."""
    if raw is None:  # `raw` = operator-saved capture (--from-file): same pipeline, no network
        try:
            raw = fetch(source["url"])
        except Exception as exc:  # noqa: BLE001 -- any fetch failure is reportable, not fatal to the run
            ev = make_event(source, "fetch_failed", diff_summary=f"{type(exc).__name__}: {exc}")
            return [ev], {}

    if source["kind"] == "intercom_collection":
        ids = _collection_article_ids(raw)
        new_hash = sha256(",".join(ids))
        events: list[dict] = []
        if source.get("last_hash") is not None:
            prev_ids = set(source.get("last_article_ids") or [])
            for aid in sorted(set(ids) - prev_ids):
                events.append(make_event(
                    source, "new_article", url=f"https://help.ether.fi/en/articles/{aid}",
                    diff_summary=f"new article id {aid} in {source['id']}"))
            for rid in sorted(prev_ids - set(ids)):
                events.append(make_event(
                    source, "removed", url=f"https://help.ether.fi/en/articles/{rid}",
                    diff_summary=f"article id {rid} no longer listed in {source['id']}"))
        return events, {
            "last_hash": new_hash, "last_article_ids": ids, "last_ok": date.today().isoformat(),
        }

    text = canon_text(source["kind"], raw)
    new_hash = sha256(text)
    updated_text = _intercom_updated_text(raw) if source["kind"] == "intercom_article" else None
    state_updates = {
        "last_hash": new_hash, "last_updated_text": updated_text,
        "last_ok": date.today().isoformat(),
    }

    prev_hash = source.get("last_hash")
    events = []
    if prev_hash is not None and prev_hash != new_hash:
        diff = unified_diff_text(_load_snapshot(source["id"]), text)
        try:
            extracted = extract_facts(source, diff)
        except Exception as exc:  # noqa: BLE001 -- a model failure is reportable, not
            # fatal to the run (Y55: a zero must carry its cause). State and
            # snapshot are BOTH left untouched so the next run re-diffs the
            # same real change instead of silently losing it: if we advanced
            # last_hash here, the change this source actually has would never
            # be retried, because the next fetch would compare against a hash
            # that already "saw" it.
            events.append(make_event(
                source, "extract_failed", diff_summary=f"{type(exc).__name__}: {exc}"))
            return events, {}
        conflicts = find_conflicts(extracted, facts)
        best_score = max(
            (score(_fact_class(item, facts), item.get("old")) for item in extracted),
            default=0.0,
        )
        events.append(make_event(
            source, "changed", extracted=extracted, conflicts=conflicts,
            score_value=best_score, diff_summary=diff[:300]))
    if not dry_run:
        _save_snapshot(source["id"], text)
    return events, state_updates


# =============================================================================
# config/intel_sources.yaml — line surgery, not a yaml.safe_dump round-trip
# =============================================================================
# Same reasoning as availability-check.py's bump_verified/replace_list: this
# file is hand-commented (why each source is tracked, why `--accept` is the
# only write path for facts) and a full round-trip would reflow every one of
# those comments. Every mutable field is authored as a single scalar line
# (last_article_ids as a flow-style `[..]` list on one line, never a block
# sequence) so the line surgery never needs to span multiple lines.

def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(v) for v in value) + "]"
    if isinstance(value, str):
        return json.dumps(value)
    return str(value)


def _facts_scalar(value: Any) -> str:
    """Same line surgery, one difference: `verified_at` is a YAML *date* in
    config/facts/etherfi.yaml, not a string. Quoting it would silently change
    the field's type on every accept."""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    return _yaml_scalar(value)


def _yaml_field(field: str, value: Any) -> str:
    return f"    {field}: {_yaml_scalar(value)}\n"


def _facts_field(field: str, value: Any) -> str:
    """One rewritten field of config/facts/etherfi.yaml, whole lines.

    `intel_sources.yaml` authors every mutable field as a single scalar line;
    `facts/etherfi.yaml` does NOT — 2 of its 18 records carry `history:` as a
    block sequence, written by hand. Replacing only the `    history:` line
    there leaves the block underneath orphaned and the truth layer stops
    loading for every consumer, so a non-empty list is re-emitted as a block
    in the file's own indentation (the item dicts hold `date` objects, which
    `json.dumps` cannot serialize at all)."""
    if isinstance(value, list) and value:
        body = yaml.safe_dump(value, sort_keys=False, allow_unicode=True,
                              default_flow_style=False, width=10 ** 6)
        return f"    {field}:\n" + indent(body.rstrip("\n"), "      ") + "\n"
    return f"    {field}: {_facts_scalar(value)}\n"


def apply_state_updates(text: str, source_id: str, updates: dict,
                        fmt=_yaml_field) -> str:
    if not updates:
        return text
    lines = text.splitlines(keepends=True)
    out = []
    in_block = False
    dropping = False
    seen: set[str] = set()
    for line in lines:
        if re.match(rf"^  - id:\s*{re.escape(source_id)}\s*$", line):
            in_block = True
        elif in_block and re.match(r"^  - id:\s*\S", line):
            in_block = False
        if dropping:
            # Continuation lines of the field just rewritten (a block sequence
            # or nested mapping under a 4-space key). `fmt` emitted the whole
            # field, so the old body has to go with the old header line.
            if re.match(r"^\s{5,}\S", line):
                continue
            dropping = False
        if in_block:
            m = re.match(r"^    (\w+):", line)
            if m and m.group(1) in updates:
                field = m.group(1)
                out.append(fmt(field, updates[field]))
                seen.add(field)
                dropping = True
                continue
        out.append(line)
    missing = set(updates) - seen
    if missing:
        raise KeyError(f"{source_id}: field(s) not found to update: {sorted(missing)}")
    return "".join(out)


# =============================================================================
# main
# =============================================================================

def _telegram_lines(events: list[dict]) -> list[str]:
    lines = []
    for ev in events:
        if ev["kind"] in ("fetch_failed", "extract_failed"):
            lines.append(f"[intel] {ev['kind']} {ev['source_id']}: {ev['diff_summary'][:150]}")
        elif ev["kind"] == "changed":
            for item in ev["extracted"]:
                lines.append(
                    f"[intel] {ev['score']} {item.get('fact_id')} "
                    f"{item.get('old')}→{item.get('new')} "
                    f"— accept: intel-ingest.py --accept {ev['event_id']}")
        elif ev["kind"] in ("new_article", "removed"):
            lines.append(f"[intel] {ev['kind']} {ev['url']}")
    return lines


# =============================================================================
# --accept / --reject (E-07c) — the human decision, and everything downstream
# =============================================================================

def _load_events() -> list[dict]:
    if not EVENTS_PATH.is_file():
        return []
    rows = []
    for line in EVENTS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def current_event(event_id: str) -> Optional[dict]:
    """The LAST record with this id. `events.jsonl` is append-only, so a
    decision is a new record rather than an edit — the latest one is the
    event's current state."""
    found = None
    for row in _load_events():
        if row.get("event_id") == event_id:
            found = row
    return found


def _render_facts() -> None:
    """facts-render.py writes the generated block in the writer's own
    SKILL.md. Wrapped in a named function so a test can hold the real skill
    file still while proving accept() calls it."""
    _facts_render.write(FACTS_PATH)


class AcceptError(RuntimeError):
    """The rewritten facts.yaml failed its own loader — nothing was written."""


def _match_type(old: Any, new: Any) -> Any:
    """Keep the field's YAML type across an accept.

    `value: 20` is an int in facts.yaml; the extractor hands back the string
    "25". Writing the string flips the type of the field on every accept, and
    a consumer that compares numbers would then compare a string."""
    if isinstance(old, bool) or not isinstance(old, (int, float)):
        return new
    if not isinstance(new, str):
        return new
    try:
        num = float(new.strip())
    except ValueError:
        return new
    return int(num) if isinstance(old, int) and num.is_integer() else num


def apply_accept_to_facts(text: str, event: dict, facts: list[dict],
                          today: Optional[date] = None) -> str:
    """New value in, old value into `history[]`, `verified_at` = today.

    Line surgery on the yaml, not a `yaml.safe_dump` round-trip: this file is
    hand-commented (every `disputed` row carries the reason it is disputed, in
    prose, above it) and a round-trip would reflow all of it."""
    today = today or date.today()
    by_id = {f["id"]: f for f in facts}
    for item in event.get("extracted") or []:
        fact = by_id.get(item.get("fact_id"))
        if fact is None:
            continue
        history = list(fact.get("history") or [])
        # Dates stay dates: the two hand-written history blocks in the real
        # file carry `from:`/`to:` as YAML dates, and a mixed column is a
        # second shape for whoever reads the archive next.
        history.append({
            "value": fact.get("value"),
            "from": fact.get("verified_at"),
            "to": today,
            "source_url": event.get("url"),
            "event_id": event.get("event_id"),
        })
        text = apply_state_updates(text, fact["id"], {
            "value": _match_type(fact.get("value"), item.get("new")),
            "verified_at": today,
            "history": history,
        }, fmt=_facts_field)
    return text


def validate_facts_text(text: str, event: dict) -> list[dict]:
    """Load + schema-validate the REWRITTEN yaml before it reaches disk.

    The rewrite is line surgery, so a field it cannot express correctly
    produces a file that no longer parses — and facts.yaml is the truth layer
    the writer, the renderer and the router all read. Writing first and
    discovering that afterwards kills every consumer at once, so the probe
    runs on a temp copy and `--accept` refuses instead."""
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8",
                                     delete=False) as fh:
        fh.write(text)
        probe = Path(fh.name)
    try:
        facts = fl.load(probe)
    except (fl.FactError, yaml.YAMLError, KeyError, TypeError) as exc:
        raise AcceptError(f"rewritten facts.yaml does not load: "
                          f"{type(exc).__name__}: {exc}") from exc
    finally:
        probe.unlink(missing_ok=True)
    by_id = {f["id"]: f for f in facts}
    for item in event.get("extracted") or []:
        fact = by_id.get(item.get("fact_id"))
        if fact is None:
            continue
        if str(fact.get("value")) != str(item.get("new")):
            raise AcceptError(
                f"{item.get('fact_id')}: value is {fact.get('value')!r} after "
                f"the rewrite, expected {item.get('new')!r}")
    return facts


def decide(event_id: str, decision: str, *, expect: Optional[int] = None,
           content_dir: Optional[Path] = None, write: bool = True) -> dict:
    """`--accept` / `--reject`. Returns a result dict; `refused` is non-null
    when nothing was written anywhere."""
    event = current_event(event_id)
    if event is None:
        return {"event": "refused", "reason": "no_such_event", "event_id": event_id}
    if event.get("decision") in ("accepted", "rejected"):
        return {"event": "refused", "reason": f"already_{event['decision']}",
                "event_id": event_id}
    if event.get("decision") != "pending":
        return {"event": "refused", "reason": "not_decidable",
                "event_id": event_id, "kind": event.get("kind")}

    decided = dict(event, decision=decision, outputs=[],
                   decided_at=datetime.now(timezone.utc)
                   .isoformat(timespec="seconds").replace("+00:00", "Z"))

    if decision == "rejected":
        if write:
            _append_event(decided)
        return {"event": "rejected", "event_id": event_id}

    facts = fl.load(FACTS_PATH)
    kwargs = {"expect": expect, "write": False}
    if content_dir is not None:
        kwargs["content_dir"] = content_dir
    # Pre-flight: measure and let every ceiling refuse BEFORE facts.yaml moves.
    preflight = _route.route(decided, facts, **kwargs)
    if preflight["refused"]:
        return {"event": "refused", "reason": preflight["refused"],
                "event_id": event_id, "conflicts": preflight["conflicts"],
                "pages_affected": preflight["pages_affected"]}

    if write:
        new_text = apply_accept_to_facts(
            FACTS_PATH.read_text(encoding="utf-8"), decided, facts)
        try:
            validate_facts_text(new_text, decided)
        except AcceptError as exc:
            return {"event": "refused", "reason": f"facts_rewrite_invalid: {exc}",
                    "event_id": event_id, "conflicts": [], "pages_affected": 0}
        FACTS_PATH.write_text(new_text, encoding="utf-8")
        _render_facts()

    kwargs["write"] = write
    result = _route.route(decided, fl.load(FACTS_PATH), **kwargs)
    decided["outputs"] = [
        {"kind": "corpus_edit", "ref": f"{result['pages_affected']} pages: {result['files']}"},
        {"kind": "changelog", "ref": result["changelog_path"]},
    ] + [{"kind": f"candidate:{c['kind']}", "ref": c.get("target_path")}
         for c in result["candidates"] if not c["capped"]]
    if write:
        _append_event(decided)
    return {"event": "accepted", "event_id": event_id, **result}


_FAIL_KINDS = ("fetch_failed", "extract_failed")


def _standing_failure(source: dict, events: list[dict]) -> Optional[tuple[dict, str]]:
    """(last event, date the current failure series began) when this source's
    latest event is a failure not followed by a successful read, else None."""
    mine = [e for e in events if e.get("source_id") == source["id"]]
    if not mine or mine[-1]["kind"] not in _FAIL_KINDS:
        return None
    last = mine[-1]
    last_ok = source.get("last_ok")
    if last_ok is not None and str(last_ok) > last["ts"][:10]:
        return None
    start = last["ts"][:10]
    for e in reversed(mine):
        if e["kind"] not in _FAIL_KINDS:
            break
        start = e["ts"][:10]
    return last, start


def run(dry_run: bool = False, only: Optional[str] = None,
        raw: Optional[str] = None) -> list[dict]:
    raw_text = SOURCES_PATH.read_text(encoding="utf-8")
    cfg = yaml.safe_load(raw_text) or {}
    sources = cfg.get("sources") or []

    try:
        facts = fl.load(FACTS_PATH)
    except fl.FactError as exc:
        print(f"[intel] facts.yaml load failed, conflict detection disabled: {exc}", file=sys.stderr)
        facts = []

    if only is not None:
        sources = [s for s in sources if s["id"] == only]

    prior = _load_events()
    all_events: list[dict] = []
    quiet: set[str] = set()  # event_ids that go to the ledger but not to Telegram
    recovered: list[str] = []
    for source in sources:
        standing = _standing_failure(source, prior)
        events, updates = process_source(source, facts, dry_run=dry_run, raw=raw)
        all_events.extend(events)
        if standing:
            for ev in events:
                # same cause as yesterday = standing gap, not news (weekly report carries it)
                if ev["kind"] == "fetch_failed" and ev["diff_summary"] == standing[0]["diff_summary"]:
                    quiet.add(ev["event_id"])
            if updates.get("last_ok"):
                since = date.fromisoformat(str(source.get("last_ok") or standing[1])[:10])
                recovered.append(
                    f"[intel] recovered {source['id']}: readable again after "
                    f"{(date.today() - since).days} d")
        if not dry_run and updates:
            raw_text = apply_state_updates(raw_text, source["id"], updates)

    if not dry_run:
        for ev in all_events:
            _append_event(ev)
        SOURCES_PATH.write_text(raw_text, encoding="utf-8")
        lines = _telegram_lines([e for e in all_events if e["event_id"] not in quiet]) + recovered
        if lines:
            telegram_lib.alert("\n".join(lines))

    return all_events


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--accept", metavar="EVENT_ID")
    ap.add_argument("--reject", metavar="EVENT_ID")
    ap.add_argument("--expect", type=int, default=None,
                    help="with --accept: how many pages the corpus edit is "
                         "supposed to change (default: the measured count)")
    ap.add_argument("--from-file", nargs=2, metavar=("SOURCE_ID", "PATH"),
                    help="ingest a browser-saved page (under logs/intel/captures/) "
                         "for one source instead of fetching it")
    args = ap.parse_args(argv)

    if not pseo_enabled():
        print(json.dumps({"event": "skip", "reason": "pseo_disabled"}))
        return 0

    if args.accept or args.reject:
        result = decide(args.accept or args.reject,
                        "accepted" if args.accept else "rejected",
                        expect=args.expect, write=not args.dry_run)
        for line in result.get("conflicts") or []:
            print(line)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result["event"] == "refused" else 0

    only = raw = None
    if args.from_file:
        only, path = args.from_file
        target = Path(path).resolve()
        if not target.is_relative_to(CAPTURES_DIR.resolve()) or not target.is_file():
            print(f"[intel] --from-file refused: {path} is not a file under {CAPTURES_DIR}", file=sys.stderr)
            return 1
        cfg = yaml.safe_load(SOURCES_PATH.read_text(encoding="utf-8")) or {}
        if only not in [s["id"] for s in cfg.get("sources") or []]:
            print(f"[intel] --from-file refused: unknown source id {only}", file=sys.stderr)
            return 1
        raw = target.read_text(encoding="utf-8", errors="replace")

    events = run(dry_run=args.dry_run, only=only, raw=raw)
    kinds = {}
    for ev in events:
        kinds[ev["kind"]] = kinds.get(ev["kind"], 0) + 1
    print(json.dumps({"event": "run_complete", "events": len(events), "by_kind": kinds}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
