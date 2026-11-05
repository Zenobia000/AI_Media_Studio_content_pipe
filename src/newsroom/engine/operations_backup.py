"""Database and file backups: a verified custom-format dump plus the uploaded files, sent to the existing object store (Tencent COS through its S3-compatible API, AWS
Signature V4) under one retention prefix: monthly/ on the 1st, otherwise weekly/ on Sundays or daily/. Port of operations/backup.ts."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlsplit, urlunsplit

import httpx
from psycopg.types.json import Jsonb

from newsroom.engine.config import config
from newsroom.engine.lib.text import js_slice
from newsroom.engine.time import beijing_date, beijing_weekday

KEEP_LOCAL = 3


def store() -> dict | None:
    """The object store, from the environment (backend.env)."""
    secret_id, secret_key = os.environ.get("DB_BACKUP_STORE_SECRET_ID"), os.environ.get("DB_BACKUP_STORE_SECRET_KEY")
    bucket, region = os.environ.get("DB_BACKUP_STORE_BUCKET"), os.environ.get("DB_BACKUP_STORE_REGION")
    if not (secret_id and secret_key and bucket and region):
        return None
    domain = os.environ.get("DB_BACKUP_STORE_DOMAIN")
    endpoint = f"https://{re.sub('^https?://', '', domain)}" if domain else f"https://{bucket}.cos.{region}.myqcloud.com"
    return {"secretId": secret_id, "secretKey": secret_key, "bucket": bucket, "region": region, "endpoint": endpoint}


def backup_configured() -> bool:
    """Backups run where the object store is configured (production); development never uploads."""
    return store() is not None


def _hex(b: bytes | str) -> str:
    return hashlib.sha256(b.encode() if isinstance(b, str) else b).hexdigest()


def _hmac(key: bytes | str, data: str) -> bytes:
    return hmac.new(key.encode() if isinstance(key, str) else key, data.encode(), hashlib.sha256).digest()


def _uri_encode(s: str) -> str:
    return quote(s, safe="-_.!~*'()")  # encodeURIComponent


def sign_v4(
    method: str, url: str, region: str, access_key: str, secret_key: str, payload_hash: str, headers: dict | None = None, now: datetime | None = None
) -> dict:
    """AWS Signature V4 headers for one S3 request."""
    parts = urlsplit(url)
    now = now or datetime.now(UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    day = amz_date[:8]
    all_headers = {**(headers or {}), "host": parts.netloc, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
    names = sorted(h.lower() for h in all_headers)
    lower = {k.lower(): str(v).strip() for k, v in all_headers.items()}
    query = "&".join(f"{_uri_encode(k)}={_uri_encode(v)}" for k, v in sorted(parse_qsl(parts.query, keep_blank_values=True)))
    path = "/".join(_uri_encode(unquote(seg)) for seg in parts.path.split("/"))
    canonical = "\n".join([method, path, query, "".join(f"{n}:{lower[n]}\n" for n in names), ";".join(names), payload_hash])
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, _hex(canonical)])
    k_signing = _hmac(_hmac(_hmac(_hmac(f"AWS4{secret_key}", day), region), "s3"), "aws4_request")
    signature = hmac.new(k_signing, to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        **all_headers,
        "authorization": f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={';'.join(names)}, Signature={signature}",
    }


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _upload(s: dict, key: str, path: Path, sha: str, size: int) -> None:
    url = f"{s['endpoint']}/{key}"
    headers = sign_v4(
        "PUT", url, s["region"], s["secretId"], s["secretKey"], sha, {"content-length": str(size), "content-type": "application/octet-stream"}
    )
    with path.open("rb") as f:
        res = httpx.put(url, headers=headers, content=f, timeout=60 * 60)
    if not res.is_success:
        raise RuntimeError(f"backup upload {key}: HTTP {res.status_code} {js_slice(res.text, 0, 300)}")


def _pg_env(database_url: str) -> tuple[dict, str]:
    """Credentials go to libpq through its environment so neither process listings nor a failed job's stored error contain them; other connection options stay."""
    parts = urlsplit(database_url)
    env = dict(os.environ)
    if parts.username:
        env["PGUSER"] = unquote(parts.username)
    if parts.password:
        env["PGPASSWORD"] = unquote(parts.password)
    kept = []
    for option in parts.query.split("&") if parts.query else []:
        i = option.find("=")
        key = unquote(option[:i])
        if key in ("user", "password"):
            env["PGUSER" if key == "user" else "PGPASSWORD"] = unquote(option[i + 1 :])
        else:
            kept.append(option)
    host = parts.hostname or ""
    netloc = (f"[{host}]" if ":" in host else host) + (f":{parts.port}" if parts.port else "")
    return env, urlunsplit((parts.scheme, netloc, parts.path, "&".join(kept), parts.fragment))


