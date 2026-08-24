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
UNAVAILABLE_SLUGS = [
    "geo/best-crypto-card-india",
    "geo/best-crypto-card-turkey",
    "geo/best-crypto-card-netherlands",
    "geo/best-crypto-card-georgia",
    "geo/best-crypto-card-arizona",
    "geo/best-crypto-card-nevada",
    "geo/best-crypto-card-washington",
]

# LAW 2 — every partner host whose clicks must be counted by register_click.
REQUIRED_TRACKER_HOSTS = ["www.ether.fi", "crypto.com"]

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
    for slug in UNAVAILABLE_SLUGS + [AVAILABLE_SLUG]:
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
