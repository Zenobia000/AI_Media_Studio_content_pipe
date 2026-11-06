"""Fetching pages. `Get` is what the collectors and fetch layers are written against, so tests pass in a stand-in; the default refuses
addresses on private networks unless ALLOW_PRIVATE_NETWORK_FETCH is set (a page must not be able to point us at the inside)."""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

USER_AGENT = "MediaHOTBot/1.0 (+newsroom)"
MAX_BYTES = 16 * 1024 * 1024


@dataclass
class Page:
    status: int
    url: str
    type: str
    text: str


@dataclass
class Binary:
    status: int
    url: str
    type: str
    length: int | None  # the size the server states, when it does
    body: bytes


Get = Callable[..., Page]
GetBinary = Callable[..., Binary]


class BlockedAddress(Exception):
    pass


def is_blocked_address(address: str) -> bool:
    """Loopback, private, link-local, metadata, multicast, reserved: never fetched."""
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"))
    )


def assert_public_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise BlockedAddress(f"只接受 http／https：{url}")
    if os.environ.get("ALLOW_PRIVATE_NETWORK_FETCH") == "true":
        return
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise BlockedAddress(f"查不到主機：{parts.hostname}") from e
    for info in infos:
        if is_blocked_address(str(info[4][0])):
            raise BlockedAddress(f"不允許連到內部位址：{parts.hostname}")


def default_get(url: str, headers: dict[str, str] | None = None, timeout: float = 20.0) -> Page:
    assert_public_url(url)
    with httpx.Client(follow_redirects=True, timeout=timeout, headers={"user-agent": USER_AGENT, **(headers or {})}) as client:
        res = client.get(url)
    return Page(res.status_code, str(res.url), res.headers.get("content-type", ""), res.text[:MAX_BYTES])


def default_get_binary(url: str, head: bool = False, max_bytes: int = 500 * 1024 * 1024) -> Binary:
    assert_public_url(url)
    with httpx.Client(follow_redirects=True, timeout=20.0 if head else 300.0, headers={"user-agent": USER_AGENT}) as client:
        if head:
            res = client.head(url)
            body = b""
        else:
            chunks: list[bytes] = []
            size = 0
            with client.stream("GET", url) as res:
                for chunk in res.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("response too large")
                    chunks.append(chunk)
            body = b"".join(chunks)
    stated = res.headers.get("content-length")
    length = int(stated) if stated and stated.isdigit() and int(stated) > 0 else None
    return Binary(res.status_code, str(res.url), res.headers.get("content-type", ""), length, body)
