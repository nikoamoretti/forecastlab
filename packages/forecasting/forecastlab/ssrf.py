from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

BLOCKED_HOSTS = {
    "localhost",
    "metadata.google.internal",
    "metadata.google.com",
}
PRIVATE_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


class UnsafeURLError(ValueError):
    pass


def _is_private(ip: str) -> bool:
    address = ipaddress.ip_address(ip)
    return any(address in network for network in PRIVATE_NETWORKS) or address.is_multicast


def host_matches(host: str, trusted_domain: str) -> bool:
    host = host.lower().rstrip(".")
    trusted = trusted_domain.lower().lstrip(".")
    return host == trusted or host.endswith("." + trusted)


def validate_url(url: str, *, allow_local_fixtures: bool = False) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURLError("Only http and https URLs are allowed")
    if parsed.username or parsed.password:
        raise UnsafeURLError("Embedded credentials are not allowed")
    host = (parsed.hostname or "").lower()
    if not host:
        raise UnsafeURLError("URL host is required")
    if allow_local_fixtures and (
        host in {"127.0.0.1", "localhost", "fixtures.forecastlab.local"}
        or host_matches(host, "forecastlab.test")
        or host_matches(host, "forecastlab.local")
    ):
        return url
    if host in BLOCKED_HOSTS or host.endswith(".local") or host.endswith(".internal"):
        raise UnsafeURLError("Host is not allowed")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Could not resolve host: {host}") from exc
    for info in infos:
        ip = info[4][0]
        if _is_private(str(ip)):
            raise UnsafeURLError("Private or loopback addresses are not allowed")
    return url
