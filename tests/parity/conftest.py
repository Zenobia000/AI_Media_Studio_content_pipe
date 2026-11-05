"""The parity harness for the backend port: the TypeScript API and the Python API answer the same requests on the same database, and their
answers are compared. Needs PARITY_DATABASE_URL (a copy of a real database, migrated by AIHOT's scripts/migrate.ts) and Node; without
them these tests are skipped.

    docker exec <db> psql -U postgres -c "create database parity"
    docker exec <source-db> pg_dump -U mediahot -d mediahot --no-owner --no-acl | docker exec -i <db> psql -U postgres -d parity -q
    PARITY_DATABASE_URL=postgres://postgres:ci@127.0.0.1:55432/parity python -m pytest tests/parity
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

AIHOT = Path(__file__).resolve().parents[2] / "AIHOT"
PORT = int(os.environ.get("PARITY_NODE_PORT", "3921"))
ENV = {
    "SITE_URL": "http://127.0.0.1:3000", "API_BASE_URL": f"http://127.0.0.1:{PORT}", "SESSION_SECRET": "ci-session-secret-0123456789abcdef",
    "IMG_PROXY_SIGN_SECRET": "ci-img-secret-0123456789abcdef", "COLLECT_ENABLED": "false", "INGEST_TOKEN": "ci-ingest-token-0123456789abcdef", "MODEL_CALLS_ENABLED": "false", "API_PORT": str(PORT),
    "LOG_LEVEL": "error", "ALLOW_PRIVATE_NETWORK_FETCH": "true", "ADMIN_PASSWORD": "ci-admin-password-123",
}  # fmt: skip


@pytest.fixture(scope="module")
def settings_env():
    """The settings the parity tests run both servers with, for the tests of one module only (other tests read the environment too)."""
    patch = pytest.MonkeyPatch()
    for k, v in ENV.items():
        patch.setenv(k, v)
    yield
    patch.undo()


def clone_template(admin, database_url: str, tags: list[str]) -> dict[str, str]:
    """Copies of the seeded database. A template must have no other sessions: `<name>_tpl`, a never-used copy made once, is used when it exists;
    otherwise the sessions on the database itself are ended first."""
    base = database_url.rsplit("/", 1)[1]
    root = database_url.rsplit("/", 1)[0]
    template = f"{base}_tpl" if admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (f"{base}_tpl",)).fetchone() else base
    if template == base:
        admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()", (base,))
    for tag in tags:
        admin.execute(f"DROP DATABASE IF EXISTS {tag} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {tag} TEMPLATE {template}")
    return {tag: f"{root}/{tag}" for tag in tags}


def _url() -> str | None:
    return os.environ.get("PARITY_DATABASE_URL")


@pytest.fixture(scope="session")
def database_url() -> str:
    if not _url() or not shutil.which("node"):
        pytest.skip("set PARITY_DATABASE_URL to a migrated copy of a real database, and have Node installed")
    return _url()  # type: ignore[return-value]


@pytest.fixture(scope="session")
def node_api(database_url):
    env = {**os.environ, **ENV, "DATABASE_URL": database_url, "AIHOT_DATA_DIR": tempfile.mkdtemp(prefix="parity-node-")}
    proc = subprocess.Popen(["node", "apps/api/src/main.ts"], cwd=AIHOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{PORT}"
    try:
        for _ in range(60):
            try:
                if httpx.get(f"{base}/api/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            pytest.fail("the TypeScript API did not start")
        yield base
    finally:
        proc.terminate()
        proc.wait(10)


@pytest.fixture(scope="session")
def py_api(database_url):
    patch = pytest.MonkeyPatch()
    for k, v in ENV.items():
        patch.setenv(k, v)
    patch.setenv("DATABASE_URL", database_url)
    patch.setenv("AIHOT_DATA_DIR", tempfile.mkdtemp(prefix="parity-py-"))
    from newsroom.api.app import build_app  # noqa: PLC0415

    with TestClient(build_app(), base_url=f"http://127.0.0.1:{PORT + 1}", follow_redirects=False) as client:
        yield client
    patch.undo()


# What differs per request, and is not part of the answer.
_REQUEST_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
HEADERS = (
    "content-type",
    "cache-control",
    "location",
    "etag",
    "retry-after",
    "vary",
    "link",
    "access-control-allow-origin",
    "access-control-expose-headers",
)


def normalise(res: httpx.Response, drop: tuple[str, ...] = ()) -> dict:
    """Status, the headers that matter, and the body with request ids (and the fields named in `drop`) taken out."""
    ctype = res.headers.get("content-type", "")
    body: object
    if "json" in ctype:
        try:
            body = res.json()
        except ValueError:
            body = res.text
        body = _strip(body, drop)
    else:
        body = _REQUEST_ID.sub("<id>", res.text)
    headers = {h: _REQUEST_ID.sub("<id>", res.headers[h]) for h in HEADERS if h in res.headers}
    # Fastify writes "; charset=utf-8" on JSON; Starlette does not: the media type is what counts.
    if "content-type" in headers:
        headers["content-type"] = headers["content-type"].split(";")[0].strip().lower()
    return {"status": res.status_code, "headers": headers, "body": body}


def _strip(value, drop):
    if isinstance(value, dict):
        return {k: ("<id>" if k == "requestId" else _strip(v, drop)) for k, v in value.items() if k not in drop}
    if isinstance(value, list):
        return [_strip(v, drop) for v in value]
    return value


@pytest.fixture()
def compare(node_api, py_api):
    def run(
        path: str,
        method: str = "GET",
        drop: tuple[str, ...] = (),
        headers: dict[str, str] | None = None,
        content: str | bytes | None = None,
        aged: bool = False,
    ):
        """`aged`: the answer says how long it stays fresh (s-maxage is what is left of the cache's five minutes), and the two servers are asked one after the other,
        so the number can differ by a second when the clock turns between them: it is not compared."""
        node = httpx.request(method, node_api + path, headers=headers, content=content, follow_redirects=False, timeout=30)
        py = py_api.request(method, path, headers=headers, content=content)
        a, b = normalise(node, drop), normalise(py, drop)
        if aged:
            for answer in (a, b):
                if "cache-control" in answer["headers"]:
                    answer["headers"]["cache-control"] = re.sub(r"s-maxage=\d+", "s-maxage=<left>", answer["headers"]["cache-control"])
        assert b == a, f"{method} {path}\nTypeScript: {a}\nPython:     {b}"
        return a

    return run
