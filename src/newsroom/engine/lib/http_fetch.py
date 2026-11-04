"""Outbound HTTP for collectors, the image proxy and the paid APIs: SSRF guard, routing, limits. Port of lib/http-fetch.ts.

Differences from the TypeScript version, on purpose or for lack of a Python equivalent:
- A direct request connects to an address this module checked itself (a custom network backend resolves, checks, and dials that address), so
  DNS rebinding between the check and the connection is closed, as with undici's guarded lookup.
- A request through the egress proxy is checked against addresses resolved through that proxy (DNS over HTTPS) before it is sent, but the
  proxy then resolves the name again to open the tunnel: the tunnel is not pinned to the checked address as undici's PinnedProxyClient
  does it. Closing that gap needs a custom proxy connection class.
- The P-256 retry for servers that refuse X25519 key shares is not done.
"""

from __future__ import annotations

import codecs
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

from newsroom.engine import pack
from newsroom.engine.config import config
from newsroom.engine.lib.netguard import assert_public_url, guarded_lookup

EgressRoute = Literal["egress", "direct"]
_PUBLIC_HEADERS = {
    "accept",
    "accept-language",
    "accept-encoding",
    "user-agent",
    "cache-control",
    "if-modified-since",
    "if-none-match",
    "range",
    "if-range",
}
_REDIRECTS = (301, 302, 303, 307, 308)


def default_ua() -> str:
    """How the collectors introduce themselves: the site's own crawler name and address (site/site.ts)."""
    return f"Mozilla/5.0 (compatible; {pack.site()['SITE']['crawlerName']}; +{config().site_url}/about)"


def language_headers(language: str | None) -> dict[str, str]:
    """The language a source asked for (its config.acceptLanguage), replacing the default preference."""
    return {"accept-language": language} if language else {}


class HttpStatusError(Exception):
    """A response other than 200, with its status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class GuardedResponse:
    status: int
    url: str
    headers: httpx.Headers
    body: bytes
    truncated: bool = False  # the body is the first max_bytes of a longer response (only with `truncate`)

    def text(self) -> str:
        return decode_body(self.body, self.headers.get("content-type"))


def require_ok(res: GuardedResponse, what: str | None = None) -> GuardedResponse:
    """A response that must be the resource itself: anything but 200 is an error naming the status (and `what` was asked for)."""
    if res.status != 200:
        raise HttpStatusError(res.status, f"HTTP {res.status} for {what}" if what else f"HTTP {res.status}")
    return res


def decode_body(body: bytes, content_type: str | None) -> str:
    m = re.search(r'charset="?([\w-]+)', content_type or "", re.I)  # a media-type parameter may be a quoted string: charset="gb2312"
    charset = m.group(1).lower() if m else "utf-8"
    if not m:
        head = body[:2048].decode("latin-1")
        meta = re.search(r"""<meta[^>]+charset=["']?([\w-]+)""", head, re.I) or re.search(r"""encoding=["']([\w-]+)["']""", head, re.I)
        if meta:
            charset = meta.group(1).lower()
    try:
        text = body.decode(codecs.lookup("gbk" if charset == "gb2312" else charset).name, errors="replace")
    except LookupError:
        text = body.decode("utf-8", errors="replace")
    # NUL cannot be represented in PostgreSQL text or JSONB. A character lost on the way reads as one U+FFFD however many of its bytes were
    # garbled, so text cut to a length keeps the same cut.
    return re.sub("�+", "�", text.replace("\x00", "�"))


class _GuardedBackend(httpcore.SyncBackend):
    """Resolves the name, refuses blocked addresses, and dials the address it checked."""

    def __init__(self, private_hosts: tuple[str, ...]) -> None:
        super().__init__()
        self._private_hosts = private_hosts

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):  # type: ignore[override]
        addresses = guarded_lookup(host, self._private_hosts)
        ip = next((a for a in addresses if ":" not in a), addresses[0])
        return super().connect_tcp(ip, port, timeout, local_address, socket_options)


def _guarded_client(private_hosts: tuple[str, ...], timeout: float) -> httpx.Client:
    transport = httpx.HTTPTransport(http2=False)
    transport._pool._network_backend = _GuardedBackend(private_hosts)  # noqa: SLF001
    return httpx.Client(transport=transport, follow_redirects=False, timeout=timeout)


def _host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _proxied(url: str, route: str, private_hosts: tuple[str, ...]) -> bool:
    proxy = config().egress_proxy_url
    if route != "egress" or not proxy:
        return False
    host = _host_of(url)
    is_ip = bool(re.fullmatch(r"[\d.]+|[0-9a-f:]+", host)) and (":" in host or host.replace(".", "").isdigit())
    direct = pack.site()["DEPLOYMENT"]["directFetchHosts"]
    return not is_ip and host not in private_hosts and not host.endswith(".cn") and not host.endswith(".local") and host not in direct