def run_backup(pool, now: datetime | None = None) -> dict:
    from newsroom.engine.operations import screenshots_forwarded

    now = now or datetime.now(UTC)
    s = store()
    cfg = config()
    directory = cfg.data_dir / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = re.sub(r"[-:T]", "", now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M"))
    # Named after the database ("news_db" → news-db-…), so two databases backed up into one store stay apart.
    name = re.sub(r"[^a-z0-9]+", "-", urlsplit(cfg.database_url).path.lstrip("/").lower())
    dump = directory / f"{name}-{stamp}.dump"
    env, url = _pg_env(cfg.database_url)
    subprocess.run(["pg_dump", "--format=custom", "--compress=6", "--no-owner", "--file", str(dump), url], env=env, check=True, capture_output=True)
    # Verify before shipping: the archive must list cleanly.
    subprocess.run(["pg_restore", "--list", str(dump)], check=True, capture_output=True)
    files = directory / f"{name}-files-{stamp}.tar.gz"
    kept: list[str] = []
    # Feedback screenshots only waiting to be forwarded are not kept (once forwarded, only the Feishu image key remains). Without the internal chat they stay here for
    # good, referred to as local: files.
    files_error = None
    try:
        for d in ["uploads"] if screenshots_forwarded() else ["uploads", "feedback-screenshots"]:
            path = cfg.data_dir / d
            if not path.exists():
                continue
            if not path.is_dir():
                raise RuntimeError(f"backup {d} path is not a directory")
            if any(p.is_file() for p in path.rglob("*")):
                kept.append(d)
        if kept:

            def pack():
                with tarfile.open(files, "w:gz") as tar:
                    for d in kept:
                        tar.add(cfg.data_dir / d, arcname=d)

            try:
                pack()
            except Exception:
                pack()
    except Exception as error:
        files_error = js_slice(str(error), 0, 300)
    out = []
    today = beijing_date(now)
    prefix = "monthly" if today.endswith("-01") else "weekly" if beijing_weekday(today) == "星期日" else "daily"
    for path in [dump, files] if kept and not files_error else [dump]:
        size = path.stat().st_size
        sha = _file_sha256(path)
        key = f"{prefix}/{path.name}"  # one upload per file; the chosen prefix carries its retention class
        if s:
            _upload(s, key, path, sha, size)
        out.append({"key": key if s else f"local-only:{path.name}", "bytes": size, "sha256": sha})
    # Local copies: keep the newest few of each kind.
    for kind in (f"{name}-2", f"{name}-files-"):
        listing = sorted((f.name for f in directory.iterdir() if f.name.startswith(kind)), reverse=True)
        for f in listing[KEEP_LOCAL:]:
            (directory / f).unlink(missing_ok=True)
    stamp_iso = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
    summary = {"at": stamp_iso, "uploaded": bool(s) and not files_error, "objects": out, **({"filesError": files_error} if files_error else {})}
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO settings (key, value, updated_by) VALUES ('backup.last', %s, 'backup') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
            (Jsonb(summary),),
        )
    if not s:
        raise RuntimeError("backup made locally but not uploaded: object store credentials are not configured")
    if files_error:
        raise RuntimeError(f"database backed up, but the file archive failed: {files_error}")
    return summary
