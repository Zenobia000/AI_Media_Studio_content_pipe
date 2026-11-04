"""The request validation the admin's sources use (zod in the TypeScript), with its messages: each issue is "<path>: <message>", joined by "; "."""

from __future__ import annotations

import re

from newsroom.engine.errors import Invalid

MISSING = object()


def received(value) -> str:
    if value is MISSING:
        return "undefined"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


class Issues:
    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []

    def add(self, path: str, message: str) -> None:
        self.items.append((path, message))

    def raise_if_any(self) -> None:
        if self.items:
            raise Invalid("; ".join(f"{p or '請求'}: {m}" for p, m in self.items)[:300])


def string(v, issues: Issues, path: str, *, min_len: int | None = None, max_len: int | None = None, pattern: re.Pattern | None = None, trim: bool = False, message: str | None = None):
    if not isinstance(v, str):
        issues.add(path, f"Invalid input: expected string, received {received(v)}")
        return None
    if trim:
        v = v.strip()
    if min_len is not None and len(v) < min_len:
        issues.add(path, f"Too small: expected string to have >={min_len} characters")
    if max_len is not None and len(v) > max_len:
        issues.add(path, f"Too big: expected string to have <={max_len} characters")
    if pattern is not None and not pattern.search(v):
        issues.add(path, message or f"Invalid string: must match pattern /{pattern.pattern}/")
    return v


def boolean(v, issues: Issues, path: str):
    if not isinstance(v, bool):
        issues.add(path, f"Invalid input: expected boolean, received {received(v)}")
        return None
    return v


def integer(v, issues: Issues, path: str, lo: int, hi: int):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        issues.add(path, f"Invalid input: expected number, received {received(v)}")
        return None
    if isinstance(v, float) and not v.is_integer():
        issues.add(path, "Invalid input: expected int, received number")
        return None
    if v < lo:
        issues.add(path, f"Too small: expected number to be >={lo}")
    if v > hi:
        issues.add(path, f"Too big: expected number to be <={hi}")
    return int(v)


def enum(v, issues: Issues, path: str, options: list[str]):
    if not isinstance(v, str) or v not in options:
        issues.add(path, "Invalid option: expected one of " + "|".join(f'"{o}"' for o in options))
        return None
    return v


def record(v, issues: Issues, path: str):
    if not isinstance(v, dict):
        issues.add(path, f"Invalid input: expected record, received {received(v)}")
        return None
    return v


def string_array(v, issues: Issues, path: str, max_items: int | None = None, max_len: int | None = None):
    if not isinstance(v, list):
        issues.add(path, f"Invalid input: expected array, received {received(v)}")
        return None
    for i, item in enumerate(v):
        string(item, issues, f"{path}.{i}", max_len=max_len)
    if max_items is not None and len(v) > max_items:
        issues.add(path, f"Too big: expected array to have <={max_items} items")
    return v


def strict_keys(data: dict, known: list[str], issues: Issues) -> None:
    extra = [k for k in data if k not in known]
    if extra:
        issues.add("", ("Unrecognized key: " if len(extra) == 1 else "Unrecognized keys: ") + ", ".join(f'"{k}"' for k in extra))


def object_input(data, issues: Issues) -> dict | None:
    if not isinstance(data, dict):
        issues.add("", f"Invalid input: expected object, received {received(data)}")
        return None
    return data


def number(v, issues: Issues, path: str, lo: float | None = None, hi: float | None = None):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        issues.add(path, f"Invalid input: expected number, received {received(v)}")
        return None
    if lo is not None and v < lo:
        issues.add(path, f"Too small: expected number to be >={lo:g}")
    if hi is not None and v > hi:
        issues.add(path, f"Too big: expected number to be <={hi:g}")
    return v
