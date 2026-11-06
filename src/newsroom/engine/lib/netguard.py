"""The outbound network guard: which addresses a request may never reach, and the checks around them. Port of the second half of
packages/backend/src/lib/url.ts (the first half, URL identity, is core/urls.py)."""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

Resolve = Callable[[str], list[str]]


class BlockedAddressError(Exception):
    """A destination the guard refuses (code EBLOCKED in the TypeScript version)."""

    code = "EBLOCKED"


def _ipv4_blocked(o: tuple[int, int, int, int]) -> bool:
    a, b, c, _ = o
    return (
        a == 0 or a == 10 or a == 127 or a >= 224  # this network, private, loopback, multicast and reserved
        or (a == 100 and 64 <= b <= 127)  # carrier-grade NAT
        or (a == 169 and b == 254)  # link-local, cloud metadata
        or (a == 172 and 16 <= b <= 31)
        or (a == 192 and b == 168)
        or (a == 192 and b == 0 and c in (0, 2))  # IETF protocol assignments, TEST-NET-1
        or (a == 192 and b == 88 and c == 99)  # 6to4 relay anycast
        or (a == 198 and b in (18, 19))  # benchmarking, also used by local fake-IP resolvers
        or (a == 198 and b == 51 and c == 100)
        or (a == 203 and b == 0 and c == 113)  # TEST-NET-2/3
    )  # fmt: skip


def parse_ipv4(s: str) -> tuple[int, int, int, int] | None:
    parts = s.split(".")
    if len(parts) != 4 or not all(re.fullmatch(r"\d{1,3}", p) for p in parts):
        return None
    o = [int(p) for p in parts]
    return (o[0], o[1], o[2], o[3]) if all(0 <= n <= 255 for n in o) else None


def ipv6_groups(s: str) -> list[int] | None:
    """The eight 16-bit groups of an IPv6 address (accepts "::" and a trailing dotted IPv4)."""
    text = re.sub(r"%.*$", "", s.lower())
    dotted = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})$", text)
    if dotted:
        v4 = parse_ipv4(dotted.group(1))
        if not v4:
            return None
        text = f"{text[: -len(dotted.group(1))]}{(v4[0] << 8) | v4[1]:x}:{(v4[2] << 8) | v4[3]:x}"
    halves = text.split("::")
    if len(halves) > 2:
        return None
    head = halves[0].split(":") if halves[0] else []
    tail = halves[1].split(":") if len(halves) == 2 and halves[1] else []
    missing = 8 - len(head) - len(tail)
    if (len(halves) == 1 and missing != 0) or missing < 0:
        return None
    groups = [*head, *(["0"] * missing), *tail]
    if len(groups) != 8 or not all(re.fullmatch(r"[0-9a-f]{1,4}", g) for g in groups):
        return None
    return [int(g, 16) for g in groups]


def _embedded(hi: int, lo: int) -> tuple[int, int, int, int]:
    return (hi >> 8, hi & 0xFF, lo >> 8, lo & 0xFF)


def is_blocked_address(address: str) -> bool:
    """True when an address must never be fetched: loopback, private, link-local, metadata, multicast, reserved and documentation ranges,
    including IPv4 addresses carried inside IPv6 (mapped, compatible, NAT64, 6to4) and Teredo, whose real endpoint cannot be judged."""
    v4 = parse_ipv4(address)
    if v4:
        return _ipv4_blocked(v4)
    g = ipv6_groups(address)
    if not g:
        return True  # not an address we understand: refuse
    g0, g1, g2, g3, g4, g5, g6, g7 = g
    if g0 == g1 == g2 == g3 == g4 == 0 and g5 in (0xFFFF, 0):
        if g5 == 0 and g6 == 0 and g7 in (0, 1):  # :: and ::1
            return True
        return _ipv4_blocked(_embedded(g6, g7))  # ::ffff:a.b.c.d (mapped), ::a.b.c.d (compatible)
    if g0 == 0x64 and g1 == 0xFF9B and g2 == g3 == g4 == g5 == 0:
        return _ipv4_blocked(_embedded(g6, g7))  # NAT64
    if g0 == 0x64 and g1 == 0xFF9B and g2 == 1:
        return True  # local-use NAT64
    if g0 == 0x2002:
        return _ipv4_blocked(_embedded(g1, g2))  # 6to4
    if (g0 == 0x2001 and g1 == 0) or (g0 == 0x2001 and g1 == 0xDB8):
        return True  # Teredo, documentation
    if g0 == 0x100 and g1 == g2 == g3 == 0:
        return True  # discard prefix
    if (g0 & 0xFE00) == 0xFC00:
        return True  # unique local
    if (g0 & 0xFFC0) in (0xFE80, 0xFEC0):
        return True  # link-local, site-local
    return (g0 & 0xFF00) == 0xFF00  # multicast


