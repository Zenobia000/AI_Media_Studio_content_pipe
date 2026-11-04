"""Discovery and static files: the site's public files (robots.txt, security.txt, the web manifest, the OpenAPI document), icons, the IndexNow key
and the about page's contact codes. Port of the file-serving half of apps/api/src/routes/static.ts (sitemap and llms.txt follow with the topics)."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from fastapi import FastAPI, Request, Response

from newsroom.api.respond import apply_public_headers, text_with_etag
from newsroom.engine import pack
from newsroom.engine.config import REPO_ROOT, config
from newsroom.engine.http_policy import contact_aliases, public_interface_version
from newsroom.engine.publication.llms import llms_txt, load_llms_availability
from newsroom.engine.publication.sitemap import load_sitemap
from newsroom.engine.site import load_contact
from newsroom.engine.taxonomy import public_api_category_keys

PUBLIC = REPO_ROOT / "site" / "public"
BRAND = REPO_ROOT / "site" / "brand"  # the site's brand files, and how long its icons are cached
ICON_CACHE = "public, max-age=2592000, stale-while-revalidate=604800"
TYPES = {
    ".json": "application/json; charset=UTF-8", ".txt": "text/plain; charset=UTF-8", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".webp": "image/webp", ".ico": "image/x-icon", ".webmanifest": "application/manifest+json",
}  # fmt: skip


def fill_placeholders(text: str, as_json: bool) -> str:
    """A public file's `{{…}}` placeholders: the site's name, address, description, tagline and locale, the reports' edition times, the public
    interface version and the category list (JSON-escaped in JSON files). In a JSON document, an `enum` or `examples` list holding
    "{{categories}}" becomes the list of category keys."""
    site = pack.site()
    times = site["EDITION_TIMES"]
    values = {
        "siteName": site["SITE"]["name"], "siteUrl": config().site_url, "description": site["SITE"]["description"], "tagline": site["SITE"]["tagline"],
        "locale": site["SITE"]["locale"], "dailyTime": times["daily"], "weeklyTime": times["weekly"], "monthlyTime": times["monthly"],
        "version": public_interface_version(), "categoryList": ", ".join(public_api_category_keys()),
    }  # fmt: skip

    def sub(m: re.Match[str]) -> str:
        value = values.get(m.group(1))
        if value is None:
            return m.group(0)
        return json.dumps(value, ensure_ascii=False)[1:-1] if as_json else value

    filled = re.sub(r"\{\{(\w+)\}\}", sub, text)
    if not as_json or '"{{categories}}"' not in filled:
        return filled

    def walk(node) -> None:
        if isinstance(node, dict):
            for key in ("enum", "examples"):
                if isinstance(node.get(key), list) and "{{categories}}" in node[key]:
                    node[key] = list(public_api_category_keys())
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    doc = json.loads(filled)
    walk(doc)
    return json.dumps(doc, ensure_ascii=False, indent=2)


_cache: dict[str, tuple[float, bytes, str]] = {}


def _etag(body: bytes) -> str:
    return f'W/"{hashlib.sha256(body).hexdigest()[:16]}"'


def load_file(file: Path, fill: bool = False) -> tuple[bytes, str]:
    """A file's bytes and ETag, read again when it changes; `fill` fills in a public file's placeholders."""
    mtime = file.stat().st_mtime
    hit = _cache.get(str(file))
    if hit and hit[0] == mtime:
        return hit[1], hit[2]
    body = file.read_bytes()
    if fill and b"{{" in body:
        body = fill_placeholders(body.decode("utf-8"), bool(re.search(r"\.(json|webmanifest)$", str(file)))).encode("utf-8")
    _cache[str(file)] = (mtime, body, _etag(body))
    return body, _etag(body)


def send_entry(request: Request, body: bytes, etag: str, media_type: str, cache_control: str, public_api: bool = False) -> Response:
    response = Response(
        status_code=304 if request.headers.get("if-none-match") == etag else 200,
        content=b"" if request.headers.get("if-none-match") == etag else body,
    )
    if public_api:
        apply_public_headers(response)
    response.headers["Content-Type"] = media_type
    response.headers["Cache-Control"] = cache_control
    response.headers["ETag"] = etag
    return response


def send_file(
    request: Request, file: Path, cache_control: str, media_type: str | None = None, public_api: bool = False, fill: bool = False
) -> Response:
    """A file's bytes with an ETag (304 when it matches), its type from the extension unless given; a missing one is a 404."""
    try:
        body, etag = load_file(file, fill)
    except OSError:
        return Response("Not found", status_code=404, media_type="text/plain; charset=utf-8")
    return send_entry(request, body, etag, media_type or TYPES.get(file.suffix, "application/octet-stream"), cache_control, public_api)


