#!/usr/bin/env python3
"""corpus_edit.py — literal, ceilinged table edits across the pSEO corpus.

WHY THIS EXISTS
---------------
`fix-expired-promo.py` grew a `--table` mode that applies hand-written literal
replacements to every page. It works, but it had no ceiling: on 2026-08-13 a
pattern-mode regex proposed 15,176 edits on a nine-item job and stripped the
spaces around 20,411 emphasis markers in 542 files. The rule bought with that
damage (`.claude/CLAUDE.md`, "THE ACCIDENT"): *any corpus-wide script must
declare how many edits it expects and refuse above that.*

So the table applier lives here, once, with the ceiling attached to it, and
`fix-expired-promo.py` imports it. One implementation — the project has already
paid 15 days for four copies of `slugify()`.

WHAT IT GUARANTEES
------------------
* **Literal, never regex.** Raw bytes in, raw bytes out. Nothing can span a
  structural boundary because nothing is interpreted.
* **Both serializations, always.** A sentence lives twice in a page: once as
  prose and once inside a one-line JSON frontmatter value where `–` is
  `\\u2013`. Each entry is applied in BOTH forms, not just whichever matched
  first — replacing only one of the two leaves the page contradicting itself.
* **`--expect` is mandatory on the CLI.** `|changed - expect| > tolerance`
  refuses and writes nothing.
* **An entry that matched nothing is a failure, not a note.** A typo'd literal
  is a silent no-op, which is exactly how a money claim survives a "successful"
  repair run.
* **Dry-run is the default.** `--write` is required to touch anything.

Usage:
    corpus_edit.py --table scripts/tables/affiliate-2026-09.tsv --expect 15 --diff
    corpus_edit.py --table scripts/tables/affiliate-2026-09.tsv --expect 15 --write
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "seo-site" / "src" / "content" / "pseo"

TODAY = date.today().isoformat()


_ESCAPES = {"n": "\n", "\\": "\\"}


def unescape(s: str) -> str:
    r"""`\n` -> newline, `\\` -> backslash. Nothing else is an escape.

    A TSV line cannot hold a real newline, and some replacements are whole
    markdown bullets (E-08 deletes a `- **Hungary**` line). Every other
    backslash sequence is returned untouched, so the `—` that the
    frontmatter writer puts in a one-line JSON value still means those six
    literal bytes.
    """
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(0)), s)


def load_table(path: Path) -> list[tuple[str, str]]:
    """Hand-written exact replacements, one `old<TAB>new` pair per line.

    Order is preserved: longer, more specific entries must come first in the
    file so a shorter one cannot consume part of a sentence a longer one was
    written for.
    """
    out: list[tuple[str, str]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            print(f"[table] line {i}: no tab, skipped", file=sys.stderr)
            continue
        out.append((unescape(parts[0]), unescape(parts[1])))
    return out


def page_path_from_text(text: str, stem: str) -> str | None:
    """`/bucket/slug/` for a pSEO source file — bucket from frontmatter, slug
    from the filename, the same convention aeo-aggregate.py's article_paths()
    uses to name a page in the fetch logs. Lives here so the scorer
    (intel-ingest) and the router (intel-route) cannot drift on what a page's
    path is; `text` is passed in because both callers have already read it."""
    for line in text.splitlines()[:40]:
        if line.startswith("bucket:"):
            bucket = line.split(":", 1)[1].strip().strip("\"'")
            return f"/{bucket}/{stem}/" if bucket else None
    return None


def serializations(old: str, new: str) -> list[tuple[str, str]]:
    """The same replacement as raw prose and as a JSON string body.

    `json.dumps(s)[1:-1]` is exactly how the frontmatter writer encodes a
    one-line value: non-ASCII becomes `\\uXXXX`, quotes become `\\"`. For a pure
    ASCII entry the two forms are identical and only one is returned, otherwise
    the replacement would be counted twice.
    """
    esc_old, esc_new = json.dumps(old)[1:-1], json.dumps(new)[1:-1]
    if esc_old == old:
        return [(old, new)]
    return [(old, new), (esc_old, esc_new)]


def add_verified(text: str) -> tuple[str, bool]:
    """Give a corrected page the visible check date it never had.

    Only pages this run actually corrected get one: a check really did happen on
    them. Back-filling dates onto untouched pages would manufacture freshness,
    which is the very thing the visible date exists to prevent.
    """
    m = re.search(r'^(snapshot_bullets:\s*)(\[.*\])\s*$', text, re.M)
    if not m:
        return text, False
    try:
        bullets = json.loads(m.group(2))
    except json.JSONDecodeError:
        return text, False
    if not isinstance(bullets, list):
        return text, False
    bullets.append(f"**Last verified:** {TODAY}")
    return text[:m.start()] + m.group(1) + json.dumps(bullets) + text[m.end():], True


# Three spellings are in the corpus: `**Last verified:**`, `**Last verified**:`
# and the bare `Last verified:`. Matching only the first two left the third kind
# of page un-bumped and then `add_verified` appended a SECOND bullet next to the
# stale one — two dates on one page, the older of them wrong.
_VERIFIED_RE = re.compile(
    r"((?:\*\*)?Last verified(?::\*\*|\*\*:|:)\s*)\d{4}-\d{2}-\d{2}")


def bump_verified(text: str) -> tuple[str, bool]:
    """The visible check date, only on pages this run actually corrected."""
    new, n = _VERIFIED_RE.subn(r"\g<1>" + TODAY, text)
    return new, n > 0


# TWO spellings are in the corpus, measured 2026-09-14: 563 pages carry
# `generated_at: "<iso>"` and the ten pages printed on 2026-05-05 carry it
# UNQUOTED. This is `Last verified`'s three-spellings defect on a second
# field — a quoted-only pattern leaves those ten pages with a stale
# dateModified after a real edit, which is the one thing this stamp exists to
# prevent. Matching the whole line handles both; the rewrite always emits the
# quoted form, which is what the generator itself writes today.
_GENERATED_AT_RE = re.compile(r"^generated_at:.*$", re.M)


def bump_generated_at(text: str) -> tuple[str, bool]:
    """The page's machine change date — `generated_at` is what
    `seo-site/src/lib/freshness.mjs` turns into the sitemap's `lastmod` and
    into `schema_article.dateModified`. Opt-in (`apply_table(...,
    bump_generated=True)`), never automatic: the ⛔ rule is that this date
    follows a REAL edit to the page's content, so only a caller that knows the
    replacement it is making is a content change may ask for it. It is applied
    inside the per-file loop, after the file is already known to have
    changed — an untouched page can never receive one."""
    stamp = datetime.now(timezone.utc).isoformat()
    new, n = _GENERATED_AT_RE.subn(f'generated_at: "{stamp}"', text)
    return new, n > 0


_WHAT_TO_WATCH_RE = re.compile(r'^(what_to_watch:\s*)(\[.*\])\s*$', re.M)


def append_what_to_watch(text: str, line: str) -> tuple[str, bool]:
    """Append one line to a page's `what_to_watch` frontmatter array.

    Parsed, never regexed onto raw text (the fix-expired-promo rule: a
    one-line JSON frontmatter value is `json.loads`-ed, edited as data, and
    `json.dumps`-ed back — a greedy text edit on this shape is what broke 5
    pages in the 08-13 accident). A no-op (missing field, unparsable value, or
    the exact line already present) returns the text unchanged."""
    m = _WHAT_TO_WATCH_RE.search(text)
    if not m:
        return text, False
    try:
        items = json.loads(m.group(2))
    except json.JSONDecodeError:
        return text, False
    if not isinstance(items, list) or line in items:
        return text, False
    items.append(line)
    return (text[:m.start()] + m.group(1) + json.dumps(items, ensure_ascii=False)
            + text[m.end():]), True


def load_watch_table(path: Path) -> list[tuple[str, str]]:
    """`slug<TAB>line` pairs — one `what_to_watch` bullet to append to one
    page. `slug` is the page's own filename stem (`<CONTENT>/<slug>.md`),
    never re-derived (the slugify() rule: match the writer's own path, don't
    recompute one to compare)."""
    out: list[tuple[str, str]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t", 1)
        if len(parts) < 2:
            print(f"[watch-table] line {i}: no tab, skipped", file=sys.stderr)
            continue
        out.append((parts[0].strip(), unescape(parts[1])))
    return out


def apply_watch_table(content_dir: Path, pairs: list[tuple[str, str]],
                       write: bool = False, expect: int | None = None,
                       tolerance: int | None = None,
                       bump_generated: bool = False) -> dict:
    """Append one `what_to_watch` bullet per (slug, line) pair, same
    ceiling discipline as `apply_table`: compute everything, then refuse
    above `expect`±`tolerance`, then (only then) write. A pair whose file is
    missing or whose `append_what_to_watch` is a no-op (line already present,
    field missing) is a refusal, not a silent skip — same law as
    `apply_table`'s `unused` entries."""
    stat: dict = {"files": 0, "refused": None, "changed": [], "diffs": [],
                  "generated_bumped": 0, "noop": []}
    pending: list[tuple[Path, str, str]] = []

    for slug, line in pairs:
        f = content_dir / f"{slug}.md"
        if not f.exists():
            stat["noop"].append(f"{slug}: no such file")
            continue
        before = f.read_text(encoding="utf-8")
        after, changed = append_what_to_watch(before, line)
        if not changed:
            stat["noop"].append(f"{slug}: append was a no-op")
            continue
        if bump_generated:
            after, gen_bumped = bump_generated_at(after)
            stat["generated_bumped"] += int(gen_bumped)
        pending.append((f, before, after))

    stat["files"] = len(pending)
    stat["changed"] = sorted(f.stem for f, _, _ in pending)

    if expect is not None:
        tol = tolerance if tolerance is not None else max(2, round(expect * 0.1))
        if abs(len(pending) - expect) > tol:
            stat["refused"] = (f"expected {expect}±{tol} files, "
                               f"the watch-table changes {len(pending)}")
            return stat
        if stat["noop"]:
            stat["refused"] = f"{len(stat['noop'])} watch-table entries did not apply"
            return stat

    if write:
        for f, _, after in pending:
            f.write_text(after, encoding="utf-8")
    else:
        stat["diffs"] = ["\n".join(difflib.unified_diff(
            b.split("\n"), a.split("\n"), fromfile=f"a/{f.name}",
            tofile=f"b/{f.name}", lineterm="", n=1)) for f, b, a in pending]
    return stat


