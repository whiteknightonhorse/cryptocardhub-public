"""G12 (Y50 P0-1) — no reader is sent to a card they cannot be issued.

Runs against the BUILT site (seo-site/dist), not the sources. The bug this
gate exists to catch lived between the markdown and the HTML: the page text
said "ether.fi is not available in India" while the layout and the CTA
placeholders still emitted ether.fi as the page's only external card link.
Only the rendered HTML shows that.

THE LAWS ARE LITERALS HERE ON PURPOSE (lesson Y49): if this test imported the
slug list from config/availability.yaml or the host list from affiliates.yaml,
then deleting an entry there would move the test with the mutation and the
gate would stay green. Literals mean a change to the config must be made
deliberately in two places.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

DIST = Path(__file__).resolve().parent.parent / "dist"

# LAW 1 — jurisdictions where ether.fi Cash cannot be issued, verified
# 2026-08-11 against help.ether.fi (see config/availability.yaml `source`).
# A page in this list must offer at least one card the reader can actually get.
#
# india and turkey moved OUT of this list on T-0142-15: T-0142.ruling-6
# measured that no card DefyCard tracks (not just ether.fi) can currently be
# issued in either jurisdiction, so "offer an alternative" is not an honest
# law for them any more — see NO_CARD_SLUGS / G47 below for their actual law.
UNAVAILABLE_SLUGS = [
    "geo/best-crypto-card-netherlands",
    "geo/best-crypto-card-georgia",
    "geo/best-crypto-card-arizona",
    "geo/best-crypto-card-nevada",
    "geo/best-crypto-card-washington",
]

# LAW 3 (T-0142-15) — pages where NO card DefyCard tracks can currently be
# issued. These must NOT link any partner/card host at all (LAW 1's "offer an
# alternative" does not apply — there is no alternative) and must route their
# CTA to the availability matrix instead.
NO_CARD_SLUGS = [
    "geo/best-crypto-card-india",
    "geo/best-crypto-card-turkey",
]

# Every card host this project tracks — a superset of REQUIRED_TRACKER_HOSTS,
# used only by G47 to prove a no-card page links NONE of them.
NO_CARD_FORBIDDEN_HOSTS = ["www.ether.fi", "crypto.com", "www.plasma.org"]

NO_CARD_CTA = "/geo/availability-matrix/"

# G47 LAW — the six cards DefyCard tracks and the status each no-card page
# must print for it (Q2, T-0142.ruling-6). Literal here on purpose (Y49): if
# this list imported from the page content it judges, deleting a row from the
# page would move the law with the mutation.
NO_CARD_STATUS_PAIRS = [
    ("ether.fi Cash", "Not available"),
    ("Crypto.com", "Not stated"),
    ("RedotPay", "Not available"),
    ("Wirex", "Not available"),
    ("Bybit Card", "Not stated"),
    ("Plasma One", "Not available"),
]

# LAW 2 — every partner host whose clicks must be counted by register_click.
REQUIRED_TRACKER_HOSTS = ["www.ether.fi", "crypto.com", "www.plasma.org"]

ETHERFI_HOST = "www.ether.fi"

# A control page: ether.fi IS available in Spain, so it must keep ether.fi.
AVAILABLE_SLUG = "geo/best-crypto-card-spain"


def _html(slug: str) -> str:
    page = DIST / slug / "index.html"
    if not page.exists():
        pytest.fail(
            f"{page} missing — build the site first: "
            f"cd seo-site && set -a && . ../.env && set +a && pnpm build"
        )
    return page.read_text(encoding="utf-8")


def _external_card_hosts(html: str) -> set[str]:
    """Hosts of every outbound card-partner link in href attributes."""
    hosts = set()
    for url in re.findall(r'href="(https?://[^"]+)"', html):
        for host in REQUIRED_TRACKER_HOSTS:
            if host in url:
                hosts.add(host)
    return hosts


@pytest.mark.parametrize("slug", UNAVAILABLE_SLUGS)
def test_unavailable_geo_page_offers_an_obtainable_card(slug):
    """ether.fi must not be the ONLY external card link where it is unavailable."""
    hosts = _external_card_hosts(_html(slug))
    assert hosts, f"{slug}: no external partner card link at all"
    alternatives = hosts - {ETHERFI_HOST}
    assert alternatives, (
        f"{slug}: ether.fi is the only external card link, but ether.fi Cash is "
        f"not available in this jurisdiction — every such click is a dead end. "
        f"Found hosts: {sorted(hosts)}"
    )


@pytest.mark.parametrize("slug", UNAVAILABLE_SLUGS)
def test_unavailable_geo_page_cta_is_not_etherfi(slug):
    """The page's primary CTA button must point at the obtainable card."""
    html = _html(slug)
    ctas = re.findall(
        r'<a[^>]+class="[^"]*\bcta\b[^"]*"[^>]+href="(https?://[^"]+)"', html
    ) + re.findall(
        r'<a[^>]+href="(https?://[^"]+)"[^>]*class="[^"]*\bcta\b[^"]*"', html
    )
    external = [u for u in ctas if any(h in u for h in REQUIRED_TRACKER_HOSTS)]
    assert external, f"{slug}: no external CTA button found"
    assert not any(ETHERFI_HOST in u for u in external), (
        f"{slug}: a primary CTA still points at ether.fi, which cannot be "
        f"issued in this jurisdiction: {external}"
    )


