"""Admin sign-in and the /api/admin guard (port of apps/api/src/routes/admin-auth.ts). Public routes never read the session."""

from __future__ import annotations

import inspect
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from functools import wraps
from typing import cast
from urllib.parse import parse_qsl
from uuid import UUID

from fastapi import FastAPI, Request, Response
from starlette.concurrency import run_in_threadpool

from newsroom.api.respond import js_json, problem
from newsroom.engine import pack
from newsroom.engine.admin.auth import (
    SESSION_COOKIE,
    SESSION_DAYS,
    SIGN_IN_HOME,
    STATE_COOKIE,
    LoginRejected,
    Principal,
    complete_login,
    cookie,
    end_session,
    feishu_login_configured,
    login_redirect,
    parse_cookies,
    password_login,
    safe_return,
    session_principal,
)
from newsroom.engine.audit import Conflict
from newsroom.engine.config import config
from newsroom.engine.errors import BadRequest, Invalid  # noqa: F401
from newsroom.engine.publication.v1 import iso

log = logging.getLogger("newsroom.api")


def secure(request: Request) -> bool:
    """A cookie is Secure when the request itself arrived over HTTPS (behind a TLS-terminating proxy, X-Forwarded-Proto says so)."""
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return (forwarded or request.url.scheme) == "https"


def client_address(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "")


def to_json(value):
    """What JSON.stringify would write for rows read from the database: instants as ISO strings, whole numbers without a trailing .0."""
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, (float, Decimal)):
        f = float(value)
        return int(f) if f.is_integer() else f
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: to_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json(v) for v in value]
    return value


def json_response(value, status: int = 200) -> Response:
    return Response(js_json(to_json(value)), status_code=status, media_type="application/json", headers={"Cache-Control": "no-store"})


def admin_handler(fn: Callable[..., object]) -> Callable[..., Response]:
    """Guard for /api/admin/*: a live session (or the development stand-in); writes need the CSRF token. The handler gets (request, admin, *args)
    and returns a Response, or a value that is sent as JSON; the request's JSON body is in `request.state.body`. A plain error is a 500 naming it,
    as in the TypeScript."""

    @wraps(fn)
    async def wrapper(request: Request, *args, **kwargs) -> Response:
        # The server framework reads the body before anything else looks at the request.
        raw = await request.body()
        request.state.body = {}
        ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype == "application/json" and not raw:
            return problem(request, 400, "invalid_request", "The request could not be processed.")
        if raw:
            if ctype != "application/json":
                return problem(request, 415, "invalid_request", "The request could not be processed.")
            try:
                parsed = json.loads(raw)
                request.state.body = {} if parsed is None else parsed
            except ValueError:
                return problem(request, 400, "invalid_request", "The request could not be processed.")
        admin = await run_in_threadpool(session_principal, request.app.state.pool, request.headers.get("cookie"))
        if not admin:
            return problem(request, 401, "unauthorized", "Sign in to the admin first.")
        if request.method not in ("GET", "HEAD") and request.headers.get("x-csrf-token") != admin.csrf:
            return problem(request, 403, "forbidden", "Missing or stale CSRF token.")
        try:
            result = await run_in_threadpool(fn, request, admin, *args, **kwargs)
        except (Invalid, BadRequest) as e:
            return problem(request, 400, "invalid_request", str(e)[:300])
        except Conflict as e:
            return problem(request, 409, "conflict", str(e))
        except Exception as e:
            log.exception("admin api error: %s", request.url.path)
            return problem(request, 500, "internal_error", str(e)[:300])
        response = result if isinstance(result, Response) else json_response(result)
        response.headers["Cache-Control"] = "no-store"
        return response

    # The route sees the handler's parameters without `admin`, which the guard supplies.
    params = [p for name, p in inspect.signature(fn).parameters.items() if name != "admin"]
    wrapper.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    return cast(Callable[..., Response], wrapper)


# Password attempts: at most 10 per client address and 50 in all per 15 minutes. The overall cap holds even when a client forges its address.
_attempts: dict[str, list[float]] = {}
_attempts_lock = threading.Lock()


def _over(key: str, limit: int, now: float) -> bool:
    recent = [t for t in _attempts.get(key, []) if now - t < 15 * 60]
    recent.append(now)
    _attempts[key] = recent
    return len(recent) > limit


def too_many_attempts(ip: str) -> bool:
    now = time.time()
    with _attempts_lock:
        if len(_attempts) > 5000:
            _attempts.clear()
        per_client = _over(f"ip:{ip}", 10, now)
        return _over("all", 50, now) or per_client


def _form_encode(params: dict[str, str]) -> str:
    """URLSearchParams.toString(): everything but letters, digits and * - . _ is percent-encoded, a space is a plus."""
    from urllib.parse import quote  # noqa: PLC0415

    def one(s: str) -> str:
        return quote(s, safe="*-._").replace("%20", "+")

    return "&".join(f"{one(k)}={one(v)}" for k, v in params.items())


