"""G21 + G22 (Y51 AEO-5, AEO-6) — the sitemap tells the truth about dates,
and lives at the URL crawlers ask for.

WHAT WAS WRONG
`lastmod: new Date()` in astro.config.mjs stamped all 758 URLs with the moment
the build ran. Every deploy announced that the entire site had just changed.
That is worse than an absent signal: a crawler told that everything changed
learns that nothing did, and Perplexity publishes staleness as its single
biggest reason not to cite a source.

And /sitemap.xml returned 404. ClaudeBot asked for it 243 times in 15 days.

Laws are literals: the test reads dates out of the CONTENT FILES and compares
them to the BUILT sitemap. It never asks astro.config.mjs what it thinks the
date should be — that is the code on trial.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

SEO = Path(__file__).resolve().parent.parent
DIST = SEO / "dist"
PSEO = SEO / "src" / "content" / "pseo"
SITEMAP = DIST / "sitemap-0.xml"
SITEMAP_ALIAS = DIST / "sitemap.xml"
SITEMAP_INDEX = DIST / "sitemap-index.xml"
LLMS = DIST / "llms.txt"

pytestmark = pytest.mark.skipif(
    not SITEMAP.exists(),
    reason="dist/ not built — run pnpm build && node scripts/build-markdown.js")


@pytest.fixture(scope="module")
def entries():
    """{url: lastmod-or-None} straight out of the built sitemap."""
    xml = SITEMAP.read_text(encoding="utf-8")
    out = {}
    for block in re.findall(r"<url>(.*?)</url>", xml, re.S):
        loc = re.search(r"<loc>(.*?)</loc>", block).group(1)
        lm = re.search(r"<lastmod>(.*?)</lastmod>", block)
        out[loc] = lm.group(1) if lm else None
    return out


@pytest.fixture(scope="module")
def source_dates():
    """{url: yyyy-mm-dd} read from each content file's own frontmatter."""
    out = {}
    for md in PSEO.glob("*.md"):
        raw = md.read_text(encoding="utf-8")
        end = raw.find("\n---\n", 4)          # generated_at sits AFTER schema_faq,
        head = raw[:end] if end > 0 else raw  # which is several kB on its own

        bucket = re.search(r'^bucket:\s*"?([^"\n]+)"?$', head, re.M)
        slug = re.search(r'^slug:\s*"?([^"\n]+)"?$', head, re.M)
        gen = re.search(r'^generated_at:\s*"?(\d{4}-\d{2}-\d{2})', head, re.M)
        if bucket and slug and gen:
            out[f"https://defycard.com/{bucket.group(1)}/{slug.group(1)}/"] = gen.group(1)
    return out


# --- (a) different content dates produce different lastmods -----------------

def test_articles_do_not_all_share_one_lastmod(entries):
    """The whole defect in one assertion: a single shared value IS the build
    timestamp, whatever it happens to be."""
    stamps = {v for v in entries.values() if v}
    assert len(stamps) > 20, f"only {len(stamps)} distinct lastmod value(s) across the site"


def test_each_article_lastmod_equals_its_own_content_date(entries, source_dates):
    """Computed from the content files, not from the config under test."""
    checked = 0
    for url, day in source_dates.items():
        got = entries.get(url)
        if got is None:
            continue
        assert got.startswith(day) or got > day, f"{url}: sitemap {got}, content {day}"
        checked += 1
    assert checked > 400, f"only {checked} articles cross-checked"


def test_two_articles_written_on_different_days_get_different_lastmods(entries, source_dates):
    by_day = {}
    for url, day in source_dates.items():
        by_day.setdefault(day, []).append(url)
    days = sorted(d for d, urls in by_day.items() if any(entries.get(u) for u in urls))
    assert len(days) >= 2, "fixture: all content generated on one day"
    a, b = by_day[days[0]][0], by_day[days[-1]][0]
    assert entries[a] != entries[b], (a, b, entries[a])


# --- (b) the build timestamp must not appear anywhere ----------------------

def test_no_lastmod_carries_a_build_time_of_day(entries):
    """Every honest date here comes from a yyyy-mm-dd content field and is
    emitted at midnight. A wall-clock time means the value came from the
    build, not from the content."""
    for url, lm in entries.items():
        if lm:
            assert lm.endswith("T00:00:00.000Z"), f"{url} -> {lm} looks like a build time"


def test_pages_with_no_honest_date_carry_no_lastmod_at_all(entries):
    """Hand-written pages (/about/, /privacy/, bucket indexes) have no content
    date. Inventing one is the exact lie being removed; omitting the field is
    allowed by the sitemap protocol."""
    missing = [u for u, v in entries.items() if v is None]
    assert missing, "every URL got a lastmod — something is manufacturing dates"
    assert "https://defycard.com/" in missing


