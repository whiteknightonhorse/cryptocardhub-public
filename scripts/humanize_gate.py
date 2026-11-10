#!/usr/bin/env python3
"""humanize_gate.py — G37 linter (CCH-HUM-02): deterministic AI-tell detector.

WHY THIS EXISTS
---------------
The corpus baseline (578 pages, 2026-10-19) shows the loudest AI tell is not
vocabulary but em-dash density (578 of 578 pages, ~14 per page), followed by
stock lexicon, stock phrases and "not just X but Y" contrast. A regex cannot
judge prose; it can count the cheap, objective tells so that a later copy-edit
call (CCH-HUM-03) rewrites only the flagged sentences. Zero model calls here.

THE LAW
-------
* `lint(text)` flags tells; `score(text)` is flags per 1000 words; a page is
  "over" when score > THRESHOLD.
* Frontmatter is JSON, so em-dashes arrive as the six characters `\\u2014`;
  they are decoded first (same as money_gate.tokens) or they are invisible.
* Skip zones are never flagged: headings, placeholder-only lines, the
  `**Signal:**`-style callout LABEL (not the sentence after it), and the FTC
  disclosure sentence (recognised by pseo-generate's own marker).
* Em-dashes are flagged only when page density exceeds EM_DASH_PER_1000.
* The gate may touch only `payload_fields()`; title/description never.

There is deliberately NO kill-switch env var; rollback is a revert.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import statistics
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_batch as ab  # noqa: E402 -- API-CREDITS-1108/3b batch lane
from typing import Any

import yaml

import money_gate

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "seo-site" / "src" / "content" / "pseo"

LEXICON = (
    "delve", "tapestry", "landscape", "seamless", "seamlessly", "robust", "unlock",
    "game-changer", "game changer", "leverage", "elevate", "empower", "navigate",
    "navigating", "realm", "testament", "pivotal", "ever-evolving", "cutting-edge",
    "crucial",
)
PHRASES = (
    "in today's", "it's worth noting", "it is worth noting", "whether you're",
    "in conclusion", "overall,", "it is important to", "keep in mind",
    "at the end of the day", "when it comes to", "look no further",
)
INTENSIFIERS = ("very", "really", "actually", "truly", "ultimately", "unfortunately")
CONTRAST = (
    r"\bnot (?:just|only|merely)\b.{0,40}\bbut\b",
    r"\bisn't just\b",
    r"\bit's not\b.{0,30},\s*it's\b",
)
EM_DASH_PER_1000 = 3.0
THRESHOLD = 4.0
VOICE_SPEC = (
    "Voice: plain and specific. One claim per sentence. No em-dash: use a comma, "
    "a period or a colon. No 'not just X but Y' or 'it's not X, it's Y'. No "
    "intensifiers (very, really, actually, truly, ultimately, unfortunately). "
    "No stock phrases (in today's, it's worth noting, whether you're, in "
    "conclusion, keep in mind, when it comes to). Never use the lexicon words: "
    + ", ".join(LEXICON) + "."
)
assert len(VOICE_SPEC) <= 700, len(VOICE_SPEC)


def voice_block() -> str:
    """What every writer prompt prints BEFORE its skill excerpt (the linter's own lists,
    ONE place): the voice spec plus the full ban line."""
    return ("\n## Voice (G37 linter law)\n" + VOICE_SPEC + "\n"
            "Never use these words or phrases: "
            + ", ".join(LEXICON + PHRASES + INTENSIFIERS) + "\n")

_LEX_RES = [("lexicon", re.compile(rf"(?<!\w){re.escape(w)}(?!\w)", re.I)) for w in LEXICON]
_PHRASE_RES = [("phrase", re.compile(rf"(?<!\w){re.escape(p)}", re.I)) for p in PHRASES]
_INT_RES = [("intensifier", re.compile(rf"(?<!\w){re.escape(w)}(?!\w)", re.I)) for w in INTENSIFIERS]
_CONTRAST_RES = [("contrast", re.compile(p, re.I)) for p in CONTRAST]
_RULES = _LEX_RES + _PHRASE_RES + _INT_RES + _CONTRAST_RES

_JSON_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_HEADING = re.compile(r"^\s*#{1,6}\s")
_PLACEHOLDER_ONLY = re.compile(r"^\s*<<[A-Z0-9_]+>>\s*$")
_CALLOUT_LABEL = re.compile(r"^\s*(?:[-*]\s+)?\*\*[A-Za-z][A-Za-z ]{0,30}:\*\*")
_LINK_TARGET = re.compile(r"\]\([^)]*\)")
_SENT_GAP = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"\w+(?:['’-]\w+)*")


@dataclass(frozen=True)
class Flag:
    kind: str
    sentence: str
    start: int
    end: int


_pg = None


def _load_pg():
    global _pg
    if _pg is None:
        name = "pseo_generate_for_humanize_gate"
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "pseo-generate.py")
        _pg = importlib.util.module_from_spec(spec)
        sys.modules[name] = _pg   # its @dataclass needs this pre-registered
        spec.loader.exec_module(_pg)
    return _pg


def _disclosure_marker() -> str:
    """pseo-generate's own DISCLOSURE_MARKER (what validate_intro tests), not a copy."""
    return _load_pg().DISCLOSURE_MARKER