def login_page(return_to: str, error: str | None = None) -> str:
    return "/admin/login?" + _form_encode({"return": safe_return(return_to), **({"error": error} if error else {})})


def redirect(location: str, status: int, headers: dict | list | None = None) -> Response:
    response = Response(status_code=status, headers={"Location": location, "Cache-Control": "no-store"})
    for value in headers if isinstance(headers, list) else [headers] if headers else []:
        for k, v in value.items():
            response.headers.append(k, v)
    return response


def register_admin_auth(app: FastAPI) -> None:
    def feishu_redirect(request: Request, return_to: str) -> Response:
        url, state_cookie = login_redirect(return_to)
        return redirect(url, 302, {"Set-Cookie": cookie(STATE_COOKIE, state_cookie, 600, secure(request))})

    @app.get("/api/auth/login", include_in_schema=False)
    def login(request: Request) -> Response:
        return_to = request.query_params.get("return", SIGN_IN_HOME)
        # A live session does not need signing in again: go straight to where the visitor was headed.
        if session_principal(request.app.state.pool, request.headers.get("cookie")):
            return redirect(safe_return(return_to), 302)
        if feishu_login_configured() and not config().admin_password:
            return feishu_redirect(request, return_to)
        return redirect(login_page(return_to), 302)

    @app.get("/api/auth/options", include_in_schema=False)
    def options() -> Response:
        return json_response({"password": bool(config().admin_password), "feishu": feishu_login_configured()})

    # The sign-in and sign-out forms post as plain HTML forms.
    async def form_of(request: Request) -> dict[str, str]:
        raw = await request.body()
        return dict(parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True))

    @app.post("/api/auth/password", include_in_schema=False)
    async def password(request: Request) -> Response:
        b = await form_of(request)
        return_to = str(b.get("return", SIGN_IN_HOME))
        if too_many_attempts(client_address(request)):
            return redirect(login_page(return_to, "too-many"), 303)
        try:
            done = await run_in_threadpool(
                password_login, request.app.state.pool, str(b.get("password", "")), return_to, request.headers.get("user-agent")
            )
        except LoginRejected as e:
            return redirect(login_page(return_to, "unset" if re.search("ADMIN_PASSWORD", str(e)) else "wrong"), 303)
        except Exception:
            log.exception("admin login failed")
            return redirect(login_page(return_to, "wrong"), 303)
        return redirect(done["returnTo"], 303, {"Set-Cookie": cookie(SESSION_COOKIE, done["token"], SESSION_DAYS * 86400, secure(request))})

    # Signing out is a plain form too.
    @app.post("/api/auth/logout", include_in_schema=False)
    async def logout(request: Request) -> Response:
        await run_in_threadpool(end_session, request.app.state.pool, request.headers.get("cookie"))
        return redirect("/", 303, {"Set-Cookie": cookie(SESSION_COOKIE, "", 0, secure(request))})

    # Feishu sign-in (FEISHU_LOGIN_APP_ID / FEISHU_LOGIN_APP_SECRET and an allowlist), from the sign-in page.
    @app.get("/api/auth/feishu", include_in_schema=False)
    def feishu(request: Request) -> Response:
        return_to = request.query_params.get("return", SIGN_IN_HOME)
        if not feishu_login_configured():
            return redirect(login_page(return_to), 302)
        return feishu_redirect(request, return_to)

    # The callback registered in the Feishu open platform: the state cookie comes back here.
    @app.get("/api/auth/callback", include_in_schema=False)
    def callback(request: Request) -> Response:
        q = request.query_params
        try:
            done = complete_login(
                request.app.state.pool,
                q.get("code", ""),
                q.get("state", ""),
                parse_cookies(request.headers.get("cookie")).get(STATE_COOKIE),
                request.headers.get("user-agent"),
            )
        except Exception as e:
            message = str(e) if isinstance(e, LoginRejected) else "登入失敗，請稍後再試"
            if not isinstance(e, LoginRejected):
                log.exception("admin login failed")
            name = pack.site()["SITE"]["name"]
            page = f'<!doctype html><meta charset="utf-8"><title>登入失敗 · {name}</title><p style="font:16px system-ui;padding:40px">{message}。<a href="/api/auth/login">重新登入</a></p>'
            return Response(page, status_code=403, media_type="text/html", headers={"Cache-Control": "no-store"})
        return redirect(
            done["returnTo"], 302,
            [{"Set-Cookie": cookie(SESSION_COOKIE, done["token"], SESSION_DAYS * 86400, secure(request))}, {"Set-Cookie": cookie(STATE_COOKIE, "", 0, secure(request))}],
        )  # fmt: skip

    @app.get("/api/admin/me", include_in_schema=False)
    @admin_handler
    def me(request: Request, admin: Principal) -> dict:
        return {"name": admin.name, "csrf": admin.csrf, "dev": admin.dev}
