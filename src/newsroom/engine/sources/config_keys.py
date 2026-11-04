"""The config keys each kind of source implements. Anything else is refused: a key a collector does not know would otherwise fall back silently to
the generic parse. Port of sources/config-keys.ts."""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlsplit

PUBLISHER = ["publisherRole", "publisherUrlPrefixes", "publisherRequiresDiscovery"]
COLLECTED = [
    *PUBLISHER, "_aihot", "allowUrlPrefixes", "denyUrlPrefixes", "ingestNoiseFilter", "itemUrlPrefixRewrite", "sortByPublishedAt", "detail",
    "fetchPublicContent", "publishedAfter", "acceptLanguage",
]  # fmt: skip

KEYS: dict[str, list[str]] = {
    "rss": [*COLLECTED, "feedUrl", "summaryIsBody", "preserveUrlFragment", "allowCategories", "denyCategories", "publishedAtUtcOffset"],
    "web_list": [
        *COLLECTED, "url", "baseUrl", "parseMode", "adapter", "cacheToleranceSeconds", "linksStartLine", "preserveUrlFragment",
        "itemSelector", "linkSelector", "titleSelector", "publishedAtSelector", "publishedAtRegex", "publishedAtUtcOffset",
    ],
    "json_list": [
        *COLLECTED, "url", "mode", "method", "headers", "bodyJson", "jsonKey", "windowVar", "itemsPath", "itemsObjectValues",
        "titlePaths", "summaryPaths", "summaryIsBody", "authorPaths", "publishedAtPath", "publishedAtUnit", "publishedAtUtcOffset", "externalIdPath",
        "urlTemplate", "urlTemplateFallback", "rawDropKeys", "requireBoolean", "minNumeric",
    ],
    "x_search": [*PUBLISHER, "_aihot", "ingestNoiseFilter", "itemUrlPrefixRewrite", "query", "searchType"],
    "mp_account": [*PUBLISHER, "wxid", "ghid", "nickname"],
    "external": [*PUBLISHER],
}  # fmt: skip

NESTED: dict[str, list[str]] = {
    "_aihot": ["initialBackfillLimit", "initialBackfillMonths"],
    "ingestNoiseFilter": ["dropMarkers", "dropMarkersTitleOnly", "keepIfMatches"],
    "itemUrlPrefixRewrite": ["from", "to"],
    "requireBoolean": ["path", "equals"],
    "minNumeric": ["path", "min"],
    "detail": [
        "maxFetches", "publishedAtSelector", "publishedAtRegex", "publishedAtUtcOffset", "publishedAtAuthoritative", "upgradeDatePrecision",
        "titleSelector", "titleRegex", "titleAuthoritative", "summarySelector", "preserve",
    ],
}  # fmt: skip

VALUES: dict[str, list[str]] = {
    "publisherRole": ["organization", "person"],
    "adapter": ["mimo_home"],
    "parseMode": ["html", "markdown", "docusaurus_changelog", "intercom_changelog"],
}

_LANGUAGE_RANGE = r"(?:[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*|\*)(?:;q=(?:0(?:\.\d{1,3})?|1(?:\.0{1,3})?))?"
_ACCEPT_LANGUAGE = re.compile(rf"{_LANGUAGE_RANGE}(?: *, *{_LANGUAGE_RANGE})*", re.A)


def _js_str(v) -> str:
    from newsroom.engine.sources.json_list import js_str

    return js_str(v)


def _preserve_rules(value) -> bool:
    if not isinstance(value, list):
        return False
    for rule in value:
        if not isinstance(rule, dict):
            return False

        def selector(key: str, rule=rule) -> bool:
            return isinstance(rule.get(key), str) and rule[key].strip() != ""

        parts = ["code", "caption"] if rule.get("as") == "pre" else [] if rule.get("as") == "table" else None
        if parts is None or not selector("selector") or not all(k in ("selector", "as") or (k in parts and selector(k)) for k in rule):
            return False
    return True


def _utc_instant(value) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z", value, re.A):
        return False
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z" == (value if "." in value else value[:-1] + ".000Z")


def _publisher_prefix(v) -> bool:
    if not isinstance(v, str):
        return False
    try:
        u = urlsplit(v)
        if u.scheme not in ("http", "https") or not u.hostname:
            return False
        return not u.username and not u.password and not u.query and not u.fragment
    except ValueError:
        return False


def unsupported_config(kind: str, config: dict | None) -> list[str]:
    """The config entries a source of this kind would ignore or cannot run, e.g. ["adapter=site_cards", "detail.titleFoo"]."""
    allowed = set(KEYS.get(kind, []))
    config = config or {}
    out: list[str] = []
    for key, value in config.items():
        if key not in allowed:
            out.append(key)
        elif key == "publisherRequiresDiscovery" and (
            not isinstance(value, bool) or (value and (not isinstance(config.get("publisherUrlPrefixes"), list) or len(config["publisherUrlPrefixes"]) == 0))
        ):
            out.append(key)
        elif key == "publishedAfter" and not _utc_instant(value):
            out.append(key)
        elif key == "acceptLanguage" and (not isinstance(value, str) or not _ACCEPT_LANGUAGE.fullmatch(value)):
            out.append(key)
        elif key == "publisherUrlPrefixes" and (not isinstance(value, list) or not all(_publisher_prefix(v) for v in value)):
            out.append(key)
        elif key in VALUES and _js_str(value) not in VALUES[key]:
            out.append(f"{key}={_js_str(value)}")
        elif key in NESTED and value and isinstance(value, (dict, list)):
            keys = [str(i) for i in range(len(value))] if isinstance(value, list) else list(value)
            out.extend(f"{key}.{sub}" for sub in keys if sub not in NESTED[key])
            if key == "detail" and isinstance(value, dict) and "preserve" in value and not _preserve_rules(value["preserve"]):
                out.append("detail.preserve")
    return out


class UnsupportedConfig(Exception):
    status_code = 400


def assert_supported_config(kind: str, config: dict) -> None:
    """Refuses a config with entries its kind does not implement (admin create, edit and preview)."""
    bad = unsupported_config(kind, config)
    if bad:
        raise UnsupportedConfig(f"不支援的配置項：{'、'.join(bad)}")