def _decode(text: str) -> str:
    text = _JSON_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)
    return text.replace("’", "'")   # same length: offsets stay valid


def _sentences(text: str):
    """Yield (sentence, offset, paragraph_id) for every lintable sentence."""
    marker = _disclosure_marker()
    off = 0
    for pid, line in enumerate(text.split("\n")):
        base = off
        off += len(line) + 1
        if not line.strip() or _HEADING.match(line) or _PLACEHOLDER_ONLY.match(line):
            continue
        pos = 0
        parts = []
        for m in _SENT_GAP.finditer(line):
            parts.append((pos, m.start()))
            pos = m.end()
        parts.append((pos, len(line)))
        for a, b in parts:
            s = line[a:b]
            if marker in s:
                continue
            if a == 0:
                lab = _CALLOUT_LABEL.match(s)
                if lab:   # blank the label, keep offsets
                    s = " " * lab.end() + s[lab.end():]
            s = _LINK_TARGET.sub(lambda m: "]" + " " * (len(m.group()) - 1), s)
            if s.strip():
                yield s, base + a, pid


def lint(text: str) -> list[Flag]:
    text = _decode(text)
    sents = list(_sentences(text))
    flags: list[Flag] = []
    words = 0
    for s, off, _pid in sents:
        words += len(_WORD.findall(s))
        for kind, rx in _RULES:
            for m in rx.finditer(s):
                flags.append(Flag(kind, s.strip(), off + m.start(), off + m.end()))
    dashes = sum(s.count("—") for s, _o, _p in sents)
    if words and dashes * 1000 / words > EM_DASH_PER_1000:
        for s, off, _pid in sents:
            i = s.find("—")
            if i >= 0:
                flags.append(Flag("emdash", s.strip(), off + i, off + i + 1))
    # repeated opener: same first two words on 3 consecutive sentences of one paragraph
    for i in range(2, len(sents)):
        trio = sents[i - 2:i + 1]
        if len({p for _s, _o, p in trio}) == 1:
            heads = {tuple(w.lower() for w in _WORD.findall(s)[:2]) for s, _o, _p in trio}
            if len(heads) == 1 and len(next(iter(heads))) == 2:
                s, off, _p = sents[i]
                flags.append(Flag("opener", s.strip(), off, off + len(s)))
    return sorted(flags, key=lambda f: f.start)


def _count_words(text: str) -> int:
    return sum(len(_WORD.findall(s)) for s, _o, _p in _sentences(_decode(text)))