def apply_table(files: list[Path], table: list[tuple[str, str]],
                write: bool = False, expect: int | None = None,
                tolerance: int | None = None,
                bump_generated: bool = False) -> dict:
    """Apply exact replacements. Compute everything, THEN decide to write."""
    stat: dict = {"applied": 0, "files": 0, "unused": [], "ambiguous": [],
                  "dated": 0, "refused": None, "changed": [], "diffs": [],
                  "generated_bumped": 0}
    used: set[str] = set()
    pending: list[tuple[Path, str, str]] = []

    for f in files:
        before = f.read_text(encoding="utf-8")
        after = before
        for old_raw, new_raw in table:
            for old, new in serializations(old_raw, new_raw):
                n = after.count(old)
                if n == 0:
                    continue
                if n > 1:
                    # Two identical sentences in one file are usually the page
                    # body and its JSON-LD twin, and both should change — but
                    # "usually" is not a licence, so it is reported.
                    stat["ambiguous"].append((f.stem, old[:60], n))
                after = after.replace(old, new)
                stat["applied"] += n
                used.add(old_raw)
        if after == before:
            continue
        after, bumped = bump_verified(after)
        if not bumped:
            after, added = add_verified(after)
            stat["dated"] += int(added)
        else:
            stat["dated"] += 1
        if bump_generated:
            after, gen_bumped = bump_generated_at(after)
            stat["generated_bumped"] += int(gen_bumped)
        pending.append((f, before, after))

    stat["unused"] = [o[:70] for o, _ in table if o not in used]
    stat["files"] = len(pending)
    stat["changed"] = sorted(f.stem for f, _, _ in pending)

    if expect is not None:
        tol = tolerance if tolerance is not None else max(2, round(expect * 0.1))
        if abs(len(pending) - expect) > tol:
            stat["refused"] = (f"expected {expect}±{tol} files, "
                               f"the table changes {len(pending)}")
            return stat
        if stat["unused"]:
            # Refusal, not a warning, and BEFORE the write: a literal that
            # matches nothing is a typo. Letting the run "succeed" is how a
            # money claim survives its own repair.
            stat["refused"] = (f"{len(stat['unused'])} table entries matched "
                               f"nothing")
            return stat

    if write:
        for f, _, after in pending:
            f.write_text(after, encoding="utf-8")
    else:
        stat["diffs"] = ["\n".join(difflib.unified_diff(
            b.split("\n"), a.split("\n"), fromfile=f"a/{f.name}",
            tofile=f"b/{f.name}", lineterm="", n=1)) for f, b, a in pending]
    return stat


