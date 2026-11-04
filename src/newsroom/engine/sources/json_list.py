"""JSON sources: plain JSON APIs, JSON embedded in HTML (script tags, window variables). Port of sources/json-list.ts."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from newsroom.engine.config import credential
from newsroom.engine.lib.http_fetch import language_headers, require_ok
from newsroom.engine.lib.text import collapse_whitespace, strip_tags
from newsroom.engine.sources.dates import PublicationDate, read_publication_date
from newsroom.engine.sources.listing_fetch import fetch_listing
from newsroom.engine.sources.rss import FetchError
from newsroom.engine.sources.xmlparse import js_number_text

JINA_PREFIX = "https://r.jina.ai/"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def js_str(v) -> str:
    """String(v) in JavaScript, for what a JSON document can hold."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v) if abs(v) <= 2**53 else js_number_text(repr(float(v)))
    if isinstance(v, float):
        return js_number_text(repr(v))
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return ",".join("" if x is None else js_str(x) for x in v)
    return "[object Object]"


def _parse_int(s: str):
    n = int(s)
    return n if abs(n) <= 2**53 else float(n)


def _reject_constant(name: str):
    raise ValueError(name)


def json_parse(text: str):
    """JSON.parse: integers beyond 2^53 lose digits as numbers do, and NaN/Infinity are not JSON."""
    return json.loads(text, parse_int=_parse_int, parse_constant=_reject_constant)


def get_path(obj, path: str):
    if not path:
        return obj
    cur = obj
    for seg in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            if re.fullmatch(r"\d+", seg, re.A):
                i = int(seg)
                cur = cur[i] if i < len(cur) else None
            elif seg == "length":
                cur = len(cur)
            else:
                return None
        elif isinstance(cur, dict):
            cur = cur.get(seg)
        else:
            return None
    return cur


def _first_string(obj, paths: list[str] | None) -> str | None:
    for p in paths or []:
        v = get_path(obj, p)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return js_str(v)
    return None


def _encode_uri_component(s: str) -> str:
    return quote(s, safe="!~*'()-_.", errors="replace")


def render_template(template: str, item) -> str | None:
    """"{path}" → encoded value, "{raw:path}" → raw value. None when a referenced value is missing."""
    missing = False

    def sub(m: re.Match[str]) -> str:
        nonlocal missing
        v = get_path(item, m.group(2))
        if v is None or v == "":
            missing = True
            return ""
        return js_str(v) if m.group(1) else _encode_uri_component(js_str(v)).replace("%2F", "/")

    out = re.sub(r"\{(raw:)?([^}]+)\}", sub, template)
    return None if missing else out


def to_date(v, unit: str | None, utc_offset: str | None) -> PublicationDate:
    """Calendar days and exact epoch/ISO times preserve the source's stated precision."""
    unknown = PublicationDate(None, None)
    if v is None or v == "":
        return unknown
    text = js_str(v).strip()
    if unit in ("epoch_ms", "epoch_s"):
        try:
            ms = float(text) * (1000 if unit == "epoch_s" else 1)
            if ms != ms or abs(ms) > 8.64e15:
                return unknown
            date = _EPOCH + timedelta(milliseconds=int(ms) if ms >= 0 else -int(-ms))
        except (ValueError, OverflowError):
            return unknown
        return PublicationDate(date, date.strftime("%Y-%m-%dT%H:%M:%S.") + f"{date.microsecond // 1000:03d}Z")
    if unit == "yyyymmdd":
        m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text, re.A)
        return read_publication_date(f"{m[1]}-{m[2]}-{m[3]}", utc_offset or "+08:00") if m else unknown
    return read_publication_date(text, utc_offset or "+08:00")


def _find_key(obj, key: str, depth: int = 0):
    if depth > 12 or not isinstance(obj, (dict, list)):
        return None
    if isinstance(obj, dict) and key in obj and isinstance(obj[key], list):
        return obj[key]
    for v in obj.values() if isinstance(obj, dict) else obj:
        found = _find_key(v, key, depth + 1)
        if found is not None:
            return found
    return None


def literal_end(text: str, start: int) -> int:
    """Where the object or array literal starting at `start` ends (exclusive), skipping brackets inside strings; -1 when it does not."""
    depth, in_str, esc = 0, None, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == in_str:
                in_str = None
            continue
        if ch in "\"'":
            in_str = ch
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def _escape_regex(s: str) -> str:
    return re.sub(r"[.*+?^${}()|\[\]\\]", lambda m: "\\" + m.group(0), s)