def score(text: str) -> float:
    """Flags per 1000 words of lintable text (0.0 when there is none)."""
    words = _count_words(text)
    return len(lint(text)) * 1000 / words if words else 0.0


def _slots(payload: dict[str, Any]):
    """(container, key) for every string the gate may touch."""
    for k in ("intro_paragraph", "body_markdown", "long_context_paragraph"):
        if isinstance(payload.get(k), str):
            yield payload, k
    for k in ("snapshot_bullets", "what_to_watch", "bottom_line"):
        for i, x in enumerate(payload.get(k) or []):
            if isinstance(x, str):
                yield payload[k], i
    for q in payload.get("faq") or []:
        if isinstance(q, dict) and isinstance(q.get("a"), str):
            yield q, "a"


def payload_fields(payload: dict[str, Any]) -> list[str]:
    """The ONLY strings the gate may touch. title/description never."""
    return [c[k] for c, k in _slots(payload)]


def page_text(path: Path) -> str:
    """A page file's gate-visible text: frontmatter payload fields + markdown body."""
    m = re.match(r"---\n(.*?)\n---\n(.*)", path.read_text(encoding="utf-8"), re.S)
    if not m:
        return ""
    fm = yaml.safe_load(m.group(1)) or {}
    fm["body_markdown"] = m.group(2)
    return "\n\n".join(payload_fields(fm))


def corpus_report(corpus: Path = CORPUS) -> dict[str, Any]:
    scores, kinds = [], Counter()
    for p in sorted(corpus.glob("*.md")):
        t = page_text(p)
        fl = lint(t)
        kinds.update(f.kind for f in fl)
        w = _count_words(t)
        scores.append(len(fl) * 1000 / w if w else 0.0)
    return {
        "pages": len(scores),
        "median_score": round(statistics.median(scores), 2) if scores else 0.0,
        "above_threshold": sum(s > THRESHOLD for s in scores),
        "flags_by_kind": dict(kinds),
    }


# --- G37 copy-edit call (CCH-HUM-03) ----------------------------------------

_EDITOR_PROMPT = (
    " You are a copy editor, not an author. Rewrite each numbered sentence to "
    "remove the named pattern. Keep every number, link, <<PLACEHOLDER>>, product "
    "name and claim exactly. Add nothing. Output one JSON object {id: rewritten "
    "sentence}."
)
_PLACEHOLDER = re.compile(r"<<[^<>]*>>")
_LINK_TARGETS = re.compile(r"\]\(([^)]*)\)|(https?://[^\s)>\]]+)")
_LIST_PREFIX = re.compile(r"^\s*(?:[-*]|\d+\.)\s+")


@dataclass
class HumanizeResult:
    payload: dict[str, Any]
    flags_before: int
    flags_after: int
    sentences_flagged: int = 0
    accepted: int = 0
    rejected: int = 0
    rewritten: bool = False
    refused_reason: str | None = None
    usage: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    medium_eligible: bool | None = None   # None = G37b not run
    style_reasons: list = field(default_factory=list)


def _money(t: str) -> Counter:
    return Counter((v, u) for v, u, _s, _e in money_gate.tokens(t))


def _invariant_failure(old: str, new: str) -> str | None:
    """Why `new` may not replace `old`, or None. Per-sentence, so structure is unchanged by construction."""
    if not isinstance(new, str) or not new.strip():
        return "empty"
    if "\n" in new:
        return "newline"
    if Counter(_PLACEHOLDER.findall(old)) != Counter(_PLACEHOLDER.findall(new)):
        return "placeholders"
    if _money(old) != _money(new):
        return "money"
    tg = lambda t: {a or b for a, b in _LINK_TARGETS.findall(t)}
    if tg(old) != tg(new):
        return "links"
    if old.count("**") != new.count("**"):
        return "bold"
    if (m := _LIST_PREFIX.match(old)) and not new.startswith(m.group()):
        return "list_prefix"
    if not 0.5 * len(_WORD.findall(old)) <= len(_WORD.findall(new)) <= 1.5 * len(_WORD.findall(old)):
        return "length"
    if lint(new):
        return "reflag"
    return None