def main() -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--table", type=Path,
                       help="TSV of literal old<TAB>new replacements")
    mode.add_argument("--watch-table", type=Path,
                       help="TSV of slug<TAB>line, appended to that page's "
                            "what_to_watch array")
    ap.add_argument("--expect", required=True, type=int,
                    help="how many FILES this table is supposed to change")
    ap.add_argument("--tolerance", type=int, default=None,
                    help="default: max(2, 10%% of --expect)")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--bump-generated", action="store_true",
                    help="also move `generated_at` (the page's dateModified "
                         "and sitemap lastmod) on the pages this run really "
                         "changed. Off by default: the date follows a real "
                         "content edit, so only a caller that knows its "
                         "replacement is one may ask.")
    ap.add_argument("--diff", action="store_true")
    ap.add_argument("--content", type=Path, default=CONTENT,
                    help="corpus directory (a tmp copy, when rehearsing)")
    a = ap.parse_args()

    if a.watch_table is not None:
        pairs = load_watch_table(a.watch_table)
        print(f"watch-table: {len(pairs)} bullets over {a.content} "
              f"({'WRITE' if a.write else 'dry-run'})")
        st = apply_watch_table(a.content, pairs, a.write, a.expect,
                               a.tolerance, a.bump_generated)
        print(f"  files changed        : {st['files']}"
              f"   generated_at bumped: {st['generated_bumped']}")
        for stem in st["changed"]:
            print(f"     · {stem}")
        if a.diff:
            for d in st["diffs"]:
                print(d)
        rc = 0
        if st["noop"] and not st["refused"]:
            for msg in st["noop"]:
                print(f"     · {msg}")
        if st["refused"]:
            print(f"  REFUSED: {st['refused']} — nothing written.")
            for msg in st["noop"]:
                print(f"     · {msg}")
            rc = 1
        return rc

    table = load_table(a.table)
    files = sorted(a.content.glob("*.md"))
    print(f"table: {len(table)} literal replacements over {len(files)} pages "
          f"({'WRITE' if a.write else 'dry-run'})")

    st = apply_table(files, table, a.write, a.expect, a.tolerance,
                     a.bump_generated)
    print(f"  replacements applied : {st['applied']}")
    print(f"  files changed        : {st['files']}   dated: {st['dated']}"
          f"   generated_at bumped: {st['generated_bumped']}")
    for stem in st["changed"]:
        print(f"     · {stem}")
    if st["ambiguous"]:
        print(f"  appeared more than once (every copy replaced): "
              f"{len(st['ambiguous'])}")
    if a.diff:
        for d in st["diffs"]:
            print(d)

    rc = 0
    if st["unused"]:
        print(f"  REFUSED: {len(st['unused'])} table entries matched nothing — "
              f"a literal that matches nothing is a typo, not a no-op:")
        for frag in st["unused"]:
            print(f"     · {frag}")
        rc = 1
    if st["refused"]:
        print(f"  REFUSED: {st['refused']} — nothing written.")
        rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
