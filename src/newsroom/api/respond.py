"""Shared HTTP helpers: Problem JSON, public API headers, ETag / 304, strict query parsing. Port of apps/api/src/http/respond.ts."""

from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qsl

from fastapi import Request, Response

from newsroom.engine import pack
from newsroom.engine.http_policy import NO_STORE, public_api_cors, site_url

PROBLEM_TITLES = {
    400: "Bad request", 403: "Forbidden", 404: "Not found", 405: "Method not allowed", 409: "Conflict", 429: "Too many requests",
    500: "Internal error", 503: "Temporarily unavailable",
}  # fmt: skip


def js_json(value) -> str:
    """JSON.stringify: no spaces, non-ASCII kept."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def problem(req: Request, status: int, code: str, detail: str, type_: str | None = None, title: str | None = None,
            retry_after: int | None = None, cache_control: str | None = None) -> Response:  # fmt: skip
    body: dict = {
        "type": type_ or f"/problems/{code.replace('_', '-')}",
        "title": title or PROBLEM_TITLES.get(status, "Error"),
        "status": status,
        "detail": detail,
        "code": code,
        "requestId": req.state.request_id,
    }
    headers = {"X-Request-Id": req.state.request_id, "Cache-Control": cache_control or NO_STORE}
    if retry_after is not None:
        body["retryAfter"] = retry_after
        headers["Retry-After"] = str(retry_after)
    return Response(js_json(body).encode(), status_code=status, media_type="application/problem+json", headers=headers)


def apply_public_headers(response: Response, cors: bool = True, deprecation: dict | None = None) -> None:
    """CORS for public machine endpoints, and the site's usage-policy headers (POLICY.terms.headers)."""
    if cors:
        for k, v in public_api_cors().items():
            response.headers[k] = v
    links: list[str] = []
    terms = pack.site()["POLICY"]["terms"].get("headers")
    if terms:
        for k, v in terms.items():
            response.headers[k] = v
        links.append(f'<{site_url("/terms")}>; rel="terms-of-service"')
    if deprecation:
        response.headers["Deprecation"] = "true"
        response.headers["Sunset"] = deprecation["sunset"]
        links.append(f'<{deprecation["guide"]}>; rel="deprecation"')
    if links:
        response.headers["Link"] = ", ".join(links)


def weak_etag(prefix: str, body: str) -> str:
    return f'W/"{prefix}-{hashlib.sha256(body.encode()).hexdigest()[:16]}"'


def etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False

    def strip(t: str) -> str:
        t = t.strip()
        return t[2:] if t.startswith("W/") else t

    return any(t.strip() == "*" or strip(t) == strip(etag) for t in header.split(","))


def json_with_etag(req: Request, body, etag_prefix: str, cache_control: str, etag_of=None) -> Response:
    """Sends JSON with a weak ETag; answers 304 with an empty body when If-None-Match matches. `etag_of` names the content the tag stands for
    when the body also carries per-request values (a snapshot's `asOf`), so unchanged content still answers 304."""
    text = js_json(body) if etag_of is None else None
    etag = weak_etag(etag_prefix, text if text is not None else js_json(etag_of))
    headers = {"ETag": etag, "Cache-Control": cache_control, "Vary": "Accept-Encoding"}
    if etag_matches(req.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(text if text is not None else js_json(body), media_type="application/json", headers=headers)


def text_with_etag(req: Request, text: str, etag_prefix: str, cache_control: str, content_type: str) -> Response:
    etag = weak_etag(etag_prefix, text)
    headers = {"ETag": etag, "Cache-Control": cache_control, "Vary": "Accept-Encoding"}
    if etag_matches(req.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(text, headers={**headers, "Content-Type": content_type})


class QueryError(Exception):
    pass


def _raw_query(req: Request) -> list[tuple[str, str]]:
    return parse_qsl(req.url.query, keep_blank_values=True)


def strict_query(req: Request, allowed: list[str]) -> dict[str, str]:
    """Strict query parsing for public APIs: only declared parameters, each at most once. Unknown parameters (including cache busters like
    `_`) and duplicates are 400."""
    out: dict[str, str] = {}
    for key, value in _raw_query(req):
        if key not in allowed:
            raise QueryError(f"Unknown query parameter: {key}.")
        if key in out:
            raise QueryError(f"Query parameter must not be repeated: {key}.")
        if "\0" in value:
            raise QueryError("Query parameters must not contain NUL.")
        out[key] = value
    return out


def loose_query(req: Request) -> dict[str, str]:
    """Lenient parsing for site endpoints: first value wins, unknown keys ignored."""
    out: dict[str, str] = {}
    for key, value in _raw_query(req):
        if key not in out:
            if "\0" in value:
                raise QueryError("Query parameters must not contain NUL.")
            out[key] = value
    return out


def client_ip(req: Request) -> str:
    """The client's address as nginx passes it on (X-Real-IP), else the connection's."""
    return req.headers.get("x-real-ip") or (req.client.host if req.client else "")
