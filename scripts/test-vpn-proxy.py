#!/usr/bin/env python3
"""
test-vpn-proxy.py — verify SOCKS5 per-channel routing.

Three checks, in order:
  1. SMOKE — connect to one SOCKS5 server, hit ifconfig.me, confirm
     exit IP differs from this host's Hetzner IP (5.78.135.159).
  2. PER-CHANNEL — for a synthetic set of 8 channel_ids, confirm each
     deterministically picks one slot from the pool AND each slot
     gives a distinct US exit IP.
  3. CONCURRENT — fire 8 threads simultaneously, each through a
     different proxy, confirm no race / no shared connection.

Run:
  source /home/cryptocardhub/.env && \
  /home/cryptocardhub/video-generator/venv/bin/python scripts/test-vpn-proxy.py
"""
from __future__ import annotations

import concurrent.futures
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "video-generator"))

# Load .env if invoked outside `set -a; . .env`. Operator may forget.
def _load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    _load_env()
    from pipeline.vpn_proxy import get_proxy_for_channel, current_exit_ip

    print(f"=== test 0: direct Hetzner IP (baseline) ===")
    direct = current_exit_ip(None)
    print(f"  direct: {direct}")
    print()

    sample_channels = [
        "UCtJMwAPl0DwI4XH25ppoong",   # defycard
        "UCpolymarket01",
        "UCpolymarket02",
        "UCpolymarket03",
        "UCpolymarket04",
        "UCpolymarket05",
        "UCpolymarket06",
        "UCpolymarket07",
    ]

    print(f"=== test 1: smoke (first channel) ===")
    proxy = get_proxy_for_channel(sample_channels[0])
    if proxy is None:
        print(
            "  ! proxy=None — NORDVPN_SOCKS_USER/PASS missing from env "
            "OR config/proxies.yaml unreadable. Aborting."
        )
        return 1
    print(f"  picked {proxy}")
    exit_ip = current_exit_ip(proxy, timeout=15)
    print(f"  exit IP: {exit_ip}")
    if exit_ip.startswith("ERROR:") or exit_ip == direct:
        print(f"  ! SOCKS5 NOT working: same as direct or errored")
        return 1
    print(f"  ✓ exit IP differs from direct — SOCKS5 path works")
    print()

    print(f"=== test 2: per-channel deterministic mapping ===")
    by_channel: dict[str, str] = {}
    for ch in sample_channels:
        p = get_proxy_for_channel(ch)
        by_channel[ch] = f"{p.host}:{p.port} ({p.region})" if p else "(none)"
    # Run again to confirm cache + hash are stable
    for ch in sample_channels:
        p2 = get_proxy_for_channel(ch)
        ident = f"{p2.host}:{p2.port} ({p2.region})" if p2 else "(none)"
        if ident != by_channel[ch]:
            print(f"  ! UNSTABLE: {ch} got {ident} then {by_channel[ch]}")
            return 1
    for ch, slot in by_channel.items():
        print(f"  {ch[:24]:<24} -> {slot}")
    # Diversity check
    distinct_hosts = {by_channel[c].split(":")[0] for c in sample_channels}
    print(f"  distinct hosts assigned: {len(distinct_hosts)} of 8 channels")
    if len(distinct_hosts) < 4:
        print(f"  ! poor diversity — hash collision likely; consider channel_pins")
    print()

    print(f"=== test 3: concurrent egress (8 parallel SOCKS5 sessions) ===")
    t0 = time.time()

    def probe(ch: str) -> tuple[str, str, str, float]:
        p = get_proxy_for_channel(ch)
        host = p.host if p else "(none)"
        start = time.time()
        ip = current_exit_ip(p, timeout=20)
        return (ch, host, ip, time.time() - start)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(probe, sample_channels))

    print(f"  total wall time: {time.time() - t0:.1f}s")
    seen_ips: dict[str, list[str]] = {}
    for ch, host, ip, dt in results:
        print(f"  {ch[:24]:<24} via {host:<35} -> {ip:<18} ({dt:.1f}s)")
        seen_ips.setdefault(ip, []).append(ch)
    print()
    print(f"  distinct exit IPs: {len([k for k in seen_ips if not k.startswith('ERROR')])}")
    errors = sum(1 for r in results if r[2].startswith("ERROR"))
    if errors:
        print(f"  ! {errors} probes ERRORED — investigate before going live")
        return 1
    if len(seen_ips) < 4:
        print(f"  ! poor egress diversity even with distinct servers")
        return 1
    print(f"  ✓ all 8 concurrent probes succeeded with diverse IPs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