def egress_resolver(proxy_url: str) -> Callable[[str], list[str]]:
    """Resolve abroad for outbound proxy traffic through DNS over HTTPS reached via the same proxy, never the system resolver."""
    cache: dict[str, tuple[list[str], float]] = {}

    def resolve(host: str) -> list[str]:
        hit = cache.get(host)
        if hit and hit[1] > time.monotonic():
            return hit[0]
        addresses: list[str] = []
        ttl = 60.0
        with httpx.Client(proxy=proxy_url, timeout=5.0) as client:
            for kind in ("A", "AAAA"):
                res = client.get(
                    "https://cloudflare-dns.com/dns-query", params={"name": host, "type": kind}, headers={"accept": "application/dns-json"}
                )
                if res.status_code != 200:
                    raise RuntimeError(f"DNS upstream {res.status_code}")
                data = res.json()
                if data.get("Status") != 0:
                    raise RuntimeError(f"DNS lookup failed for {host}")
                for a in data.get("Answer", []):
                    if a.get("type") in (1, 28):
                        addresses.append(a["data"])
                        ttl = min(ttl, float(a.get("TTL") or 0))
        if not addresses:
            raise RuntimeError(f"No address for {host}")
        cache[host] = (addresses, time.monotonic() + max(0.0, ttl))
        return addresses

    return resolve


def guarded_fetch(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
    timeout_ms: int = 20_000,
    max_bytes: int = 8 * 1024 * 1024,
    truncate: bool = False,
    max_redirects: int = 5,
    redirect_policy: Literal["same-origin"] | None = None,
    route: EgressRoute = "egress",
    private_feed_host: bool = False,
) -> GuardedResponse:
    """GET (or another method) with the guard on: every hop is checked, one time budget covers DNS, every redirect and the body."""
    cfg = config()
    deadline = time.monotonic() + timeout_ms / 1000
    configured = _host_of(url)
    private_hosts = (configured,) if private_feed_host and configured in cfg.private_feed_hosts else ()
    resolver = egress_resolver(cfg.egress_proxy_url) if cfg.egress_proxy_url else None

    def check(target: str) -> str:
        if time.monotonic() >= deadline:
            raise TimeoutError("request budget used up")
        via_proxy = _proxied(target, route, private_hosts)
        return assert_public_url(target, cfg.allow_private_network_fetch, resolver if via_proxy else None, private_hosts)

    current = check(url)
    req_headers = httpx.Headers({"user-agent": default_ua(), "accept-language": "zh-CN,zh;q=0.9,en;q=0.8"})
    for name, value in (headers or {}).items():
        req_headers[name] = value  # a caller's "User-Agent" replaces the default instead of joining it
    # Unknown custom headers and request bodies may carry credentials: the whole redirect chain keeps the first origin.
    origin_bound = redirect_policy == "same-origin" or body is not None or any(n.lower() not in _PUBLIC_HEADERS for n in (headers or {}))
    initial = urlsplit(current)
    initial_origin = (initial.scheme, initial.netloc)
    verb = method.upper()
    payload = body
    hop = 0
    while True:
        via_proxy = _proxied(current, route, private_hosts)
        remaining = max(0.1, deadline - time.monotonic())
        if cfg.allow_private_network_fetch:
            client = httpx.Client(follow_redirects=False, timeout=remaining)
        elif via_proxy:
            client = httpx.Client(proxy=cfg.egress_proxy_url, follow_redirects=False, timeout=remaining)
        else:
            client = _guarded_client(private_hosts, remaining)
        with client:
            req = client.build_request(verb, current, headers=req_headers, content=payload.encode() if payload is not None else None)
            res = client.send(req, stream=True)
            try:
                location = res.headers.get("location")
                if res.status_code in _REDIRECTS and location:
                    res.close()
                    if hop >= max_redirects:
                        raise RuntimeError("Too many redirects")
                    nxt = urljoin(current, location)
                    n = urlsplit(nxt)
                    if origin_bound and (n.scheme, n.netloc) != initial_origin:
                        raise RuntimeError("Blocked cross-origin redirect for a protected request")
                    current = check(nxt)
                    if (res.status_code in (301, 302) and verb == "POST") or (res.status_code == 303 and verb not in ("GET", "HEAD")):
                        verb, payload = "GET", None
                        for name in ("content-encoding", "content-language", "content-location", "content-type", "content-length"):
                            req_headers.pop(name, None)
                    hop += 1
                    continue
                chunks: list[bytes] = []
                size = 0
                truncated = False
                for chunk in res.iter_bytes():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("request budget used up")
                    if size + len(chunk) > max_bytes:
                        if not truncate:
                            raise RuntimeError(f"Response too large from {urlsplit(current).hostname}")
                        chunks.append(chunk[: max_bytes - size])
                        truncated = True
                        break
                    size += len(chunk)
                    chunks.append(chunk)
                data = b"".join(chunks)
                return GuardedResponse(res.status_code, current, res.headers, data, truncated)
            finally:
                res.close()
