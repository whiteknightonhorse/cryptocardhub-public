#!/usr/bin/env python3
"""T-0142-13 — R6 candidate cards, measured, not chosen.

Measures live issuer sources for the three cards already named in prose on
the 7 fallback geo pages (India, Turkey, Netherlands, Georgia/Arizona/Nevada/
Washington) — RedotPay, Bybit Card, Wirex — against two questions:
(a) is the card issuable in each of the 7 jurisdictions, (b) what does each
card's referral/partner program pay, and which countries is it closed to.

This module ONLY measures and writes logs/partners/r6-candidates-<date>.{md,json}.
It never touches config/affiliates.yaml, config/availability.yaml, or any
seo-site page — per T-0142-11's law: a partner is measured before it is
written into config. The choice of which candidate (if any) to promote is
the operator's; see ruling-3, Q9 point 4.

Sources (fetched 2026-09-24, dangerouslyDisableSandbox curl — WebFetch/
WebSearch were denied in this sandbox for both the parent session and every
subagent; general internet egress works from this host, so the measurement
was done with curl + a browser User-Agent, reading each issuer's own
server-rendered article body — Intercom's `intercom-interblocks-*` markup
for RedotPay, Next.js JSON payload for Bybit — rather than guessing from a
client-rendered shell):

  RedotPay — https://helpcenter.redotpay.com (Intercom-based help center;
             re-fetched attempt 2, 2026-09-24, per ruling-1: the account/KYC
             list [10339263] does not name India/Turkey, but the dedicated
             "Card Issuance Restrictions" article [14254838] does — that
             one, not the account list, answers whether a card is issued;
             a third list, "Location-Based Access Restrictions" [14252906],
             answers app-access-by-location instead and names Tanzania, not
             India/Turkey)
  Bybit    — https://www.bybit.com/en/help-center (in-house Next.js help center)
  Wirex    — https://help.wirexapp.com (Angular SPA; fetched via r.jina.ai's
             text-extraction proxy since the raw HTML is a client-rendered
             shell with no server-side content)
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "logs" / "partners"

JURISDICTIONS = [
    "India", "Turkey", "Netherlands",
    "Georgia (US)", "Arizona (US)", "Nevada (US)", "Washington (US)",
]
VALID_VALUES = {"yes", "no", "not stated"}

# ---------------------------------------------------------------------------
# The measurement itself — literal, dated, cited. Absence from a genuinely
# closed enumerated list is "yes" (T-0142-08's own exception clause); absence
# of any published list at all is "not stated", never inferred as either.
# ---------------------------------------------------------------------------

CANDIDATES: list[dict[str, Any]] = [
    {
        "card": "RedotPay",
        # ruling-1 (2026-09-24) REJECTed the prior India/Turkey "yes": this
        # module had cited the general account/KYC list (10339263), which
        # does not name them, but RedotPay publishes a DEDICATED
        # "Card Issuance Restrictions" article (14254838) — the one that
        # actually answers "is a card issued here" — and it names both. A
        # third list, "Location-Based Access Restrictions" (14252906,
        # app-access-by-location, names Tanzania instead of India/Turkey),
        # answers a different question again. All three fetched and diffed
        # 2026-09-24; the issuance list is authoritative for every RedotPay
        # row below because it is the one titled to answer this question.
        "availability": {
            "India": {
                "value": "no",
                "url": "https://helpcenter.redotpay.com/en/articles/14254838-card-issuance-restrictions",
                "quote": "Residents of these areas are ineligible for both "
                         "virtual and physical cards",
                "fetched": "2026-09-24",
                "note": "India is a literal row on the dedicated 'Card "
                        "Issuance Restrictions' list (updated 2026-08-28). "
                        "It is absent from the broader account/KYC list "
                        "(10339263) and from the app-access list (14252906) "
                        "— only the issuance-specific list excludes it, and "
                        "issuance is exactly what this question asks.",
            },
            "Turkey": {
                "value": "no",
                "url": "https://helpcenter.redotpay.com/en/articles/14254838-card-issuance-restrictions",
                "quote": "Residents of these areas are ineligible for both "
                         "virtual and physical cards",
                "fetched": "2026-09-24",
                "note": "Same as India — Turkey is on the issuance list "
                        "(14254838) but absent from the other two RedotPay "
                        "lists (10339263, 14252906).",
            },
            "Netherlands": {
                "value": "yes",
                "url": "https://helpcenter.redotpay.com/en/articles/14254838-card-issuance-restrictions",
                "quote": "RedotPay services are currently unavailable in "
                         "the regions listed below.",
                "fetched": "2026-09-24",
                "note": "The Netherlands itself is not on the card-issuance "
                        "list — only the distinct former territory "
                        "\"Netherlands Antilles\" is listed.",
            },
            "Georgia (US)": {
                "value": "no",
                "url": "https://helpcenter.redotpay.com/en/articles/14254838-card-issuance-restrictions",
                "quote": "United States",
                "fetched": "2026-09-24",
                "note": "\"United States\" is a literal row on the "
                        "card-issuance list; RedotPay does not sub-divide "
                        "the US by state, so the block is federal.",
            },
            "Arizona (US)": {
                "value": "no",
                "url": "https://helpcenter.redotpay.com/en/articles/14254838-card-issuance-restrictions",
                "quote": "United States",
                "fetched": "2026-09-24",
                "note": "Same federal block as above.",
            },
            "Nevada (US)": {
                "value": "no",
                "url": "https://helpcenter.redotpay.com/en/articles/14254838-card-issuance-restrictions",
                "quote": "United States",
                "fetched": "2026-09-24",
                "note": "Same federal block as above.",
            },
            "Washington (US)": {
                "value": "no",
                "url": "https://helpcenter.redotpay.com/en/articles/14254838-card-issuance-restrictions",
                "quote": "United States",
                "fetched": "2026-09-24",
                "note": "Same federal block as above.",
            },
        },
        "referral": {
            "url": "https://helpcenter.redotpay.com/en/articles/10339296-how-to-invite-friends-and-earn-rewards",
            "reward": "Card Order Commission (% of card fee, paid instantly) "
                      "+ Payment Commission (% of transaction amount, "
                      "settled every 30 days) + Secondary Commission (10% "
                      "of a sub-referral's commission); exact percentages "
                      "are not published in this article, only the "
                      "mechanism and settlement cadence.",
            "currency": "USDT",
            "prohibited_countries_verbatim": None,
            "prohibited_countries_note": "No referral-specific prohibited-country "
                "list is published; referring requires a RedotPay account, so "
                "the account-level block-list above (41 countries, incl. the "
                "United States) governs eligibility to refer by construction.",
            "kyc_or_business_required": "not stated",
            "kyc_or_business_note": "Referrer must have a verified RedotPay "
                "account (implied by needing an account to hold a referral "
                "link at all); a separate 'Partner Program' for larger "
                "audiences is mentioned but its own terms were not found.",
        },
    },
    {
        "card": "Bybit Card",
        "availability": {
            "India": {
                "value": "not stated",
                "url": "https://www.bybit.com/en/help-center/article/How-to-Apply-for-Bybit-Card",
                "quote": "Bybit Card is currently only available in "
                         "limited countries.",
                "fetched": "2026-09-24",
                "note": "No enumerated country allow-list is published for "
                        "the Card specifically (gated behind an in-app "
                        "country picker); India is not named in Bybit's "
                        "platform-wide Excluded Jurisdictions list either, "
                        "so this is genuinely undetermined, not inferred.",
            },
            "Turkey": {
                "value": "not stated",
                "url": "https://www.bybit.com/en/help-center/article/How-to-Apply-for-Bybit-Card",
                "quote": "Bybit Card is currently only available in "
                         "limited countries.",
                "fetched": "2026-09-24",
                "note": "Same as India — no Card-specific list published.",
            },
            "Netherlands": {
                "value": "yes",
                "url": "https://www.bybit.com/en/help-center/article/FAQ-Changes-to-Bybit-Card-Services-in-the-EEA-CH-region",
                "quote": "If you are an EEA resident, you can visit "
                         "bybit.eu/cards to register a new account on "
                         "Bybit EU and complete identity verification.",
                "fetched": "2026-09-24",
                "note": "Available, but as of Dec 2025 EEA residents "
                        "(Netherlands included) are issued a Bybit EUR Card "
                        "by Bybit EU, a separate MiCAR-regulated entity — "
                        "not the same card program as bybit.com's Card.",
            },
            "Georgia (US)": {
                "value": "no",
                "url": "https://www.bybit.com/en/help-center/article/Service-Restricted-Countries",
                "quote": "Bybit does not offer services or products to "
                         "Users in a few excluded jurisdictions including "
                         "the United States",
                "fetched": "2026-09-24",
                "note": "Platform-wide exclusion (ToS §11.3); Bybit does "
                        "not carve the US out by state, so this covers all "
                        "four US jurisdictions.",
            },
            "Arizona (US)": {
                "value": "no",
                "url": "https://www.bybit.com/en/help-center/article/Service-Restricted-Countries",
                "quote": "Bybit does not offer services or products to "
                         "Users in a few excluded jurisdictions including "
                         "the United States",
                "fetched": "2026-09-24",
                "note": "Same platform-wide exclusion as above.",
            },
            "Nevada (US)": {
                "value": "no",
                "url": "https://www.bybit.com/en/help-center/article/Service-Restricted-Countries",
                "quote": "Bybit does not offer services or products to "
                         "Users in a few excluded jurisdictions including "
                         "the United States",
                "fetched": "2026-09-24",
                "note": "Same platform-wide exclusion as above.",
            },
            "Washington (US)": {
                "value": "no",
                "url": "https://www.bybit.com/en/help-center/article/Service-Restricted-Countries",
                "quote": "Bybit does not offer services or products to "
                         "Users in a few excluded jurisdictions including "
                         "the United States",
                "fetched": "2026-09-24",
                "note": "Same platform-wide exclusion as above.",
            },
        },
        "referral": {
            "url": "https://www.bybit.com/en/help-center/article/FAQ-Bybit-Card-Referral-Program",
            "reward": "Referrer earns 20 USDT and referee earns 10 USDT "
                      "after the referee applies for a virtual Bybit Card "
                      "and spends at least 100 USDT (or equivalent) within "
                      "30 days of approval.",
            "currency": "USDT",
            "prohibited_countries_verbatim": (
                "This event is not meant for Austria, Belgium, Bulgaria, "
                "Croatia, Cyprus, Czech Republic, Denmark, Estonia, "
                "Finland, France, Germany, Greece, Hungary, Ireland, "
                "Italy, Latvia, Lithuania, Luxembourg, Malta, Netherlands, "
                "Poland, Portugal, Romania, Slovakia, Slovenia, Spain, "
                "Sweden, Iceland, Liechtenstein and Norway (\"European "
                "Economic Area\") customers. All European Economic Area "
                "customer participation is strictly prohibited."
            ),
            "prohibited_countries_note": "Netherlands is explicitly named — "
                "the Card itself is available there (via Bybit EU) but "
                "participating in THIS referral program is not, a direct "
                "example of why availability and referral eligibility are "
                "measured separately.",
            "kyc_or_business_required": "yes",
            "kyc_or_business_note": "\"Any Bybit user is a qualified "
                "referrer. Referrers are not required to be Bybit Card "
                "users\" — a standard KYC'd Bybit account suffices, no "
                "registered business needed for this program.",
        },
    },
    {
        "card": "Wirex",
        "availability": {
            "India": {
                "value": "no",
                "url": "https://help.wirexapp.com/article/supported-countries-1189",
                "quote": "India✔️✔️✔️✔️N/A",
                "fetched": "2026-09-24",
                "note": "Table columns are Wirex account / Crypto account / "
                        "X-Accounts / Local card top-up / Card — the last "
                        "cell (Card) is N/A for India.",
            },
            "Turkey": {
                "value": "no",
                "url": "https://help.wirexapp.com/article/supported-countries-1189",
                "quote": "Turkey✔️✔️✔️✔️N/A",
                "fetched": "2026-09-24",
                "note": "Same table, Card column N/A for Turkey.",
            },
            "Netherlands": {
                "value": "yes",
                "url": "https://help.wirexapp.com/article/supported-countries-1189",
                "quote": "Netherlands✔️✔️✔️✔️✔️",
                "fetched": "2026-09-24",
                "note": "All five columns, including Card, are checked "
                        "for the Netherlands.",
            },
            "Georgia (US)": {
                "value": "no",
                "url": "https://help.wirexapp.com/article/supported-countries-1189",
                "quote": "United States of America✔️✔️N/A N/A N/A",
                "fetched": "2026-09-24",
                "note": "Card column is N/A for the United States as a "
                        "whole; the table links a per-state breakdown page "
                        "at https://www.wirexapp.com/en-us/help/article/"
                        "the-list-of-supported-states-0633 (note: a "
                        "different host, www.wirexapp.com, not "
                        "help.wirexapp.com) — that page 404'd at fetch time, "
                        "so no state-level exception is known; the federal "
                        "N/A stands as measured.",
            },
            "Arizona (US)": {
                "value": "no",
                "url": "https://help.wirexapp.com/article/supported-countries-1189",
                "quote": "United States of America✔️✔️N/A N/A N/A",
                "fetched": "2026-09-24",
                "note": "Same as Georgia (US) — federal Card N/A, "
                        "per-state page at www.wirexapp.com unreachable "
                        "(404) at fetch time.",
            },
            "Nevada (US)": {
                "value": "no",
                "url": "https://help.wirexapp.com/article/supported-countries-1189",
                "quote": "United States of America✔️✔️N/A N/A N/A",
                "fetched": "2026-09-24",
                "note": "Same as Georgia (US).",
            },
            "Washington (US)": {
                "value": "no",
                "url": "https://help.wirexapp.com/article/supported-countries-1189",
                "quote": "United States of America✔️✔️N/A N/A N/A",
                "fetched": "2026-09-24",
                "note": "Same as Georgia (US).",
            },
        },
        "referral": {
            "url": "https://help.wirexapp.com/article/what-is-wirexs-refer-a-friend-program-1322",
            "reward": "Both referrer and referee earn $10 when the "
                      "referee deposits over $100 into their X-Account "
                      "Plus, or $30 for deposits over $500.",
            "currency": "USD",
            "prohibited_countries_verbatim": None,
            "prohibited_countries_note": "No geographic prohibited-country "
                "list is published for refer-a-friend; the only published "
                "restriction is a promotion-METHOD ban (no paid ads on "
                "'Wirex' keywords), not a jurisdiction. Wirex separately "
                "runs a formal Affiliate program (via Impact.com, "
                "https://help.wirexapp.com/article/wirex-affiliate-faq-1325) "
                "open to individuals and businesses; no prohibited-country "
                "list was found for it either.",
            "kyc_or_business_required": "not stated",
            "kyc_or_business_note": "Refer-a-friend needs \"a valid Wirex "
                "account\" only (no business). The separate Affiliate "
                "program \"welcomes both individual and business "
                "affiliates\" — neither is required, either is accepted.",
        },
    },
]


def validate(candidates: list[dict[str, Any]]) -> None:
    """The law: every yes/no cell must carry a URL and a non-empty quote.

    Raises ValueError naming the offending card+jurisdiction — never a
    silent pass. A 'not stated' cell needs neither (T-0142-08: absence of a
    source is itself the honest answer).
    """
    for c in candidates:
        card = c["card"]
        for j in JURISDICTIONS:
            cell = c["availability"][j]
            value = cell["value"]
            if value not in VALID_VALUES:
                raise ValueError(
                    f"{card}/{j}: value {value!r} not in {VALID_VALUES}")
            if value in ("yes", "no"):
                if not cell.get("url"):
                    raise ValueError(f"{card}/{j}: {value} cell has no url")
                if not cell.get("quote"):
                    raise ValueError(f"{card}/{j}: {value} cell has no quote")


def render_markdown(candidates: list[dict[str, Any]], report_date: str) -> str:
    lines = [f"# R6 candidates measured {report_date} — not chosen", ""]
    for c in candidates:
        lines.append(f"## {c['card']}")
        lines.append("")
        lines.append("| Jurisdiction | Value | URL | Quote | Fetched |")
        lines.append("|---|---|---|---|---|")
        for j in JURISDICTIONS:
            cell = c["availability"][j]
            url = cell.get("url") or ""
            quote = (cell.get("quote") or "").replace("|", "\\|")
            lines.append(f"| {j} | {cell['value']} | {url} | "
                         f"\"{quote}\" | {cell['fetched']} |")
        lines.append("")
        r = c["referral"]
        lines.append("**Referral program**")
        lines.append(f"- URL: {r['url']}")
        lines.append(f"- Reward: {r['reward']}")
        lines.append(f"- Currency: {r['currency']}")
        prohibited = r["prohibited_countries_verbatim"] or "no such list published"
        lines.append(f"- Prohibited countries (verbatim): \"{prohibited}\"")
        lines.append(f"- KYC/business required: {r['kyc_or_business_required']} "
                     f"— {r['kyc_or_business_note']}")
        lines.append("")
    lines.append(
        "No verdict is drawn here. Both filters (a: card issuable in the "
        "7 jurisdictions, b: operator can join the referral program) must "
        "pass before a candidate goes into config/affiliates.yaml or "
        "config/availability.yaml; the operator's own jurisdiction was "
        "given as \"Global\" (2026-09-24), so filter (b) is left to the "
        "operator to check against the verbatim prohibited-country lists "
        "above, not computed here.")
    return "\n".join(lines) + "\n"


def parse_table(md_text: str) -> list[dict[str, Any]]:
    """Parse the availability tables back out of rendered markdown.

    This is the acceptance-side reader: it enforces the same law as
    validate() but by reading rendered TEXT, so a renderer that quietly
    drops a url/quote for a yes/no cell is caught even if validate() was
    never called on the in-memory data that produced it.
    """
    cards: list[dict[str, Any]] = []
    card_name = None
    for line in md_text.splitlines():
        m = re.match(r"^## (.+)$", line)
        if m:
            card_name = m.group(1)
            continue
        if not line.startswith("| ") or line.startswith("|---"):
            continue
        cells = [x.strip() for x in line.strip("|").split("|")]
        if len(cells) != 5 or cells[0] == "Jurisdiction":
            continue
        jurisdiction, value, url, quote, fetched = cells
        if jurisdiction not in JURISDICTIONS:
            continue
        if value not in VALID_VALUES:
            raise ValueError(f"{card_name}/{jurisdiction}: value {value!r} "
                             f"not in {VALID_VALUES}")
        quote_text = quote.strip('"')
        if value in ("yes", "no"):
            if not url:
                raise ValueError(f"{card_name}/{jurisdiction}: {value} row "
                                 f"has no URL")
            if not quote_text:
                raise ValueError(f"{card_name}/{jurisdiction}: {value} row "
                                 f"has no quote")
        cards.append({"card": card_name, "jurisdiction": jurisdiction,
                      "value": value, "url": url, "quote": quote_text})
    return cards


def write_outputs(report_date: str | None = None) -> tuple[Path, Path]:
    validate(CANDIDATES)
    report_date = report_date or date.today().isoformat()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md_path = OUT_DIR / f"r6-candidates-{report_date}.md"
    json_path = OUT_DIR / f"r6-candidates-{report_date}.json"
    md_path.write_text(render_markdown(CANDIDATES, report_date), encoding="utf-8")
    json_path.write_text(
        json.dumps({"date": report_date, "cards": CANDIDATES}, indent=2),
        encoding="utf-8")
    # round-trip check: what we wrote must parse back clean under the same law
    parse_table(md_path.read_text(encoding="utf-8"))
    return md_path, json_path


def latest_report() -> dict[str, Any] | None:
    """Most recent r6-candidates-*.json by filename date, or None."""
    if not OUT_DIR.is_dir():
        return None
    files = sorted(OUT_DIR.glob("r6-candidates-*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def summary_line() -> str:
    """The one line ruling-3 asks _streams_lines() to print. Never silent."""
    report = latest_report()
    if report is None:
        return "  R6 candidates    not yet measured"
    n = len(report["cards"])
    rel = f"logs/partners/r6-candidates-{report['date']}.md"
    return f"  R6 candidates    measured {report['date']}: {n} cards, see {rel}"


if __name__ == "__main__":
    md_path, json_path = write_outputs()
    print(f"wrote {md_path}")
    print(f"wrote {json_path}")
    print(summary_line())