def _flagged_sentences(text: str) -> dict[str, set[str]]:
    """{original sentence: {flag kinds}} — sliced from the decoded text, not the masked lint copy."""
    text = _decode(text)
    spans = [(off, off + len(s), s) for s, off, _p in _sentences(text)]
    out: dict[str, set[str]] = {}
    for f in lint(text):
        for a, b, s in spans:
            if a <= f.start < b:
                out.setdefault(text[a:b].strip(), set()).add(f.kind)
    return out


# --- G37b: the sales style-lint.py as a second deterministic lint (CCH-MEDIUM-01) ---
# style-lint.py is a VERBATIM copy of the sales file (md5 pinned in the tests);
# drift is a sales-side change. It lints HTML, so the payload is projected to
# <p>/<li>/<h2>/<h3> first. RED never blocks a page: it only decides medium_eligible.

_STYLE_LINT = Path(__file__).resolve().parent / "style-lint.py"
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_LIST = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(.*)")


def _md_clean(t: str) -> str:
    t = _MD_LINK.sub(r"\1", t)
    return re.sub(r"\*\*|`|^>\s*", "", t).replace("*", "").strip()


def _md_blocks(md: str) -> list[tuple[str, str]]:
    """Markdown -> [(tag, text)]. Images, tables, rules, raw HTML and <<PLACEHOLDER>> lines are not prose."""
    out: list[tuple[str, str]] = []
    para: list[str] = []

    def flush():
        if para:
            out.append(("p", _md_clean(" ".join(para))))
            para.clear()
    for line in md.splitlines():
        st = line.strip()
        if not st:
            flush()
        elif st.startswith(("![", "|", "<", "---", "<<")):
            flush()
        elif (h := re.match(r"(#{1,6})\s+(.*)", st)):
            flush()
            out.append((f"h{min(len(h.group(1)), 3)}" if len(h.group(1)) > 1 else "h1", _md_clean(h.group(2))))
        elif (li := _MD_LIST.match(line)):
            flush()
            out.append(("li", _md_clean(li.group(1))))
        else:
            para.append(st)
    flush()
    return [(t, x) for t, x in out if x]


def style_projection(payload: dict[str, Any]) -> str:
    """The article prose the template renders (long_context_paragraph is not rendered), as minimal HTML for style-lint.py."""
    blocks: list[tuple[str, str]] = []
    if isinstance(payload.get("intro_paragraph"), str):
        blocks += _md_blocks(payload["intro_paragraph"])
    for b in payload.get("snapshot_bullets") or []:
        blocks.append(("li", _md_clean(str(b))))
    blocks += _md_blocks(payload.get("body_markdown") or "")
    for k in ("what_to_watch", "bottom_line"):
        blocks += [("li", _md_clean(str(b))) for b in payload.get(k) or []]
    for q in payload.get("faq") or []:
        if isinstance(q, dict):
            blocks.append(("h3", _md_clean(str(q.get("q", "")))))
            blocks += _md_blocks(str(q.get("a", "")))
    return "\n".join(f"<{t}>{x.replace('&', '&amp;').replace('<', '&lt;')}</{t}>" for t, x in blocks if x)


def style_lint(payload: dict[str, Any]) -> dict[str, Any]:
    """{'green': bool, 'reasons': [...], ...} from `python3 -I scripts/style-lint.py --json`."""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "page.html"
        f.write_text(style_projection(payload), encoding="utf-8")
        r = subprocess.run([sys.executable, "-I", str(_STYLE_LINT), "--json", str(f)],
                           capture_output=True, text=True, timeout=60)
    if r.returncode not in (0, 1):
        raise RuntimeError(f"style-lint rc={r.returncode}: {r.stderr[:200]}")
    return next(iter(json.loads(r.stdout).values()))