def register_static(app: FastAPI) -> None:
    def send_sitemap(request: Request, file: str) -> Response:
        try:
            doc = load_sitemap(request.app.state.pool)
        except Exception:
            return Response(
                "Sitemap temporarily unavailable", status_code=503,
                headers={"Retry-After": "300", "Cache-Control": "no-store"},
            )  # fmt: skip
        xml = doc["files"].get(file)
        if xml is None:
            return Response("Not found", status_code=404, media_type="text/plain; charset=utf-8")
        seconds = max(0, int((doc["expiresAt"] - time.time() * 1000) // 1000))
        cache = f"public, max-age=0, s-maxage={seconds}, must-revalidate" if seconds > 0 else "no-store"
        return text_with_etag(request, xml, "sitemap", cache, "application/xml")

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap_index(request: Request) -> Response:
        return send_sitemap(request, "sitemap.xml")

    @app.get("/sitemaps/{file}", include_in_schema=False)
    def sitemap_file(request: Request, file: str) -> Response:
        return send_sitemap(request, file)

    @app.get("/llms.txt", include_in_schema=False)
    def llms(request: Request) -> Response:
        text = llms_txt(load_llms_availability(request.app.state.pool))
        # Cached like /openapi-v1.json: a release that adds an ability is described everywhere within minutes.
        response = text_with_etag(request, text, "llms", "public, max-age=300, must-revalidate", "text/plain; charset=utf-8")
        apply_public_headers(response, cors=False)
        return response

    # The site's public files (site/public/), placeholders filled in; one the site does not have is a 404.
    def public_file(url: str, relative: str, cache: str, public_api: bool = False) -> None:
        def handler(request: Request) -> Response:
            return send_file(request, PUBLIC / relative, cache, public_api=public_api, fill=True)

        app.add_api_route(url, handler, methods=["GET"], include_in_schema=False)

    public_file("/robots.txt", "robots.txt", "public, max-age=3600")
    public_file("/.well-known/security.txt", ".well-known/security.txt", "public, max-age=86400")
    # Cached as long as the icons it names: every page links it.
    public_file("/manifest.webmanifest", "manifest.webmanifest", ICON_CACHE)
    public_file("/openapi-v1.json", "openapi-v1.json", "public, max-age=300, must-revalidate", public_api=True)

    # IndexNow proves the key by a file at the site root named after it (INDEXNOW_KEY).
    key = config().index_now_key
    if key:
        body = key.encode()

        def indexnow(request: Request) -> Response:
            return send_entry(request, body, _etag(body), TYPES[".txt"], "public, max-age=3600")

        app.add_api_route(f"/{key}.txt", indexnow, methods=["GET"], include_in_schema=False)

    # The site's icons (site/brand/): the standard ones, then any others it keeps at the root.
    for icon in ["favicon.ico", "icon.png", "icon-192.png", "apple-icon.png", "logo.svg", *pack.site()["SITE"]["rootIcons"]]:

        def icon_handler(request: Request, _icon=icon) -> Response:
            return send_file(request, BRAND / _icon, ICON_CACHE)

        app.add_api_route(f"/{icon}", icon_handler, methods=["GET"], include_in_schema=False)

    # Contact codes' root addresses linked from outside lead to the codes the about page shows now (replacing a code changes its file name).
    for alias in contact_aliases():

        def alias_handler(request: Request, _slot=alias["slot"]) -> Response:
            with request.app.state.pool.connection() as conn:
                contact = load_contact(conn)
            return Response(status_code=302, headers={"Location": contact[f"{_slot}Qr"], "Cache-Control": "public, max-age=3600"})

        app.add_api_route(f"/{alias['file']}", alias_handler, methods=["GET"], include_in_schema=False)

    # Contact codes on the about page: uploaded in the admin (content-hashed names), or shipped in the pack.
    @app.get("/contact/{file}", include_in_schema=False)
    def contact_file(request: Request, file: str) -> Response:
        if not re.fullmatch(r"[\w.-]+\.(png|jpg|jpeg|webp)", file):
            return Response(status_code=404)
        hashed = bool(re.search(r"-[0-9a-f]{8}\.", file))
        cache = "public, max-age=31536000, immutable" if hashed else "public, max-age=3600"
        uploaded = config().data_dir / "uploads" / file
        return send_file(request, uploaded if uploaded.exists() else BRAND / "contact" / file, cache)
