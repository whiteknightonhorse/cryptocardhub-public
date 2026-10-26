"""G39 (T-0142-06) — the "verified against the issuer" badge only ever shows a
`status: current` fact, and only on pages that are actually about ether.fi.

Bought by the ledger's own recurring bug (Y50/Y53/Y54): `partner` defaults to
`etherfi` (config/affiliates.yaml `default: etherfi`) whenever a page sets no
`primary_partner` at all — so a Crypto.com FAQ page with no frontmatter
override silently inherits ether.fi as ITS partner too. Badging such a page
with ether.fi's own fact record would stamp a verified date onto a card the
page isn't even about — exactly the class of mismatch this ledger keeps
paying for one surface at a time.

Runs against the BUILT site (seo-site/dist), like every gate here (Y50:
measure the served artifact) — the badge's placement and its gating both live
in the Astro template, and only the rendered HTML proves the fact record's own
verified_at/source_url actually reached a page (Y53/Y54: verify arrived, not
exists).

THE LAW IS A LITERAL HERE (Y49): the exact verified_at/source_url are typed
out from config/facts/etherfi.yaml below, not read back from that file — a
test that re-derives its expectation from the fact record it is meant to
police moves with the record and can never go red.

REVIEW-FIX (ruling-2, REJECT): the badge used to print only a bare date, with
no claim tying it to a specific number — on 31 pages it sat directly under
stale hand-written copy ("$40 refundable deposit") that the cited fact's own
`history` calls wrong (etherfi.fee.physical_card.core: `value: 50`, history
entry `"$40 refundable deposit (wrong)"`). The badge now carries the fact's
own value in its text, so it states the figure it actually backs instead of
silently endorsing whatever sits above it. `reviews/ether-fi-cash-review` is
itself one of the 31 — its `snapshot_bullets` say "$40 refundable deposit" —
so it doubles as the literal regression case the ruling asked for.
"""
from __future__ import annotations

from pathlib import Path

import pytest

DIST = Path(__file__).resolve().parent.parent / "dist"

# LAW — config/facts/etherfi.yaml, id etherfi.fee.physical_card.core,
# status: current. A deliberate change to that record's verified_at/
# source_url/status/value must update these literals in the same commit.
BADGE_VERIFIED_AT = "2026-08-13"
BADGE_SOURCE_URL = "https://help.ether.fi/en/articles/376356-physical-cards-faq"
BADGE_CLAIM_VALUE = "$50"
BADGE_MARKER = 'class="verified-badge"'


def _html(slug: str) -> str:
    page = DIST / slug / "index.html"
    if not page.exists():
        pytest.fail(
            f"{page} missing — build the site first: "
            f"cd seo-site && set -a && . ../.env && set +a && pnpm build"
        )
    return page.read_text(encoding="utf-8")


def test_etherfi_review_page_carries_the_badge_with_the_facts_yaml_date():
    html = _html("reviews/ether-fi-cash-review")
    assert BADGE_MARKER in html, "no verified-badge on the ether.fi review page"
    idx = html.find(BADGE_MARKER)
    block = html[idx: idx + 500]
    assert BADGE_VERIFIED_AT in block, (
        f"verified-badge is present but does not carry the facts.yaml date {BADGE_VERIFIED_AT!r}"
    )
    assert BADGE_SOURCE_URL in block, (
        "verified-badge is present but does not carry the facts.yaml source_url"
    )


def test_etherfi_review_page_badge_states_the_correct_fee_not_the_stale_one():
    """The literal regression case ruling-2 asked for. This page's own
    snapshot_bullets say "$40 refundable deposit" — stale copy the cited
    fact's own `history` calls wrong. The badge must state the fact's actual
    value ($50), not repeat the $40 figure sitting right above it, and must
    not go silent instead (going silent would dodge the fix, not satisfy it).
    Mutation: revert VerifiedBadge.astro's `claim` prop to a hardcoded literal
    equal to the stale figure (e.g. "Core physical card fee: $40") and this
    goes red.
    """
    html = _html("reviews/ether-fi-cash-review")
    assert "$40 refundable deposit" in html, (
        "fixture assumption broken — this page no longer carries the stale "
        "$40 figure the badge is being checked against; pick a page that does"
    )
    idx = html.find(BADGE_MARKER)
    assert idx != -1, "no verified-badge on the ether.fi review page"
    block = html[idx: idx + 500]
    assert BADGE_CLAIM_VALUE in block, (
        f"verified-badge does not state the fact's own value {BADGE_CLAIM_VALUE!r} — "
        "it must claim the figure it backs, not just a bare date"
    )
    assert "$40" not in block, (
        "verified-badge repeats the stale $40 figure instead of the fact's current value"
    )


def test_cryptocom_partner_page_carries_no_badge():
    html = _html("geo/best-crypto-card-india")
    assert BADGE_MARKER not in html, (
        "geo/best-crypto-card-india routes its CTA to Crypto.com (config/availability.yaml "
        "fallback partner) — it must not carry ether.fi's verified badge"
    )


def test_page_defaulting_to_etherfi_but_not_about_it_carries_no_badge():
    """The sharper case: this FAQ page sets no `primary_partner` at all, so it
    silently inherits DEFAULT_PARTNER=etherfi (config/affiliates.yaml) the
    same way every genuine ether.fi page does — the badge must still not
    appear, because the page is about Crypto.com's card, not ether.fi's.
    Mutation: drop the slug/brand-token check from `isEtherfiContentPage` in
    [bucket]/[slug].astro (leave only `partner === 'etherfi'` + bucket) and
    this goes red — the badge appears on this page too.
    """
    html = _html("faq/is-crypto-com-card-safe")
    assert BADGE_MARKER not in html, (
        "faq/is-crypto-com-card-safe has no primary_partner frontmatter and inherits the "
        "site default (etherfi) same as a real ether.fi page — the badge must be gated on "
        "the page's own content (its slug naming the brand), not the resolved CTA partner alone"
    )
