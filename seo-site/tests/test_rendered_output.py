"""G34 — the reader never sees the markup. Zero, not "few".

Bought on 2026-08-13: 544 of 782 built pages were serving raw markdown to
readers — 4,272 literal `**bold**` and 1,300 literal `[text](url)` — because a
placeholder is not a valid CommonMark destination, because frontmatter fields
were emitted without any markdown conversion, and because an injected HTML
block with no blank line around it swallowed the markdown that followed it.
Four separate causes, one symptom, and nothing was watching for the symptom.

THE LAW IS ZERO, and the thresholds are literals here (Y49):

  visible HTML text  (outside script/style/code/pre/textarea)
      0 x `**`                     -- emphasis that did not render
      0 x `[label](target)`        -- a link that did not render
      0 x `<<PLACEHOLDER>>`        -- a substitution that did not happen
      0 x `eth.fi`                 -- the writer's typo for ether.fi

  markdown sidecars  (dist/**/index.md, what the answer engines read)
      markdown is the NATIVE syntax there and is legal — Y56: one page, one
      law, but the FORM of the law follows the surface. Only the placeholders
      and the typo are checked.

If a literal `**` is ever genuinely wanted on a page, its place is inside
`<code>`, which this gate does not read — so the law stays zero rather than
acquiring an exception list.

Runs against dist/, like every gate here (Y50: measure the served artifact).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

DIST = Path(__file__).resolve().parent.parent / "dist"

# --- the literals -----------------------------------------------------------
# Zero for everything the pipeline controls.
MAX_PLACEHOLDER = 0
MAX_ETHFI_TYPO = 0

# ...and a NAMED list for anything that cannot be fixed yet, rather than a
# count. A count of "eight" hides which eight; a list fails on anything new and
# shows exactly what is owed. THE LIST MAY ONLY SHRINK —
# `test_the_known_list_is_not_stale` fails when an entry stops occurring, which
# forces its removal instead of letting it harden into folklore.
#
# IT IS EMPTY, AND THAT IS THE POINT. It held eight entries for one afternoon.
# Each had a cause that reading the template could not reveal, and each was
# fixed by hand once the RAW source bytes were read instead of the decoded
# string:
#
#   * emphasis inside a link LABEL splits the text node before the AST repair
#     can see the construct — the ceiling Fable named when specifying it;
#   * `[www.ether.fi](...)` — GFM autolinks the literal inside the label and
#     produces a link nested in a link;
#   * `~$20 x 12 = **~$240/year**` — two tildes pair into STRIKETHROUGH and the
#     resulting <del> swallows the emphasis between them;
#   * a snapshot bullet is split on its FIRST colon into key and value, cutting
#     a `**...**` pair in half, so the colon has to live inside the emphasis.
#
# And the reason three earlier attempts reported "0 applied": the literal-table
# runner inherited the pattern mode's page filter and was only ever shown pages
# that still contained the promo — which these no longer did. The strings were
# right; the file list was empty.
KNOWN = []
KNOWN_SET = {(p, f) for p, f in KNOWN}


def _unknown(hits):
    """Hits that are not on the named list. The list may only shrink."""
    return [(p, f) for p, f in hits if (p, f[:48]) not in KNOWN_SET]

_STRIP = re.compile(r"<(script|style|code|pre|textarea)\b[^>]*>.*?</\1>",
                    re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")
_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|#39|nbsp);")

LITERAL_BOLD = re.compile(r"\*\*[^*\n]{1,80}\*\*")
LITERAL_LINK = re.compile(r"\[[^\]\n]{1,120}\]\((?:https?://|/|<<)[^)\s]{0,160}\)")
PLACEHOLDER = re.compile(r"<<[A-Z][A-Z0-9_]*>>")
ETHFI_TYPO = re.compile(r"\beth\.fi\b", re.I)


def _visible_text(html: str) -> str:
    """What a reader actually sees, with code and scripts removed."""
    body = _STRIP.sub(" ", html)
    text = _TAGS.sub(" ", body)
    return _ENTITY.sub(" ", text)


def _pages():
    if not DIST.is_dir():
        pytest.skip("dist/ not built")
    return sorted(DIST.rglob("index.html"))


def _sidecars():
    if not DIST.is_dir():
        pytest.skip("dist/ not built")
    return sorted(DIST.rglob("index.md"))


def _hits(rx, files, extract):
    out = []
    for f in files:
        text = extract(f.read_text(encoding="utf-8", errors="replace"))
        for m in rx.finditer(text):
            out.append((str(f.parent.relative_to(DIST)), m.group(0)[:70]))
    return out


def _report(name, hits, limit):
    if len(hits) <= limit:
        return
    sample = "; ".join(f"{p}: {frag!r}" for p, frag in hits[:6])
    pytest.fail(f"{len(hits)} x {name} visible to readers (limit {limit}). {sample}")


def _report_new(name, hits):
    """Anything not on the named list is a regression and fails the deploy."""
    new = _unknown(hits)
    if not new:
        return
    sample = "; ".join(f"{p}: {frag!r}" for p, frag in new[:6])
    pytest.fail(f"{len(new)} NEW x {name} visible to readers "
                f"(the {len(KNOWN)} known ones are listed in this file). {sample}")


# --- the reader's copy ------------------------------------------------------

def test_no_literal_bold_reaches_the_reader():
    """`**Cashback:**` printed as characters means a field skipped the markdown
    renderer. It happened on 4,272 places across the site."""
    _report_new("literal ** bold", _hits(LITERAL_BOLD, _pages(), _visible_text))


def test_no_literal_markdown_link_reaches_the_reader():
    """A link the parser could not build is worse than no link: the reader sees
    the URL as punctuation, and the click never happens."""
    _report_new("literal markdown link", _hits(LITERAL_LINK, _pages(), _visible_text))


def test_no_unsubstituted_placeholder_reaches_the_reader():
    """`<<AFFILIATE_BUTTON>>` shipped to 424 pages because the deploy's final
    grep looked for `<<AFFILIATE_URL` only — a blacklist where an allow-list
    was needed."""
    _report("raw placeholder", _hits(PLACEHOLDER, _pages(), _visible_text),
            MAX_PLACEHOLDER)


def test_the_issuer_name_is_not_misspelled():
    """`eth.fi` is a writer typo for ether.fi. The pattern cannot match
    `ether.fi` — after `eth` there comes an `e`, not a dot."""
    assert not ETHFI_TYPO.search("ether.fi Cash is available")   # control
    _report("eth.fi typo", _hits(ETHFI_TYPO, _pages(), _visible_text),
            MAX_ETHFI_TYPO)


# --- the answer engines' copy ----------------------------------------------

def test_sidecars_keep_markdown_but_never_a_placeholder():
    """Markdown is the native syntax of a .md sidecar — bold and links there are
    correct and must NOT be 'fixed'. Only an unsubstituted placeholder or the
    typo is a defect."""
    _report("raw placeholder in a sidecar",
            _hits(PLACEHOLDER, _sidecars(), lambda s: s), MAX_PLACEHOLDER)
    _report("eth.fi typo in a sidecar",
            _hits(ETHFI_TYPO, _sidecars(), lambda s: s), MAX_ETHFI_TYPO)


def test_the_gate_can_actually_see_a_defect():
    """A gate that reports zero because it looks at nothing is the failure mode
    this whole round was about. Prove the extractor keeps prose and drops code."""
    html = ("<html><body><p>Rate is **3 %** and [see](/faq/x/).</p>"
            "<code>**not a defect**</code>"
            "<script>var x = '<<AFFILIATE_URL>>';</script></body></html>")
    text = _visible_text(html)
    assert LITERAL_BOLD.search(text), "extractor lost the visible bold"
    assert LITERAL_LINK.search(text), "extractor lost the visible link"
    assert "not a defect" not in text, "extractor kept <code> content"
    assert not PLACEHOLDER.search(text), "extractor kept <script> content"


def test_the_known_list_is_not_stale():
    """Every entry must still occur. When one stops, delete it — the list is a
    debt register, not a permanent exemption, and a stale entry would quietly
    widen the gate."""
    hits = {(p, f[:48]) for p, f in
            _hits(LITERAL_BOLD, _pages(), _visible_text)
            + _hits(LITERAL_LINK, _pages(), _visible_text)}
    gone = [e for e in KNOWN_SET if e not in hits]
    assert not gone, (
        f"{len(gone)} KNOWN entries no longer occur and must be removed from "
        f"the list: {gone[:4]}")