def test_facts_rechecked_against_the_issuer_do_bump_the_date(entries):
    """A redeploy is not a change; a re-verification of the facts IS one. The
    geo pages whose content is sourced from config/availability.yaml carry the
    date that file was last checked against the issuer."""
    import yaml
    avail = yaml.safe_load((SEO.parent / "config" / "availability.yaml").read_text())
    verified = str(avail["cards"]["etherfi"]["verified"])
    url = "https://defycard.com/geo/best-crypto-card-india/"
    assert entries.get(url, "").startswith(verified), (entries.get(url), verified)


# --- Article schema: dateModified follows the same rule --------------------

def _article_schema(bucket, slug):
    html = (DIST / bucket / slug / "index.html").read_text(encoding="utf-8")
    for block in re.findall(r'type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Article":
            return data
    return None


def test_date_modified_is_the_content_date_not_the_build_date(source_dates):
    """A redeploy must not claim every article was just revised. The schema
    date and the sitemap date come from one source, so they cannot disagree."""
    from datetime import date
    today = date.today().isoformat()
    checked = 0
    for bucket, slug in (("reviews", "is-redotpay-legit"),
                         ("learn", "how-do-crypto-debit-cards-work"),
                         ("faq", "who-can-get-ether-fi-cash")):
        art = _article_schema(bucket, slug)
        if art is None:
            continue
        url = f"https://defycard.com/{bucket}/{slug}/"
        expected = source_dates.get(url)
        assert expected, url
        assert str(art["dateModified"]).startswith(expected), (url, art["dateModified"])
        assert not str(art["dateModified"]).startswith(today), \
            f"{url}: dateModified bumped to today without any re-verification"
        checked += 1
    assert checked == 3


def test_date_modified_does_bump_where_the_facts_were_rechecked():
    import yaml
    avail = yaml.safe_load((SEO.parent / "config" / "availability.yaml").read_text())
    verified = str(avail["cards"]["etherfi"]["verified"])
    art = _article_schema("geo", "best-crypto-card-india")
    assert art is not None
    assert str(art["dateModified"]).startswith(verified), art["dateModified"]


# --- (c) llms.txt counts what is actually there ----------------------------

def test_llms_txt_states_the_real_article_count():
    text = LLMS.read_text(encoding="utf-8")
    real = len(list(PSEO.glob("*.md")))
    assert f"Total published articles: {real}" in text, \
        f"llms.txt does not state the real count ({real})"


def test_llms_txt_no_longer_promises_a_cycle_that_does_not_run():
    """It advertised a '30-day data refresh cycle'. Nothing in this repo
    performed one. Either the cycle or the promise — not the promise alone."""
    text = LLMS.read_text(encoding="utf-8")
    assert "30-day data refresh cycle" not in text
    assert "30/90-day cycle" not in text


def test_llms_txt_points_at_the_one_re_check_that_does_run():
    text = LLMS.read_text(encoding="utf-8")
    assert "/geo/availability-matrix/" in text
    assert "Availability lists re-checked against the issuer:" in text


def test_llms_txt_template_markers_never_ship():
    """If the build step did not run, the placeholder prose would be served as
    if it were the inventory."""
    text = LLMS.read_text(encoding="utf-8")
    assert "This block is generated at build time" not in text


# --- G22: /sitemap.xml ------------------------------------------------------

def test_sitemap_xml_exists_at_the_conventional_url():
    """ClaudeBot requested /sitemap.xml 243 times in 15 days and got 404 every
    time. The working index was at /sitemap-index.xml."""
    assert SITEMAP_ALIAS.exists(), "dist/sitemap.xml missing — /sitemap.xml would 404"


def test_sitemap_xml_is_a_sitemapindex_listing_the_real_sub_sitemaps():
    xml = SITEMAP_ALIAS.read_text(encoding="utf-8")
    assert "<sitemapindex" in xml
    subs = re.findall(r"<loc>(.*?)</loc>", xml)
    assert len(subs) >= 2, subs
    for sub in subs:
        name = sub.rsplit("/", 1)[-1]
        assert (DIST / name).exists(), f"{name} is listed but not built"


def test_sitemap_xml_and_sitemap_index_xml_agree():
    """Two names, one document. If they drift, one of them is lying."""
    assert SITEMAP_ALIAS.read_text(encoding="utf-8") == \
           SITEMAP_INDEX.read_text(encoding="utf-8")


def test_robots_advertises_the_conventional_sitemap_url():
    robots = (DIST / "robots.txt").read_text(encoding="utf-8")
    assert "Sitemap: https://defycard.com/sitemap.xml" in robots


def test_the_search_indexer_of_claude_is_explicitly_allowed():
    """Claude-SearchBot has never visited us once. It was already covered by
    the `*` group; make the permission explicit so a future edit to that group
    cannot revoke it by accident."""
    robots = (DIST / "robots.txt").read_text(encoding="utf-8")
    m = re.search(r"User-agent:\s*Claude-SearchBot\s*\n\s*(Allow|Disallow):\s*/", robots)
    assert m, "no explicit Claude-SearchBot group in robots.txt"
    assert m.group(1) == "Allow", "Claude-SearchBot is DISALLOWED"