def embedded_json(html: str, source: dict):
    cfg = source["config"]
    if cfg.get("mode") == "html_window_var":
        name = js_str(cfg.get("windowVar"))
        m = re.compile(r"(?:window\.)?" + name.replace("$", "\\$") + r"\s*=\s*").search(html)
        if not m:
            raise FetchError(f"window.{name} not found")
        start = m.end()
        end = literal_end(html, start)
        if end > 0:
            return json_parse(html[start:end])
        raise FetchError(f"window.{name} not terminated")
    # html_json_key: scan JSON script blocks (e.g. __NEXT_DATA__) for the key.
    key = js_str(cfg.get("jsonKey"))
    for m in re.finditer(r"<script[^>]*>([\s\S]*?)</script>", html, re.I):
        body = m.group(1).strip()
        # Flight payloads carry the key escaped inside a string (\"items\").
        if f'"{key}"' not in body and f'\\"{key}\\"' not in body:
            continue
        for c in (body, re.sub(r";?\s*$", "", re.sub(r"^[^{\[]*", "", body))):
            try:
                found = _find_key(json_parse(c), key)
                if found:
                    return {key: found}
            except ValueError:
                pass  # Next.js flight payloads: self.__next_f.push([1,"..."])
        flight = re.search(r'"((?:[^"\\]|\\.)*)"\]\)\s*$', body)
        if flight:
            try:
                decoded = json_parse(f'"{flight.group(1)}"')
                at = re.search(f'"{_escape_regex(key)}"\\s*:\\s*\\[', decoded)
                start = at.end() - 1 if at else -1
                end = -1 if start < 0 else literal_end(decoded, start)
                if end > 0:
                    return {key: json_parse(decoded[start:end])}
            except ValueError:
                pass  # keep scanning
    raise FetchError(f"embedded key {key} not found")


def fetch_json_list(source: dict) -> list[dict]:
    c = source["config"]
    url = js_str(c.get("url") if c.get("url") is not None else "")
    headers = {"accept": "application/json, text/html;q=0.9", **language_headers(c.get("acceptLanguage")), **(c.get("headers") or {})}
    if re.match(r"^https://api\.github\.com/", url):
        token = credential("collectors", "GITHUB_TOKEN")
        if token:
            headers["authorization"] = f"Bearer {token}"
    embedded = c.get("mode") in ("html_json_key", "html_window_var")
    if embedded and url.startswith(JINA_PREFIX):
        raise NotImplementedError("reading a listing through Jina needs the paid-request receipts, which are not ported yet")
    body_json = c.get("bodyJson")
    res = fetch_listing(
        url,
        redirect_policy="same-origin",
        method=c.get("method") or "GET",
        headers={**headers, "content-type": "application/json"} if body_json else headers,
        body=json.dumps(body_json, ensure_ascii=False, separators=(",", ":")) if body_json else None,
        timeout_ms=25_000,
    )
    text = require_ok(res).text()
    if embedded:
        data = embedded_json(text, source)
    else:
        try:
            data = json_parse(text)
        except ValueError:
            raise FetchError("response is not JSON") from None
    items = get_path(data, c["itemsPath"]) if c.get("itemsPath") else get_path(data, c["jsonKey"]) if c.get("jsonKey") else data
    if c.get("itemsObjectValues") and isinstance(items, dict):
        items = list(items.values())
    if not isinstance(items, list):
        raise FetchError("items path did not resolve to an array")

    out: list[dict] = []
    for item in items:
        rb, mn = c.get("requireBoolean"), c.get("minNumeric")
        if rb and get_path(item, rb["path"]) != rb.get("equals"):
            continue
        if mn and not _at_least(get_path(item, mn["path"]), mn.get("min")):
            continue
        title = _first_string(item, c.get("titlePaths"))
        link = (c.get("urlTemplate") and render_template(c["urlTemplate"], item)) or (
            c.get("urlTemplateFallback") and render_template(c["urlTemplateFallback"], item)
        )
        if not title or not link:
            continue
        external_id = get_path(item, c["externalIdPath"]) if c.get("externalIdPath") else None
        summary = _first_string(item, c.get("summaryPaths"))
        summary_is_body = c.get("summaryIsBody") is True and bool(summary)
        pub = to_date(get_path(item, c["publishedAtPath"]) if c.get("publishedAtPath") else None, c.get("publishedAtUnit"), c.get("publishedAtUtcOffset"))
        out.append({
            "url": link,
            "title": collapse_whitespace(strip_tags(title)),
            "author": _first_string(item, c.get("authorPaths")),
            "published_at": pub.published_at,
            "publication_time": pub.publication_time,
            "excerpt": collapse_whitespace(strip_tags(summary))[:2000] if summary else None,
            "body_text": strip_tags(summary) if summary_is_body else None,
            "body_status": "ok" if summary_is_body else "pending",
            "raw": {"externalId": external_id},
        })  # fmt: skip
    if len(items) > 0 and not out and not c.get("requireBoolean") and not c.get("minNumeric"):
        raise FetchError("no items mapped (check title/url paths)")
    return out


def _js_number(v) -> float:
    """Number(v) for a JSON value."""
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return 0.0
        try:
            return float(s) if re.fullmatch(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|[-+]?Infinity", s, re.A) else float("nan")
        except ValueError:
            return float("nan")
    if isinstance(v, list):
        return _js_number(v[0]) if len(v) == 1 else (0.0 if not v else float("nan"))
    return float("nan")


def _at_least(value, minimum) -> bool:
    return _js_number(value) >= _js_number(minimum)
