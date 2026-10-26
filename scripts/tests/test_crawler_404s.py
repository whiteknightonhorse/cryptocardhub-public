"""CCH-SEO-02: verified search-crawler 404 miner. Literal fixtures, no network."""
import importlib.util
import ipaddress
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
spec = importlib.util.spec_from_file_location("crawler_404s", ROOT / "scripts" / "crawler-404s.py")
c4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c4)

GOOGLE_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
IN_IP, OUT_IP = "66.249.66.1", "203.0.113.9"
RANGES = {"Googlebot": [ipaddress.ip_network("66.249.64.0/19")],
          "bingbot": [ipaddress.ip_network("157.55.39.0/24")]}


def line(ip, path, status, ua=GOOGLE_UA, host="defycard.com"):
    tail = f" host={host}" if host else ""
    return (f'{ip} - - [19/Oct/2026:09:15:12 +0000] "GET {path} HTTP/1.1" {status} 146 "-" '
            f'"{ua}"{tail}')


def test_verified_googlebot_404_counted():
    r = c4.mine([line(IN_IP, "/compare/multi-currency-crypto-cards/?x=1", 404)], RANGES)
    assert r["paths"] == {"/compare/multi-currency-crypto-cards/": {
        "hits": 1, "last_seen": "2026-10-19", "agents": {"Googlebot": 1, "bingbot": 0}}}
    assert r["window"] == ["2026-10-19", "2026-10-19"]
    assert r["claimed_unverified"] == 0


def test_ip_outside_range_is_claimed_unverified():
    r = c4.mine([line(OUT_IP, "/compare/multi-currency-crypto-cards/", 404)], RANGES)
    assert r["paths"] == {}
    assert r["claimed_unverified"] == 1


def test_200_ignored():
    r = c4.mine([line(IN_IP, "/compare/multi-currency-crypto-cards/", 200)], RANGES)
    assert r["paths"] == {} and r["claimed_unverified"] == 0


def test_other_host_ignored():
    r = c4.mine([line(IN_IP, "/compare/x-y/", 404, host="aipush.app")], RANGES)
    assert r["paths"] == {} and r["claimed_unverified"] == 0


def test_no_host_field_kept():
    r = c4.mine([line(IN_IP, "/compare/x-y/", 404, host=None)], RANGES)
    assert list(r["paths"]) == ["/compare/x-y/"]


def test_video_without_json_proposes_410(tmp_path):
    assert c4.propose("/videos/abc/", set(), videos_dir=tmp_path) == "410"
    (tmp_path / "abc.json").write_text("{}")
    assert c4.propose("/videos/abc/", set(), videos_dir=tmp_path) != "410"


def test_jaccard_proposal_prefers_same_bucket():
    served = {"/compare/best-crypto-cards/", "/reviews/best-crypto-cards/"}
    assert c4.propose("/compare/zzz/", served) == "-"  # no shared token
    assert c4.propose("/for/best-crypto-cards/", served) == "/compare/best-crypto-cards/"  # tie: sorted first
    assert c4.propose("/compare/best-crypto-cards-2026/", served) == "/compare/best-crypto-cards/"