def _style_sentences(payload: dict[str, Any], reasons: list[str]) -> set[str]:
    """Raw sentences of payload_fields that style-lint flags on their own (banned, '!', emoji, dashes; passive only if cited)."""
    spec = importlib.util.spec_from_file_location("_style_lint", _STYLE_LINT)
    sl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sl)
    bad: set[str] = set()
    for text in payload_fields(payload):
        for blk in re.split(r"\n+", text):
            for s in sl.split_sentences(blk):
                c = _md_clean(s)
                if not sl.lint_blocks([("p", c)])["green"] or (
                        any("passive" in r for r in reasons) and sl.PASSIVE.search(c)):
                    bad.add(s.strip())
    return bad


def humanize(payload: dict[str, Any], call=None, g37b: bool = False) -> HumanizeResult:
    """ONE copy-edit call over the flagged sentences; never writes, never mutates `payload`.

    g37b=True adds the style-lint second lint (CCH-MEDIUM-01): RED style reasons join the
    one call's prompt; both lints re-run on the result; still RED keeps the ORIGINAL (unless
    G37 itself needed the rewrite) and sets medium_eligible=False. G37b never refuses a page."""
    pg = _load_pg()
    call = call or pg.call_claude_cli
    text = "\n\n".join(payload_fields(payload))
    before = lint(text)
    res = HumanizeResult(payload, len(before), len(before))
    g37_needed = score(text) > THRESHOLD
    reasons: list[str] = []
    if g37b:
        try:
            sl = style_lint(payload)
        except Exception:   # G37b never blocks; an unmeasured page stays medium_eligible=None (= not offered)
            g37b = False
        else:
            reasons = sl["reasons"]
            res.medium_eligible, res.style_reasons = sl["green"], reasons
    if not g37_needed and not reasons:
        return res
    flagged = _flagged_sentences(text) if g37_needed else {}
    for s_ in sorted(_style_sentences(payload, reasons) if reasons else ()):
        flagged.setdefault(s_, set()).add("style")
    items = list(flagged)
    res.sentences_flagged = len(items)
    if not items:
        return res
    user = "\n".join(f"{i} [{','.join(sorted(flagged[s]))}] {s}" for i, s in enumerate(items, 1))
    system = VOICE_SPEC + _EDITOR_PROMPT
    if reasons:
        system += " Style-lint also reported (fix those too): " + "; ".join(reasons)[:600]
    try:
        raw, res.usage, res.cost_usd = call(system, user, pg.DEFAULT_MODEL)
        repl = json.loads(pg._strip_code_fences(raw))
    except (ValueError, TypeError) as e:   # unparseable = no replacements
        repl = {}
    except Exception as e:   # CLI/transport failure: loud, not a crash
        if g37_needed:
            res.refused_reason = f"G37:call_failed:{type(e).__name__}"
        else:   # G37b-only: the page stands as written, just not offered to Medium
            res.medium_eligible = False
        return res
    if not isinstance(repl, dict):
        repl = {}
    new_payload = copy.deepcopy(payload)
    for i, old in enumerate(items, 1):
        new = repl.get(str(i))
        slot = next(((c, k) for c, k in _slots(new_payload) if old in c[k]), None)
        if slot is None or _invariant_failure(old, new):
            res.rejected += 1
            continue
        c, k = slot
        c[k] = c[k].replace(old, new.strip(), 1)
        res.accepted += 1
    after_text = "\n\n".join(payload_fields(new_payload))
    res.flags_after = len(lint(after_text))
    if score(after_text) > THRESHOLD:
        res.refused_reason = f"G37:score_after:{score(after_text):.2f}"
        res.flags_after = len(before)
        return res
    if g37b:
        sl2 = style_lint(new_payload)
        res.style_reasons = sl2["reasons"]
        if not sl2["green"]:
            res.medium_eligible = False
            if not g37_needed:   # the rewrite bought nothing for G37 and not for style: keep the original
                res.flags_after = len(before)
                return res
        else:
            res.medium_eligible = True
    res.payload, res.rewritten = new_payload, res.accepted > 0
    return res