def test_available_geo_page_keeps_etherfi():
    """Control: the fix must not strip ether.fi where it IS available."""
    hosts = _external_card_hosts(_html(AVAILABLE_SLUG))
    assert ETHERFI_HOST in hosts, (
        f"{AVAILABLE_SLUG}: ether.fi is available in Spain but is not linked "
        f"— the partner routing over-fired. Found: {sorted(hosts)}"
    )


@pytest.mark.parametrize("host", REQUIRED_TRACKER_HOSTS)
def test_click_tracker_covers_every_partner_host(host):
    """register_click must fire for ALL partners, not just ether.fi.

    Before Y50 the listener matched the single literal 'ether.fi/@defycard',
    so the moment a page linked Crypto.com those conversions became invisible
    and the fix would have looked like a drop in clicks.
    """
    html = _html(UNAVAILABLE_SLUGS[0])
    tracker = re.search(r"partnerHostMap\s*=\s*(\{.*?\})", html, re.S)
    assert tracker, "register_click tracker host map not found in the page"
    assert host in tracker.group(1), (
        f"tracker does not cover partner host {host!r} — clicks to it are "
        f"uncounted. Map: {tracker.group(1)}"
    )


def test_no_unresolved_affiliate_placeholder():
    """Both <<AFFILIATE_URL>> and <<AFFILIATE_URL:slug>> must be substituted."""
    for slug in UNAVAILABLE_SLUGS + NO_CARD_SLUGS + [AVAILABLE_SLUG]:
        assert "<<AFFILIATE_URL" not in _html(slug), f"{slug}: unresolved placeholder"


# ---------------------------------------------------------------------------
# G12-md (Y56) — the same two laws, read off the MARKDOWN SIDECAR.
#
# Why this exists: every assertion above read index.html only. The sidecars
# (index.md, served on Accept: text/markdown and listed in llms.txt) are built
# by a different program — scripts/build-markdown.js — which had its own
# affiliate substitution and never consulted primaryPartnerFor(). Four pages
# therefore carried ether.fi referrals in markdown while their HTML correctly
# routed elsewhere, and this gate stayed green through all of it: it was
# measuring the surface where the bug was not.
#
# The deploy guard does not cover this. It fails on an UNRESOLVED placeholder;
# the money error is a WRONGLY RESOLVED one, and it caught 2 of the 4 pages
# only because those happened to also carry a tagged placeholder.
# ---------------------------------------------------------------------------


def _external_card_hosts_md(md: str) -> set[str]:
    """Same law as _external_card_hosts, but markdown links are (url), not href=".

    Reusing the HTML helper here silently returned an empty set for every page,
    which made the control assertion fail for the wrong reason — the gate would
    have looked red while measuring nothing.
    """
    hosts = set()
    for url in re.findall(r"https?://[^\s)\]\"'<>]+", md):
        for host in REQUIRED_TRACKER_HOSTS:
            if host in url:
                hosts.add(host)
    return hosts


def _markdown(slug: str) -> str:
    page = DIST / slug / "index.md"
    if not page.exists():
        pytest.fail(
            f"{page} missing — the sidecar is a shipped surface, not an "
            f"optional extra; build the site first"
        )
    return page.read_text(encoding="utf-8")


