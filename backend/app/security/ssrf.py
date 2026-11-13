"""Outbound request policy (SSRF defence).

Rules:
* Only http/https, no credentials in the URL.
* A *test target* (page navigation, API call) must be on the owner's explicit allowlist
  ("host:port"). Nothing is scanned unless the owner authorised it.
* Cloud metadata addresses are blocked even when allowlisted.
* *Subresources* loaded by a page under test may go to public addresses, but private,
  loopback and link-local addresses are blocked unless allowlisted.
* Hostnames are resolved and every resolved address is checked; callers re-validate on
  every redirect hop and request.

Residual risk: DNS can change between validation and connection (TOCTOU / rebinding).
See docs/SECURITY_MODEL.md.
"""

import ipaddress
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

ALLOWED_SCHEMES = {"http", "https"}
_METADATA_ADDRESSES = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("169.254.170.2"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
}
_METADATA_HOSTNAMES = {"metadata.google.internal", "metadata", "instance-data"}

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class TargetNotAllowed(Exception):
    """The destination violates the outbound policy."""


@dataclass(frozen=True)
class ResolvedTarget:
    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


def normalise_allow_entry(entry: str) -> str:
    """Normalise an allowlist entry to 'host:port' (lower-case); accepts a full URL too."""
    entry = entry.strip().lower()
    if "://" in entry:
        parts = urlsplit(entry)
        host, port = parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80)
    else:
        host, _, port_text = entry.rpartition(":") if ":" in entry else (entry, "", "")
        if not port_text.isdigit():
            raise ValueError(f"allowlist entry '{entry}' must be host:port")
        port = int(port_text)
    if not host or not 0 < port < 65536:
        raise ValueError(f"allowlist entry '{entry}' must be host:port")
    return f"{host.strip('[]')}:{port}"


def _resolve(host: str, port: int) -> list[IPAddress]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise TargetNotAllowed(f"cannot resolve host '{host}': {exc}") from exc
    addresses = {ipaddress.ip_address(str(info[4][0]).split("%")[0]) for info in infos}
    if not addresses:
        raise TargetNotAllowed(f"host '{host}' resolved to no addresses")
    return sorted(addresses, key=str)


def _unmap(address: IPAddress) -> IPAddress:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def _is_metadata(address: IPAddress) -> bool:
    return _unmap(address) in _METADATA_ADDRESSES


def _is_public(address: IPAddress) -> bool:
    return _unmap(address).is_global


def _parse(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise TargetNotAllowed(f"scheme '{scheme or '(none)'}' is not allowed (http/https only)")
    if parts.username or parts.password:
        raise TargetNotAllowed("credentials in URLs are not allowed")
    host = (parts.hostname or "").lower()
    if not host:
        raise TargetNotAllowed("URL has no host")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise TargetNotAllowed("invalid port") from exc
    return scheme, host, port


def _check(url: str, allowed: Iterable[str], *, require_allowlist: bool) -> ResolvedTarget:
    _, host, port = _parse(url)
    if host in _METADATA_HOSTNAMES:
        raise TargetNotAllowed("cloud metadata endpoints are always blocked")
    allowlisted = f"{host}:{port}" in set(allowed)
    addresses = _resolve(host, port)
    if any(_is_metadata(address) for address in addresses):
        raise TargetNotAllowed("cloud metadata endpoints are always blocked")
    if require_allowlist and not allowlisted:
        raise TargetNotAllowed(
            f"'{host}:{port}' is not an authorised target. Add it under Execution Settings -> "
            "Allowed targets only if you own it or are authorised to test it."
        )
    if not allowlisted and not all(_is_public(address) for address in addresses):
        raise TargetNotAllowed(f"'{host}' resolves to a private/loopback address and is not allowlisted")
    return ResolvedTarget(url=url, host=host, port=port, addresses=tuple(str(a) for a in addresses))


def validate_target(url: str, allowed: Iterable[str]) -> ResolvedTarget:
    """Validate a URL the platform is about to actively test. Must be allowlisted."""
    return _check(url, allowed, require_allowlist=True)


def validate_subresource(url: str, allowed: Iterable[str]) -> ResolvedTarget:
    """Validate a URL a page under test wants to load (public or allowlisted only)."""
    return _check(url, allowed, require_allowlist=False)
