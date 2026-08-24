"""Per-channel SOCKS5 proxy selector for YouTube API egress (Y45).

Every YT API call from this Hetzner host (which hosts 8 separate
YouTube channels) MUST route through a per-channel NordVPN SOCKS5
exit so YouTube doesn't see all 8 channels as co-publishing from one
IP — a soft trust-score penalty for new channels.

Architecture:
  - `config/proxies.yaml` declares a pool of ~8 US SOCKS5 servers.
  - Each channel is mapped to ONE pool slot via deterministic
    hash(channel_id) % N (so the same channel always exits from the
    same US city — consistent geo signal builds trust faster).
  - Credentials live in .env: NORDVPN_SOCKS_USER + NORDVPN_SOCKS_PASS
    (NordVPN "service credentials", not the regular account login).
  - Concurrency safe: SOCKS5 is per-connection, no shared tun device.
    Channel A publishing through atlanta.us.nordhold.net and channel B
    publishing through new-york.us.nordhold.net at the same wall-clock
    second do not interfere.

Public API:
  get_proxy_for_channel(channel_id) -> ProxySpec | None
  make_proxied_http(proxy) -> googleapiclient-ready httplib2.Http
  current_exit_ip(proxy) -> str (for diagnostics)
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

# Resolve repo root robustly — script may be imported by pipeline.main or
# called directly from test harnesses.
_ROOT = Path(__file__).resolve().parent.parent.parent
_CFG = _ROOT / "config" / "proxies.yaml"


@dataclass(frozen=True)
class ProxySpec:
    host: str
    port: int
    user: str
    password: str
    region: str

    @property
    def url(self) -> str:
        """Render as `socks5h://user:pass@host:port` for `requests`/curl.

        socks5h forces hostname resolution at the SOCKS5 server (not
        client-side). Mandatory when proxying through NordVPN — local
        DNS would leak the channel's true client identity.
        """
        return f"socks5h://{self.user}:{self.password}@{self.host}:{self.port}"

    def __str__(self) -> str:  # safe for logs — never leak password
        return f"<ProxySpec {self.host}:{self.port} region={self.region}>"


def _load_cfg() -> dict:
    """Read config/proxies.yaml; cached for the process lifetime."""
    import yaml
    return yaml.safe_load(_CFG.read_text(encoding="utf-8"))


@lru_cache(maxsize=64)
def get_proxy_for_channel(channel_id: Optional[str]) -> Optional[ProxySpec]:
    """Pick a stable SOCKS5 server for this channel.

    Order of resolution:
      1. config.channel_pins[channel_id] — explicit operator override.
      2. hash(channel_id) % len(us_socks5_pool) — deterministic mapping.
      3. None if creds or pool are missing — caller falls back to direct.

    `None` is the safe default for unknown channels and for the
    operator's local development (no creds in .env). The pipeline
    treats `None` as "no proxy" — request goes through the host IP.
    """
    # Hard kill-switch — operator can disable Y45 routing without removing
    # credentials. 2026-05-13: defycard is the only channel in this project,
    # so the multi-channel-IP-isolation benefit doesn't apply, and OAuth
    # Google Account was created from Hetzner DE — proxying through NYC
    # introduces a geolocation mismatch that may trigger anti-fraud review.
    # Set YT_PROXY_ENABLED=true once a second channel goes live here.
    if os.environ.get("YT_PROXY_ENABLED", "").lower() not in ("true", "1", "yes", "on"):
        return None
    user = os.environ.get("NORDVPN_SOCKS_USER", "").strip()
    pwd = os.environ.get("NORDVPN_SOCKS_PASS", "").strip()
    if not (user and pwd):
        return None

    try:
        cfg = _load_cfg()
    except (OSError, ValueError):
        return None

    pool = cfg.get("us_socks5_pool") or []
    if not pool:
        return None
    port = int(cfg.get("socks5_default_port", 1080))

    pins = cfg.get("channel_pins") or {}
    pinned_host = pins.get(channel_id) if channel_id else None

    if pinned_host:
        match = next((p for p in pool if p["host"] == pinned_host), None)
        if match:
            return ProxySpec(
                host=match["host"], port=port,
                user=user, password=pwd,
                region=match.get("region", "us"),
            )
        # Pin host not in pool — fall through to hash. Log on next caller.

    # Hash-based deterministic pick. SHA-1 because hash() seed varies
    # per Python process and we want STABLE per-channel routing across
    # restarts.
    key = (channel_id or "default").encode("utf-8")
    idx = int(hashlib.sha1(key).hexdigest(), 16) % len(pool)
    chosen = pool[idx]
    return ProxySpec(
        host=chosen["host"], port=port,
        user=user, password=pwd,
        region=chosen.get("region", "us"),
    )


def make_proxied_http(proxy: ProxySpec, timeout: int = 300):
    """Build an httplib2.Http instance wired to the SOCKS5 proxy.

    googleapiclient.discovery.build(..., http=...) consumes any
    httplib2.Http; we just need to pass `proxy_info`. Returns the
    plain Http when `proxy` is None so callers can collapse the
    branching.

    Timeout default 300s — YT video upload is chunked + can take
    several minutes through SOCKS5 on a 15-30MB Short.
    """
    import httplib2
    if proxy is None:
        return httplib2.Http(timeout=timeout)
    proxy_info = httplib2.ProxyInfo(
        proxy_type=httplib2.socks.PROXY_TYPE_SOCKS5,
        proxy_host=proxy.host,
        proxy_port=proxy.port,
        proxy_user=proxy.user,
        proxy_pass=proxy.password,
        proxy_rdns=True,  # resolve at exit server, not locally
    )
    return httplib2.Http(proxy_info=proxy_info, timeout=timeout)


def current_exit_ip(proxy: Optional[ProxySpec], *, timeout: int = 8) -> str:
    """Diagnostic: returns the IP that ifconfig.me sees through `proxy`.

    Returns "ERROR: <msg>" on failure. Used by scripts/test-vpn-proxy.py.
    """
    if proxy is None:
        # Direct path — useful for confirming what "no proxy" returns.
        try:
            import requests
            r = requests.get("https://ifconfig.me/ip", timeout=timeout)
            return (r.text or "").strip() or "ERROR: empty"
        except Exception as e:
            return f"ERROR: {type(e).__name__}: {e}"

    try:
        import requests
        r = requests.get(
            "https://ifconfig.me/ip",
            proxies={"http": proxy.url, "https": proxy.url},
            timeout=timeout,
        )
        return (r.text or "").strip() or "ERROR: empty"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