@pytest.mark.parametrize("slug", UNAVAILABLE_SLUGS)
def test_unavailable_geo_sidecar_etherfi_is_not_the_only_card(slug):
    """LAW 1 on the sidecar, same form as the HTML gate above: ether.fi must
    not be the ONLY card path.

    NOT the stricter "no ether.fi referral at all" (verdict 2026-08-12): the
    india source links ether.fi inside its Switzerland section and nevada
    inside its Wyoming section — correctly targeted referrals to readers who
    CAN get the card. The HTML and the sidecar are the same page for
    different readers; one law per page, whatever the serialization. A
    stricter sidecar law would have forced an editorial rewrite that deletes
    real commissions the HTML gate already ruled legitimate.

    Pages whose prose carries no partner link at all pass vacuously —
    asserting one into existence would be a product requirement smuggled in
    as a bug fix.
    """
    hosts = _external_card_hosts_md(_markdown(slug))
    assert hosts != {ETHERFI_HOST}, (
        f"{slug}: ether.fi is the only card link in the markdown sidecar, but "
        f"ether.fi Cash cannot be issued in this jurisdiction — an AI "
        f"assistant quoting this surface has only a dead end to offer."
    )


@pytest.mark.parametrize("slug", UNAVAILABLE_SLUGS)
def test_unavailable_geo_sidecar_cta_is_not_etherfi(slug):
    """LAW 2 on the sidecar: a primary CTA, where present, must not point at
    ether.fi.

    In markdown the CTA block renders as the literal link text
    '[Get your DefyCard →](url)' — the text is the marker, the html class is
    gone. Three of the seven pages carry no CTA in their body prose at all;
    that is allowed (see above), so absence passes vacuously.
    """
    for url in re.findall(
        r"\[Get your DefyCard[^\]]*\]\((https?://[^)]+)\)", _markdown(slug)
    ):
        assert ETHERFI_HOST not in url, (
            f"{slug}: a sidecar CTA points at ether.fi, which cannot be "
            f"issued in this jurisdiction: {url}"
        )


@pytest.mark.parametrize("slug", UNAVAILABLE_SLUGS)
def test_unavailable_geo_sidecar_carries_no_unresolved_placeholder(slug):
    """A literal <<AFFILIATE_URL...>> in shipped markdown is a visible defect."""
    md = _markdown(slug)
    assert "<<AFFILIATE_URL" not in md, (
        f"{slug}: sidecar still contains a literal <<AFFILIATE_URL...>> "
        f"placeholder — the tagged form is resolved by the remark plugin for "
        f"HTML only, so the markdown builder must resolve it itself"
    )


def test_available_geo_sidecar_keeps_etherfi():
    """Control: the fix must not strip ether.fi from markdown where it IS available."""
    hosts = _external_card_hosts_md(_markdown(AVAILABLE_SLUG))
    assert ETHERFI_HOST in hosts, (
        f"{AVAILABLE_SLUG}: ether.fi is available in Spain but the sidecar no "
        f"longer links it — the fix over-corrected and cost a real referral"
    )


# ---------------------------------------------------------------------------
# G47 (T-0142-15) — no-card pages: no reader is sent ANYWHERE, because no card
# DefyCard tracks can currently be issued in india or turkey.
#
# Mutations this gate must catch:
#   revert `primary_partner: cryptocom` on india  -> (a) goes red (a crypto.com
#     href reappears)
#   swap the CTA for an external card link        -> (b) goes red (.cta no
#     longer points at the availability matrix)
# ---------------------------------------------------------------------------


def _no_card_forbidden_hosts_html(html: str) -> set[str]:
    hosts = set()
    for url in re.findall(r'href="(https?://[^"]+)"', html):
        for host in NO_CARD_FORBIDDEN_HOSTS:
            if host in url:
                hosts.add(host)
    return hosts


@pytest.mark.parametrize("slug", NO_CARD_SLUGS)
def test_no_card_page_links_no_tracked_card_host(slug):
    """(a) zero href to any of the three partner/card hosts, zero rel=sponsored."""
    html = _html(slug)
    assert not _no_card_forbidden_hosts_html(html), (
        f"{slug}: a no-card page links a tracked card host — "
        f"{sorted(_no_card_forbidden_hosts_html(html))}"
    )
    assert 'rel="sponsored' not in html, (
        f"{slug}: a no-card page carries rel=\"sponsored\" — there is no "
        f"partner link on this page to sponsor"
    )


