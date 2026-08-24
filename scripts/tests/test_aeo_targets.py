"""G29 — the three work lists Fable's 2026-08-13 ruling put the conveyor on.

Recombining bank keys into new pages is forbidden (doorway by construction), so
the only lawful inputs are measured: W1 the top fetched pages, W2 the pages no
agent ever fetched, W3 article paths agents asked for and we do not have.

W3 IS THE DANGEROUS ONE and this file exists mostly for it. A path that reaches
`demand_404` becomes a demand row, and a demand row is a licence to print a
page. Measured on this host over one retention window: 1288 of the 404s carry
an answer-time user agent, and all but ONE of them ask for /.env, /.git/config,
/secrets.json and friends. The UA is forged — `ChatGPT-User` is a string anyone
can send, so excluding index crawlers does NOT protect this list. The
article-shape filter is the only thing standing between a vulnerability scanner
and the keyword bank.

THE LAWS ARE LITERALS HERE (lesson Y49): the threshold 3, the batch size 20 and
the URL shape are typed out again by hand and never imported from the module
under test.
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


aeo = _load("aeo_aggregate", "scripts/aeo-aggregate.py")

WEEK = (date(2026, 8, 3), date(2026, 8, 9))

CHATGPT_UA = ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); "
              "compatible; ChatGPT-User/1.0; +https://openai.com/bot")
CLAUDE_UA = "Claude-User/1.0; +Claude-User@anthropic.com"
CRAWLER_UA = "Mozilla/5.0 (compatible; OAI-SearchBot/1.0)"
BROWSER_UA = "Mozilla/5.0 (Macintosh) Chrome/131.0 Safari/537.36"


def _line(ua, path, status, day="05"):
    return (f'1.2.3.4 - - [{day}/Aug/2026:10:00:00 +0000] "GET {path} HTTP/1.1" '
            f'{status} 1234 "-" "{ua}"')


# ---------------------------------------------------------------------------
# W3: what may become a demand row
# ---------------------------------------------------------------------------

def test_scanner_wearing_an_agent_ua_never_becomes_demand():
    """The real traffic this filter exists for — forged UA, credential paths."""
    for path in ("/api/.env", "/.git/config", "/secrets.json", "/id_rsa",
                 "/.aws/credentials", "/wp-admin/setup-config.php"):
        assert aeo.parse_404(_line(CHATGPT_UA, path, 404)) is None, path


def test_a_real_missing_article_is_demand():
    rec = aeo.parse_404(_line(CHATGPT_UA, "/reviews/brighty-card-review/", 404))
    assert rec == {"date": "2026-08-05", "path": "/reviews/brighty-card-review/"}


def test_demand_needs_all_three_conditions():
    art = "/reviews/brighty-card-review/"
    # a 200 is not demand — we already have that page
    assert aeo.parse_404(_line(CHATGPT_UA, art, 200)) is None
    # a human's 404 is not demand — this instrument measures agents
    assert aeo.parse_404(_line(BROWSER_UA, art, 404)) is None
    # an index crawler's 404 is not demand — it says nothing about an answer
    assert aeo.parse_404(_line(CRAWLER_UA, art, 404)) is None
    # a bucket index is not an article
    assert aeo.parse_404(_line(CHATGPT_UA, "/reviews/", 404)) is None


def test_threshold_is_three_hits_in_the_window():
    art = "/reviews/brighty-card-review/"
    two = [_line(CHATGPT_UA, art, 404, "04"), _line(CLAUDE_UA, art, 404, "05")]
    three = two + [_line(CHATGPT_UA, art, 404, "06")]

    assert aeo.mine_404s(two, *WEEK) == {art: 2}
    tg = aeo.build_targets({"by_page": {}}, aeo.mine_404s(two, *WEEK), set())
    assert tg["demand_404"] == []                       # 2 is noise
    assert tg["demand_404_below_threshold"] == 1        # ...but it is COUNTED

    tg = aeo.build_targets({"by_page": {}}, aeo.mine_404s(three, *WEEK), set())
    assert tg["demand_404"] == [{"path": art, "hits": 3}]
    assert tg["demand_404_below_threshold"] == 0


def test_demand_outside_the_window_does_not_count():
    art = "/reviews/brighty-card-review/"
    inside = [_line(CHATGPT_UA, art, 404, d) for d in ("04", "05", "06")]
    outside = [_line(CHATGPT_UA, art, 404, "02")]      # Sunday before the week
    assert aeo.mine_404s(inside + outside, *WEEK) == {art: 3}


# ---------------------------------------------------------------------------
# W1 / W2: the ranking and its complement
# ---------------------------------------------------------------------------

def test_by_page_counts_every_fetch_and_matches_the_page_total():
    hot = "/faq/who-can-get-ether-fi-cash/"
    cool = "/reviews/is-redotpay-legit/"
    lines = [_line(CHATGPT_UA, hot, 200)] * 3 + [_line(CLAUDE_UA, cool, 200)]
    agg = aeo.aggregate(lines, *WEEK)
    assert agg["by_page"] == {hot: 3, cool: 1}
    assert list(agg["by_page"]) == [hot, cool]          # sorted by count desc
    assert agg["ai_pages_fetched"] == len(agg["by_page"])


def test_hot_and_cold_partition_the_published_pages():
    hot = "/faq/who-can-get-ether-fi-cash/"
    cold_a = "/compare/best-crypto-card-for-amazon/"
    cold_b = "/learn/what-is-a-crypto-card/"
    existing = {hot, cold_a, cold_b}
    agg = {"by_page": {hot: 9, "/": 130}}

    tg = aeo.build_targets(agg, {}, existing)

    assert tg["hot"] == [{"path": hot, "fetches": 9}]
    assert tg["cold"] == [cold_a, cold_b]               # sorted, complete
    assert tg["hot_total"] + tg["cold_total"] == tg["pages_total"] == 3
    # the home page is fetched but is not a pSEO page: it belongs to neither
    # list, and it is not an orphan either.
    assert "/" not in tg["cold"] and "/" not in tg["orphan_fetched"]


def test_a_fetched_path_absent_from_disk_is_an_orphan_not_a_hot_page():
    """A stale URL still served from an old build must not enter the W1 batch —
    there is no file to deepen, and counting it would hide the deletion."""
    gone = "/reviews/deleted-card-review/"
    live = "/faq/who-can-get-ether-fi-cash/"
    tg = aeo.build_targets({"by_page": {gone: 40, live: 9}}, {}, {live})
    assert tg["orphan_fetched"] == [gone]
    assert tg["hot"] == [{"path": live, "fetches": 9}]


def test_the_w1_batch_is_capped_at_twenty():
    by_page = {f"/faq/q-{i:03d}/": 100 - i for i in range(30)}
    tg = aeo.build_targets({"by_page": by_page}, {}, set(by_page))
    assert len(tg["hot"]) == 20
    assert tg["hot_total"] == 30                        # the cap hides nothing
    assert tg["hot"][0]["fetches"] == 100               # highest first