# --- blind audit (CCH-HUM-07): a sampled instrument, never a blocker ---------

_JUDGE_SYSTEM = "You are a blind judge."
JUDGE_JOB = "judge"
JUDGE_MAX_TOKENS = 16   # one token: 1, 2 or tie
_JUDGE_ASK = "\n\nWhich text is better written? Answer with exactly one token: 1, 2 or tie."


def _slug_hash(slug: str) -> int:
    return int(hashlib.sha256(slug.encode("utf-8")).hexdigest()[:8], 16)


def audit_sample(slug: str) -> bool:
    """1 page in 10, deterministic on the slug."""
    return _slug_hash(slug) % 10 == 0


def audit_pair(slug: str, original: dict[str, Any], rewritten: dict[str, Any],
               call=None, log_dir: Path | None = None) -> dict[str, Any] | None:
    """ONE blind-judge call on a sampled, rewritten page; appends logs/humanize/audit.jsonl.

    Never raises and never gates: an unreadable answer or a failed call leaves no row.
    """
    if not audit_sample(slug):
        return None
    pg = _load_pg()
    batch = call is None and ab.enabled(JUDGE_JOB)
    call = call or pg.call_claude_cli
    pos = 1 if (_slug_hash(slug) // 10) % 2 == 0 else 2   # original's position, same hash
    a = "\n\n".join(payload_fields(original))
    b = "\n\n".join(payload_fields(rewritten))
    t1, t2 = (a, b) if pos == 1 else (b, a)
    user = f"Text 1:\n{t1}\n\nText 2:\n{t2}{_JUDGE_ASK}"
    try:
        if batch and ab.submit(JUDGE_JOB, ab.cid("aud", slug), {"slug": slug, "pos": pos}, _JUDGE_SYSTEM, user,
                               JUDGE_MAX_TOKENS, pg.DEFAULT_MODEL):
            return None   # instrument: the collector writes the row
        raw, _usage, cost = call(_JUDGE_SYSTEM, user, pg.DEFAULT_MODEL)
        return _write_audit_row(slug, pos, raw, cost, log_dir)
    except Exception:   # noqa: BLE001 -- an instrument, never a blocker
        return None


def _write_audit_row(slug: str, pos: int, raw: str, cost: float, log_dir: Path | None = None) -> dict[str, Any] | None:
    ans = raw.strip().strip(".\"'`").lower()
    if ans not in ("1", "2", "tie"):
        return None
    verdict = "tie" if ans == "tie" else ("original" if int(ans) == pos else "rewrite")
    row = {"ts": datetime.now(timezone.utc).isoformat(), "slug": slug, "original_pos": pos,
           "verdict": verdict, "cost_usd": round(cost, 6)}
    d = log_dir or ROOT / "logs" / "humanize"
    d.mkdir(parents=True, exist_ok=True)
    with (d / "audit.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def collect_batches() -> int:
    """Collector tick: ended judge answers -> the same audit row (cost lives in the api ledger)."""
    def handle(results):
        for _i, (ref, t) in results.items():
            if t:
                _write_audit_row(ref["slug"], ref["pos"], t, 0.0)
    return ab.collect(JUDGE_JOB, handle)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--collect":
        print(f"collected {collect_batches()} batch(es)")
    elif len(sys.argv) > 1 and sys.argv[1] == "--corpus":
        for k, v in corpus_report().items():
            print(f"{k}: {v}")
    else:
        src = Path(sys.argv[1]).read_text(encoding="utf-8") if len(sys.argv) > 1 else sys.stdin.read()
        for f in lint(src):
            print(f"{f.kind}\t{f.sentence[:100]}")
        print(f"score\t{score(src):.2f}")
