from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def assert_safe_url(url: str) -> str:
    """Reject URLs that resolve to a non-public address (SSRF guard for user-supplied base_url).
    NOTE: connect-time IP-pinning for full DNS-rebind defense is a Slice-B hardening; for Slice A we
    validate every resolved address and the caller disables redirects."""
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError("only http(s) URLs allowed")
    if not p.hostname:
        raise ValueError("no host in URL")
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except Exception:
        raise ValueError("host does not resolve")
    for *_, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise ValueError("URL resolves to a non-public address")
    return url
