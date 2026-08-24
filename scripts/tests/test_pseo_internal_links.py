"""G21 — the link validator is ON the write path, not merely present.

Y53, the lesson that keeps arriving: a correct helper nobody calls is still a
broken pipeline. The prompt change alone cannot hold — a prompt supplies, it
does not guarantee — so what must be pinned is the CALL, and the mutation this
gate exists to catch is "validator written, never invoked".
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "pseo-generate.py"
_SPEC = importlib.util.spec_from_file_location("pseo_generate", _PATH)
pg = importlib.util.module_from_spec(_SPEC)
sys.modules["pseo_generate"] = pg
try:
    _SPEC.loader.exec_module(pg)  # type: ignore[union-attr]
except SystemExit:
    pass


# --- the validator's own law ------------------------------------------------

def test_invented_links_are_stripped_and_anchor_text_survives():
    existing = {"/how-to/how-to-get-a-crypto-card/", "/compare/"}
    body = ("Read [the guide](/how-to/how-to-get-a-crypto-card/), then "
            "[our review](/reviews/does-not-exist/) and [compare](/compare/).")
    out, dropped = pg.validate_internal_links(body, existing)

    assert dropped == 1
    assert "/reviews/does-not-exist/" not in out
    assert "our review" in out, "the sentence lost its words, not just its link"
    assert "/how-to/how-to-get-a-crypto-card/" in out
    assert "/compare/" in out, "a bucket index is a real page and must survive"


def test_angle_bracket_and_slashless_forms_are_not_a_loophole():
    """Both forms are legal Markdown and the writer emits both; a pattern that
    misses either leaves dead links unchecked — five and then two of them did
    exactly that before this test existed."""
    existing = {"/how-to/how-to-get-a-crypto-card/"}
    out, dropped = pg.validate_internal_links(
        "See [a](</education/what-is-self-custody/>) and [b](/how-to/get-a-crypto-card).",
        existing)
    assert dropped == 2, "angle-bracket or slashless target slipped through"
    assert "/education/" not in out


# --- the wiring, which is the point -----------------------------------------

def test_write_page_calls_the_validator(monkeypatch):
    seen: dict[str, object] = {}

    def _spy(body, existing):
        seen["called"] = True
        seen["body"] = body
        return body, 0

    monkeypatch.setattr(pg, "validate_internal_links", _spy)

    class _KW:
        keyword = "best crypto card test"
        bucket = next(iter(pg.BUCKET_META))
        volume = 10
        intent = "commercial"

    class _Cluster:
        primary = _KW()
        supporting: list = []

    class _Res:
        cluster = _Cluster()
        payload = {"body_markdown": "x [dead](/reviews/does-not-exist/) y"}
        usage: dict = {}

    # write_page fails further down on the deliberately minimal payload — that
    # is irrelevant and deliberate: the only claim under test is that the
    # validator was REACHED before anything else could go wrong.
    try:
        pg.write_page(_Res(), [])
    except Exception:
        pass

    assert seen.get("called"), (
        "write_page did not call validate_internal_links — the validator exists "
        "but nothing on the write path invokes it"
    )


def test_existing_paths_includes_bucket_indexes():
    """Bucket indexes are a dynamic Astro route and cannot be found by walking
    filenames; missing them made the validator delete 14 working links."""
    paths = pg.existing_paths()
    for seg in ("compare", "reviews", "how-to"):
        assert f"/{seg}/" in paths, f"/{seg}/ is a real page but the validator does not know it"


def test_the_poisoned_prompt_line_is_gone():
    src = _PATH.read_text(encoding="utf-8")
    assert "plausible slugs" not in src
    assert "never 404" not in src
    assert "Internal link candidates" in src, "the replacement block is missing"

# --- the href the validator never looked at --------------------------------
# The first page of the FAQ stream published with two links written as
# `[anchor](link)` — the writer emitting the word "link" as a placeholder. The
# original pattern matched only targets beginning with "/", so a malformed href
# was not a dead target it rejected; it was a target it never examined, and it
# reached the reader as <a href="link">. A validator that inspects only the
# shapes it understands reports success on exactly the inputs it cannot handle.

def test_a_placeholder_href_is_stripped():
    body = "See [the eligibility rules](link) before applying."
    out, dropped = pg.validate_internal_links(body, {"/faq/x/"})
    assert dropped == 1
    assert "](link)" not in out
    assert "the eligibility rules" in out       # the sentence survives


def test_every_href_shape_is_decided_explicitly():
    """Allow-list, not blacklist: anything not explicitly permitted goes."""
    existing = {"/faq/does-ether-fi-cash-work-in-usa/"}
    keep = [
        "[live](/faq/does-ether-fi-cash-work-in-usa/)",
        "[issuer](https://help.ether.fi/en/articles/262374)",
        "[cta](<<AFFILIATE_URL>>)",
        "[jump](#fees)",
        "[write](mailto:hi@defycard.com)",
    ]
    for body in keep:
        out, dropped = pg.validate_internal_links(body, existing)
        assert dropped == 0, body
        assert out == body

    # Y68 moved the button out of the keep list: it is a block component, not
    # a URL. Normalisation converts it earlier; if one still reaches here, the
    # link goes.
    drop = ["[x](link)", "[x](/faq/not-a-page/)", "[x](url)", "[x](TBD)",
            "[x](page.html)", "[x](../relative/)", "[x](<<AFFILIATE_BUTTON>>)"]
    for body in drop:
        out, dropped = pg.validate_internal_links(body, existing)
        assert dropped == 1, body
        assert "](" not in out, body

# --- Y68: only the affiliate URL may stand in an href ----------------------
# `<<AFFILIATE_BUTTON>>` is a block CTA component and `<<IMAGE_0>>` is a
# picture. The first allow-list accepted any `^<<[A-Z0-9_]+>>$`, so a writer
# putting the button in a link target sailed through — and substitute_placeholders
# then expanded a whole `<p class="article-cta">` block inside `](...)`, in the
# source file, on 135 pages.

def test_only_the_affiliate_url_placeholder_may_be_an_href():
    existing = {"/faq/x/"}
    for body in ("[cta](<<AFFILIATE_URL>>)", "[cta](<<AFFILIATE_URL:etherfi>>)"):
        out, dropped = pg.validate_internal_links(body, existing)
        assert dropped == 0 and out == body, body
    for body in ("[btn](<<AFFILIATE_BUTTON>>)", "[img](<<IMAGE_0>>)",
                 "[x](<<ANYTHING>>)"):
        out, dropped = pg.validate_internal_links(body, existing)
        assert dropped == 1, body
        assert "](" not in out, body


def test_a_button_in_a_link_target_is_normalised_before_substitution():
    """Order matters: substitute_placeholders expands the button with a blind
    str.replace, so normalisation that ran after it would be repairing HTML
    instead of preventing it."""
    out, n = pg.normalize_placeholders("[Sign up](<<AFFILIATE_BUTTON>>) today.")
    assert n == 1
    assert out == "[Sign up](<<AFFILIATE_URL>>) today."


def test_a_bare_button_in_a_frontmatter_field_becomes_a_link():
    """Frontmatter fields get no placeholder substitution at all, so a button
    there reached the reader raw — measured on 424 built pages."""
    out, n = pg.normalize_field("Ready to start? <<AFFILIATE_BUTTON>>")
    assert n == 1
    assert "<<AFFILIATE_BUTTON>>" not in out
    assert "](<<AFFILIATE_URL>>)" in out


def test_a_field_without_the_button_is_returned_untouched():
    for text in ("**Cashback:** up to 3%", "plain text", "[a](/faq/x/)"):
        out, n = pg.normalize_field(text)
        assert n == 0 and out == text