@pytest.mark.parametrize("slug", NO_CARD_SLUGS)
def test_no_card_page_cta_points_at_availability_matrix(slug):
    """(b) the primary CTA routes to the availability matrix, not a card."""
    html = _html(slug)
    ctas = re.findall(
        r'<a[^>]+class="[^"]*\bcta\b[^"]*"[^>]+href="([^"]+)"', html
    )
    assert ctas, f"{slug}: no .cta element found"
    assert all(href == NO_CARD_CTA for href in ctas), (
        f"{slug}: .cta href must be exactly {NO_CARD_CTA!r}, found {ctas}"
    )


@pytest.mark.parametrize("slug", NO_CARD_SLUGS)
def test_no_card_page_states_all_six_issuer_statuses(slug):
    """(c) the six tracked cards' status is printed, literally, on the page."""
    html = _html(slug)
    for name, status in NO_CARD_STATUS_PAIRS:
        assert re.search(rf"{re.escape(name)}.*?{re.escape(status)}", html, re.S), (
            f"{slug}: missing status pair {name!r} -> {status!r}"
        )


@pytest.mark.parametrize("slug", NO_CARD_SLUGS)
def test_no_card_page_is_indexable(slug):
    """(d) no-card is an honest answer, not a reason to hide the page."""
    html = _html(slug)
    assert 'name="robots" content="noindex' not in html, (
        f"{slug}: a no-card page must stay indexable — it is the honest answer "
        f"to the query, not a dead end to hide"
    )


@pytest.mark.parametrize("slug", NO_CARD_SLUGS)
def test_no_card_sidecar_links_no_tracked_card_host(slug):
    """(e) the markdown sidecar carries no external card link at all."""
    hosts = _external_card_hosts_md(_markdown(slug))
    assert hosts == set(), (
        f"{slug}: no-card sidecar links a tracked card host — {sorted(hosts)}"
    )


# ---------------------------------------------------------------------------
# G48 (T-0142-16) — `url_fallback` is a property of the PRODUCT, not the
# brand: crypto.com/cards serves two different cards depending on locale
# (US credit card vs EU/NL prepaid card, ruling-6 Q3), so the CTA on each of
# these 5 pages must point at the product actually described on that page,
# not just "some crypto.com URL".
#
# Mutation this gate must catch: revert netherlands' `url_fallback` from
# cryptocom_eu (/nl/cards) back to the shared /cards redirect -> (a) goes red.
# ---------------------------------------------------------------------------

# LAW 4 — literal, per Y49 (never re-derived from config/affiliates.yaml):
# which exact Crypto.com product URL each page's CTA must resolve to.
CTA_HREF_BY_SLUG = {
    "geo/best-crypto-card-netherlands": "https://crypto.com/nl/cards",
    "geo/best-crypto-card-georgia": "https://crypto.com/us/cards",
    "geo/best-crypto-card-arizona": "https://crypto.com/us/cards",
    "geo/best-crypto-card-nevada": "https://crypto.com/us/cards",
    "geo/best-crypto-card-washington": "https://crypto.com/us/cards",
}


@pytest.mark.parametrize("slug,expected", CTA_HREF_BY_SLUG.items())
def test_g48_cta_href_matches_the_products_own_url(slug, expected):
    html = _html(slug)
    ctas = re.findall(
        r'<a[^>]+class="[^"]*\bcta\b[^"]*"[^>]+href="([^"]+)"', html
    )
    assert ctas, f"{slug}: no .cta element found"
    assert all(href == expected for href in ctas), (
        f"{slug}: .cta href must be exactly {expected!r}, found {sorted(set(ctas))}"
    )


@pytest.mark.parametrize("slug,expected", CTA_HREF_BY_SLUG.items())
def test_g48_sidecar_cta_matches_the_products_own_url(slug, expected):
    """Same law, read off the sidecar. A page whose body prose carries no CTA
    link at all (netherlands, same pre-existing exception as G12-md above)
    passes vacuously; where a crypto.com link IS present, it must be this
    exact URL, not the other locale's product."""
    md = _markdown(slug)
    urls = re.findall(r"\]\((https?://crypto\.com[^)]*)\)", md)
    assert all(u == expected for u in urls), (
        f"{slug}: sidecar crypto.com link must be exactly {expected!r}, found {sorted(set(urls))}"
    )


# ---------------------------------------------------------------------------
# G43 (T-0142-09, ruling-2) — Plasma One as a SECOND card, appended to
# `what_to_watch`, on the 11 US state/city pages inside Plasma's own
# 31-state allow-list (config/availability.yaml cards.plasma, help
# 14703938). ether.fi stays every page's primary CTA (Spain control law,
# G12) — a page never routes its .cta button at Plasma, this task only
# widens what_to_watch.
#
# Mutations this gate must catch (ruling-2's own table):
#   the watch-table's `<<AFFILIATE_URL:plasma>>` reverted to bare
#   `<<AFFILIATE_URL>>`  -> texas's plasma.org link disappears, (a) red
#   the same bullet appended to a page outside the allow-list (india)
#                                                 -> (c) negative control red
# ---------------------------------------------------------------------------

