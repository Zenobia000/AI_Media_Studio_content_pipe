"""Outward HTTP behaviour, defined once: CORS, the public interface version, redirects and which process owns a path. Port of
packages/contracts/src/http-policy.ts. The site has no modules of its own now, so the module tables are empty."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from newsroom.engine import pack
from newsroom.engine.config import config

NO_STORE = "no-store"


def public_interface_version() -> str:
    """MCP and the v1 OpenAPI carry one public version, the site's; it only goes up."""
    return pack.site()["SITE"]["interfaceVersion"]


def public_api_cors() -> dict[str, str]:
    """CORS for /api/v1/* and /openapi-v1.json."""
    terms = pack.site()["POLICY"]["terms"].get("headers") or {}
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "Accept, If-None-Match, If-Modified-Since",
        "Access-Control-Expose-Headers": ", ".join(["ETag", "Last-Modified", "Retry-After", "X-Request-Id", "Deprecation", "Sunset", "Link", *terms]),
        "Access-Control-Max-Age": "86400",
    }


@dataclass
class RedirectRule:
    match: str  # exact | regex | prefix
    path: str
    status: int
    location: str | None = None  # relative; `$1`… refer to regex groups, `*` appends the remainder for prefix rules
    keep_query: bool = False
    fragment: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


ENGINE_REDIRECTS = [
    RedirectRule("regex", r"^/(all|about|agent|changelog|feedback|starred|more|privacy|terms)/+$", 301, "/$1", keep_query=True),
    # A subscription address may carry its reader's own query, which the Location repeats: the redirect never enters shared caches.
    RedirectRule("regex", r"^/(feed|rss|rss\.xml|atom\.xml)$", 301, "/feed.xml", keep_query=True, headers={"Cache-Control": "private, no-store"}),
]


@dataclass
class RedirectDecision:
    status: int
    location: str | None
    headers: dict[str, str]


def _expand(template: str, m: re.Match[str]) -> str:
    """`$1`… as the groups of the match (`$0` is the whole match; a group that does not exist is empty), like the TypeScript table."""
    return re.sub(r"\$(\d)", lambda g: (m.group(int(g.group(1))) or "") if int(g.group(1)) <= m.re.groups else "", template)


def resolve_redirect(pathname: str, search: str) -> RedirectDecision | None:
    for rule in ENGINE_REDIRECTS:
        location: str | None = None
        if rule.match == "exact":
            if pathname != rule.path:
                continue
            location = rule.location
        elif rule.match == "prefix":
            in_tree = pathname.startswith(rule.path) if rule.path.endswith("/") else pathname == rule.path or pathname.startswith(f"{rule.path}/")
            if not in_tree:
                continue
            location = rule.location.replace("*", pathname[len(rule.path) :], 1) if rule.location else None
        else:
            m = re.search(rule.path, pathname)
            if not m:
                continue
            location = _expand(rule.location, m) if rule.location else None
        if location and rule.keep_query and search:
            location += search
        if location and rule.fragment:
            location += f"#{rule.fragment}"
        return RedirectDecision(rule.status, location, dict(rule.headers))
    return None


OAUTH_PROBE_PATHS = [
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/api/mcp",
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-authorization-server/api/mcp",
    "/.well-known/openid-configuration",
    "/.well-known/openid-configuration/api/mcp",
    "/api/mcp/.well-known/oauth-protected-resource",
    "/api/mcp/.well-known/oauth-authorization-server",
    "/api/mcp/.well-known/openid-configuration",
]


def _literal(text: str) -> str:
    return re.escape(text)


def contact_aliases() -> list[dict[str, str]]:
    """Root addresses of the about page's contact codes that were linked from outside (ABOUT.maker), served by the static routes."""
    maker = pack.site()["ABOUT"].get("maker") or {}
    return [{"slot": slot, "file": maker[slot]["alias"]} for slot in ("wechat", "feishu") if (maker.get(slot) or {}).get("alias")]


def api_owned_patterns() -> list[re.Pattern[str]]:
    """Paths served by the api process; everything else is the web process. Patterns are anchored so page paths such as /feedback never
    fall into /feed."""
    site = pack.site()["SITE"]
    patterns = [
        r"^/api/",
        r"^/feed(\.xml|/.*)?$",
        r"^/(rss|rss\.xml|atom\.xml)$",
        r"^/openapi-v1\.json$",
        r"^/(llms\.txt|robots\.txt|sitemap\.xml|manifest\.webmanifest)$",
        r"^/sitemaps/",
        r"^/\.well-known/",
        r"^/(favicon\.ico|icon\.png|icon-192\.png|apple-icon\.png|logo\.svg)$",
    ]
    if site["rootIcons"]:
        patterns.append(rf"^/({'|'.join(_literal(i) for i in site['rootIcons'])})$")
    patterns += [r"^/(og|contact)/"]
    aliases = contact_aliases()
    if aliases:
        patterns.append(rf"^/({'|'.join(_literal(a['file']) for a in aliases)})$")
    patterns += [r"^/[0-9a-f]{32}\.txt$", r"^/items/[^/]+/markdown$"]
    return [re.compile(p) for p in patterns]


def is_api_owned(pathname: str) -> bool:
    return any(p.search(pathname) for p in api_owned_patterns())


def site_url(path: str) -> str:
    """Absolute links always use the configured address (SITE_URL), never the request Host: a CDN or proxy may send a different Host."""
    return f"{config().site_url}{path}"