def _ipv4_private_service(o: tuple[int, int, int, int]) -> bool:
    a, b, c, d = o
    if (a, b, c, d) == (100, 100, 100, 200):
        return False  # Alibaba Cloud metadata, inside carrier-grade NAT
    return a in (10, 127) or (a == 100 and 64 <= b <= 127) or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)


def private_service_address(address: str) -> bool:
    """Where a self-hosted service may run: loopback, private, carrier-grade NAT and unique local addresses, never link-local, cloud
    metadata (100.100.100.200, fd00:ec2::254), multicast or reserved ranges."""
    v4 = parse_ipv4(address)
    if v4:
        return _ipv4_private_service(v4)
    g = ipv6_groups(address)
    if not g:
        return False
    if all(x == 0 for x in g[:5]) and g[5] == 0xFFFF:
        return _ipv4_private_service(_embedded(g[6], g[7]))
    if all(x == 0 for x in g[:7]) and g[7] == 1:
        return True
    return (g[0] & 0xFE00) == 0xFC00 and not (g[0] == 0xFD00 and g[1] == 0xEC2 and all(x == 0 for x in g[2:7]) and g[7] == 0x254)


def refused_address(address: str, listed: bool) -> bool:
    """A listed private feed host (PRIVATE_FEED_HOSTS) may also use private service addresses."""
    return is_blocked_address(address) and not (listed and private_service_address(address))


def blocked_hostname(host: str) -> bool:
    name = host.rstrip(".") if host.endswith(".") else host
    return name == "localhost" or name.endswith(".localhost") or name.endswith(".internal") or name == "metadata.google.internal"


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def system_resolve(host: str) -> list[str]:
    return list(dict.fromkeys(str(i[4][0]) for i in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)))


def assert_public_url(url: str, allow_private: bool = False, resolve: Resolve | None = None, private_hosts: tuple[str, ...] | list[str] = ()) -> str:
    """Rejects non-HTTP URLs and private destinations. Direct requests check the system DNS again at connect time; proxy requests supply their
    outbound resolver. Only local debugging may disable the guard via ALLOW_PRIVATE_NETWORK_FETCH; `private_hosts` are whole names that may
    reach a self-hosted service on a private address. Returns the URL."""
    u = urlsplit(url)
    if u.scheme not in ("http", "https"):
        raise ValueError(f"Blocked protocol {u.scheme}:")
    if allow_private:
        return url
    host = (u.hostname or "").lower()
    listed = host in private_hosts
    if not listed and blocked_hostname(host):
        raise BlockedAddressError(f"Blocked host {host}")
    addresses = [host] if _is_ip(host) else (resolve or system_resolve)(host)
    if not addresses:
        raise BlockedAddressError(f"No address for {host}")
    for address in addresses:
        if refused_address(address, listed):
            raise BlockedAddressError(f"Blocked private address for {host}")
    return url


def guarded_lookup(hostname: str, private_hosts: tuple[str, ...] | list[str] = (), resolve: Resolve | None = None) -> list[str]:
    """DNS lookup for outbound sockets that refuses blocked addresses. Used at connect time, it closes the gap between the URL check and
    the connection (DNS rebinding)."""
    name = hostname.lower()
    listed = name in private_hosts
    if not listed and blocked_hostname(name):
        raise BlockedAddressError(f"Blocked host {hostname}")
    addresses = (resolve or system_resolve)(hostname)
    if not addresses or any(refused_address(a, listed) for a in addresses):
        raise BlockedAddressError(f"Blocked private address for {hostname}")
    return addresses