# LAW 5 — literal (Y49): the 11 pages ruling-2 named, never re-derived from
# config/availability.yaml's cards.plasma.us_states.
PLASMA_SLUGS = [
    "geo/best-crypto-card-california",
    "geo/best-crypto-card-colorado",
    "geo/best-crypto-card-florida",
    "geo/best-crypto-card-illinois",
    "geo/best-crypto-card-new-york",
    "geo/best-crypto-card-new-york-city",
    "geo/best-crypto-card-pennsylvania",
    "geo/best-crypto-card-texas",
    "geo/best-crypto-card-wyoming",
    "geo/crypto-card-hawaii",
    "geo/best-crypto-card-usa",
]

# Negative controls (ruling-2's own list): a jurisdiction Plasma's help
# article names as blocked (india), a US state absent from its own 31-state
# allow-list (arizona), and a page this task never touched (spain).
PLASMA_NEGATIVE_CONTROL_SLUGS = [
    "geo/best-crypto-card-india",
    "geo/best-crypto-card-arizona",
    "geo/best-crypto-card-spain",
]

PLASMA_HOST = "www.plasma.org"


@pytest.mark.parametrize("slug", PLASMA_SLUGS)
def test_g43_plasma_second_card_present(slug):
    """(a) a sponsored plasma.org link exists on every allow-listed page."""
    html = _html(slug)
    links = re.findall(
        r'<a[^>]+href="(https://www\.plasma\.org/[^"]*)"[^>]*rel="[^"]*\bsponsored\b',
        html,
    )
    assert links, f"{slug}: no sponsored www.plasma.org link found"


@pytest.mark.parametrize("slug", PLASMA_SLUGS)
def test_g43_primary_cta_stays_etherfi(slug):
    """(b) Plasma is a SECOND card — the page's own primary CTA is untouched."""
    html = _html(slug)
    ctas = re.findall(r'<a[^>]+class="[^"]*\bcta\b[^"]*"[^>]+href="([^"]+)"', html)
    assert ctas, f"{slug}: no .cta element found"
    assert all(ETHERFI_HOST in href for href in ctas), (
        f"{slug}: primary CTA moved off ether.fi: {ctas}"
    )


@pytest.mark.parametrize("slug", PLASMA_NEGATIVE_CONTROL_SLUGS)
def test_g43_plasma_absent_on_negative_controls(slug):
    """(c) Plasma must not appear outside its own allow-list, HTML or sidecar.

    Checked via _external_card_hosts (href= only), not a raw substring: every
    page's register_click tracker carries a GLOBAL partnerHostMap naming every
    configured partner site-wide (PARTNER_HOST_MAP, Y50), so a raw
    'www.plasma.org' in html is present on every page by design and would be
    a false positive here.
    """
    hosts = _external_card_hosts(_html(slug))
    assert PLASMA_HOST not in hosts, f"{slug}: unexpected www.plasma.org link in HTML"
    md_hosts = _external_card_hosts_md(_markdown(slug))
    assert PLASMA_HOST not in md_hosts, f"{slug}: unexpected www.plasma.org link in sidecar"


def test_g43_tracker_covers_plasma_host():
    """(d) register_click's host map names plasma, same law as G43 LAW 2."""
    html = _html(PLASMA_SLUGS[0])
    tracker = re.search(r"partnerHostMap\s*=\s*(\{.*?\})", html, re.S)
    assert tracker, "register_click tracker host map not found in the page"
    assert '"www.plasma.org":"plasma"' in tracker.group(1).replace(" ", ""), (
        f"tracker does not map www.plasma.org -> plasma: {tracker.group(1)}"
    )


@pytest.mark.parametrize("slug", PLASMA_SLUGS)
def test_g43_no_unresolved_plasma_placeholder(slug):
    """(e) both placeholder forms resolved, same law as test_no_unresolved_affiliate_placeholder."""
    assert "<<AFFILIATE_URL" not in _html(slug), f"{slug}: unresolved placeholder"
    assert "<<AFFILIATE_URL" not in _markdown(slug), f"{slug}: unresolved placeholder in sidecar"
