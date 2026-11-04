"""Admin identity: the admin password (ADMIN_PASSWORD), or optionally Feishu OAuth with an allowlist of union_ids / emails; opaque sessions stored
hashed, and an audit trail for every manual change. Development may impersonate an admin with DEV_AUTH_ROLE=admin; production refuses it. Port of
admin/auth.ts (and safeReturn of contracts/admin.ts)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlencode, urlsplit

import httpx
from psycopg.types.json import Jsonb

from newsroom.engine import pack
from newsroom.engine.audit import audit
from newsroom.engine.config import config, credential
from newsroom.engine.sanitize import _QUERY_KEEP, _normalize_path

SESSION_COOKIE = "aihot_admin"
STATE_COOKIE = "aihot_oauth_state"
SESSION_DAYS = 30
SIGN_IN_HOME = "/admin" if pack.site().get("ADMIN") else "/"
PASSWORD_ADMIN = "admin@local"  # the single password admin: one row, found by its reserved address


def callback_url() -> str:
    """Register this callback in the Feishu open platform when Feishu sign-in is used."""
    return f"{config().site_url}/api/auth/callback"


def feishu_login_configured() -> bool:
    return bool(credential("integrations", "FEISHU_LOGIN_APP_ID")) and bool(credential("integrations", "FEISHU_LOGIN_APP_SECRET"))


class LoginRejected(Exception):
    pass


class Principal:
    def __init__(self, user_id: int | None, name: str, csrf: str, dev: bool) -> None:
        self.user_id, self.name, self.csrf, self.dev = user_id, name, csrf, dev


def actor_of(p: Principal) -> str:
    return f"dev:{p.name}" if p.dev else f"admin:{p.user_id}"


def _secret() -> str:
    s = credential("auth", "SESSION_SECRET")
    if not s:
        raise RuntimeError("SESSION_SECRET is not configured")
    return s


def _sha256(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _js_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def session_binding(method: str, identity, key: str) -> str:
    return hmac.new(key.encode(), _js_json(["admin-session-v1", method, identity]).encode(), hashlib.sha256).hexdigest()


def _valid_claims(c) -> bool:
    return (
        isinstance(c, dict) and set(c) == {"appId", "unionId", "email"} and isinstance(c["appId"], str) and len(c["appId"]) > 0
        and (c["unionId"] is None or (isinstance(c["unionId"], str) and len(c["unionId"]) > 0))
        and (c["email"] is None or (isinstance(c["email"], str) and len(c["email"]) > 0 and c["email"] == c["email"].lower()))
    )  # fmt: skip


def _session_authorized(row: dict) -> bool:
    key = credential("auth", "SESSION_SECRET")
    binding = row["auth_binding"]
    if not key or not binding or not re.fullmatch(r"[0-9a-f]{64}", binding):
        return False
    cfg = config()
    if row["auth_method"] == "password":
        if not cfg.admin_password or len(cfg.admin_password) < 12 or row["auth_claims"] is not None:
            return False
        expected = session_binding("password", cfg.admin_password, key)
    elif row["auth_method"] == "feishu":
        c = row["auth_claims"]
        if not _valid_claims(c) or not feishu_login_configured() or c["appId"] != credential("integrations", "FEISHU_LOGIN_APP_ID"):
            return False
        if not (c["unionId"] and c["unionId"] in cfg.admin_union_ids) and not (c["email"] and c["email"] in cfg.admin_emails):
            return False
        # jsonb does not keep key order: rebuild the claims in their sign-in order, never from mutable profile data.
        expected = session_binding("feishu", {"appId": c["appId"], "unionId": c["unionId"], "email": c["email"]}, key)
    else:
        return False
    return hmac.compare_digest(bytes.fromhex(expected), bytes.fromhex(binding))


def _sign(value: str) -> str:
    return f"{value}." + _b64url(hmac.new(_secret().encode(), value.encode(), hashlib.sha256).digest())


def _unsign(signed: str | None) -> str | None:
    if not signed:
        return None
    i = signed.rfind(".")
    if i <= 0:
        return None
    value = signed[:i]
    return value if hmac.compare_digest(_sign(value)[i + 1 :].encode(), signed[i + 1 :].encode()) else None


def parse_cookies(header: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    from urllib.parse import unquote  # noqa: PLC0415

    for part in (header or "").split(";"):
        i = part.find("=")
        if i <= 0:
            continue
        try:
            out[part[:i].strip()] = unquote(part[i + 1 :].strip(), errors="strict")
        except UnicodeDecodeError:
            pass  # other cookies need not be URI-encoded: a malformed value must not break admin sign-in
    return out


def cookie(name: str, value: str, max_age_seconds: int, secure: bool) -> str:
    """A browser extension can call the admin API with the session cookie: after changing these attributes, check it still can."""
    return f"{name}={quote(value, safe=chr(33) + chr(39) + '()*-._~')}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age_seconds}{'; Secure' if secure else ''}"


# ── safeReturn ──────────────────────────────────────────────────────────────────────────


def _same_host(p: str) -> bool:
    """new URL(p, "http://return.invalid").host === "return.invalid": a backslash reads as a slash, and "//" starts a host."""
    cleaned = re.sub(r"[\t\n\r]", "", p).replace("\\", "/")
    return not cleaned.startswith("//")


def safe_return(target: str) -> str:
    """A path on this site to go to after signing in; anything else (other hosts, protocol-relative, schemes) is the sign-in home. So is a path
    too long for a Location header."""
    try:
        if re.match(r"^https?://", target, re.I):
            u = urlsplit(target)
            path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        else:
            path = target
        if not (path.startswith("/") and _same_host(path)):
            return SIGN_IN_HOME
        cleaned = re.sub("[\t\n\r]", "", path).replace("\\", "/").split("#")[0]
        pure, _, query = cleaned.partition("?")
        kept = _normalize_path(pure) + (f"?{quote(query, safe=_QUERY_KEEP)}" if query else "")
        return kept if _same_host(kept) and len(kept) <= 2000 else SIGN_IN_HOME
    except ValueError:
        return SIGN_IN_HOME


# ── Feishu ──────────────────────────────────────────────────────────────────────────────


def login_redirect(return_to: str) -> tuple[str, str]:
    """Where to send the browser to sign in; the signed state also carries where to return."""
    app_id = credential("integrations", "FEISHU_LOGIN_APP_ID")
    if not app_id:
        raise RuntimeError("FEISHU_LOGIN_APP_ID is not configured")
    state = f"{_b64url(secrets.token_bytes(16))}|{safe_return(return_to)}"
    query = urlencode({"client_id": app_id, "redirect_uri": callback_url(), "response_type": "code", "state": _sign(state)})
    return f"https://passport.feishu.cn/suite/passport/oauth/authorize?{query}", _sign(state)


def _feishu_json(res: httpx.Response, step: str) -> dict:
    try:
        return res.json()
    except ValueError:
        # JSON decoding errors may quote response bytes, which are private authentication data.
        raise RuntimeError(f"Feishu {step} response is not valid JSON (HTTP {res.status_code})") from None


def _feishu_user(code: str) -> tuple[dict, str]:
    app_id, secret = credential("integrations", "FEISHU_LOGIN_APP_ID"), credential("integrations", "FEISHU_LOGIN_APP_SECRET")
    if not app_id or not secret:
        raise RuntimeError("Feishu login app is not configured")
    token_res = httpx.post(
        "https://passport.feishu.cn/suite/passport/oauth/token",
        data={"grant_type": "authorization_code", "client_id": app_id, "client_secret": secret, "code": code, "redirect_uri": callback_url()},
        timeout=15,
    )
    token = _feishu_json(token_res, "token")
    if not token.get("access_token"):
        raise RuntimeError(f"Feishu token exchange failed (HTTP {token_res.status_code})")
    user_res = httpx.get(
        "https://passport.feishu.cn/suite/passport/oauth/userinfo", headers={"authorization": f"Bearer {token['access_token']}"}, timeout=15
    )
    return _feishu_json(user_res, "profile"), app_id


def _create_session(conn, user_id: int, user_agent: str | None, method: str, binding: str, claims: dict | None) -> str:
    token = _b64url(secrets.token_bytes(32))
    conn.execute(
        """INSERT INTO admin_sessions (id_hash, user_id, csrf_token, expires_at, user_agent, auth_method, auth_binding, auth_claims)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (_sha256(token), user_id, _b64url(secrets.token_bytes(18)), datetime.now(UTC) + timedelta(days=SESSION_DAYS), user_agent[:300] if user_agent else None,
         method, binding, Jsonb(claims) if claims else None),
    )  # fmt: skip
    return token


def complete_login(pool, code: str, state: str, state_cookie: str | None, user_agent: str | None) -> dict:
    """OAuth callback: verify state, identify the Feishu user, admit only allowlisted admins."""
    expected, given = _unsign(state_cookie), _unsign(state)
    if not expected or not given or expected != given:
        raise LoginRejected("登入狀態已失效，請重新登入")
    # What follows the random part is the path (safe_return, signed), which may itself hold a "|".
    return_to = given[given.index("|") + 1 :] if "|" in given else SIGN_IN_HOME
    u, app_id = _feishu_user(code)
    email_claim = u.get("enterprise_email") if u.get("enterprise_email") is not None else u.get("email")
    email = (email_claim.lower() or None) if isinstance(email_claim, str) else None
    union_id = (u["union_id"] or None) if isinstance(u.get("union_id"), str) else None
    claims = {"appId": app_id, "unionId": union_id, "email": email}
    cfg = config()
    if not ((union_id and union_id in cfg.admin_union_ids) or (email and email in cfg.admin_emails)):
        raise LoginRejected("這個飛書賬號沒有後臺許可權")
    binding = session_binding("feishu", claims, _secret())
    with pool.connection() as conn, conn.transaction():
        existing = conn.execute(
            "SELECT id FROM admin_users WHERE (%(u)s::text IS NOT NULL AND feishu_union_id = %(u)s) OR (%(e)s::text IS NOT NULL AND email = %(e)s) LIMIT 1",
            {"u": union_id, "e": email},
        ).fetchone()
        if existing:
            user = conn.execute(
                """UPDATE admin_users SET feishu_union_id = coalesce(feishu_union_id, %s), email = coalesce(email, %s),
                     display_name = coalesce(%s, display_name), last_login_at = now() WHERE id = %s RETURNING id""",
                (union_id, email, u.get("name"), existing["id"]),
            ).fetchone()
        else:
            user = conn.execute(
                "INSERT INTO admin_users (feishu_union_id, email, display_name, last_login_at) VALUES (%s, %s, %s, now()) RETURNING id",
                (union_id, email, u.get("name")),
            ).fetchone()
        token = _create_session(conn, user["id"], user_agent, "feishu", binding, claims)
        audit(conn, f"admin:{user['id']}", "auth.login", None, None, None, {"union_id": union_id})
    return {"token": token, "returnTo": return_to, "userId": user["id"]}


def password_login(pool, password: str, return_to: str, user_agent: str | None) -> dict:
    """Password sign-in: a constant-time comparison of digests, so the length leaks nothing either."""
    expected = config().admin_password
    if not expected or len(expected) < 12:
        raise LoginRejected("還沒有設定管理員密碼（環境變數 ADMIN_PASSWORD，至少 12 位）")
    given = hmac.new(b"admin-password", password.encode(), hashlib.sha256).digest()
    wanted = hmac.new(b"admin-password", expected.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(given, wanted):
        raise LoginRejected("密碼不對")
    binding = session_binding("password", expected, _secret())
    with pool.connection() as conn, conn.transaction():
        user = conn.execute(
            """INSERT INTO admin_users (email, display_name, last_login_at) VALUES (%s, '管理員', now())
               ON CONFLICT (email) DO UPDATE SET last_login_at = now() RETURNING id""",
            (PASSWORD_ADMIN,),
        ).fetchone()
        token = _create_session(conn, user["id"], user_agent, "password", binding, None)
        audit(conn, f"admin:{user['id']}", "auth.login", None, None, None, {"method": "password"})
    return {"token": token, "returnTo": safe_return(return_to), "userId": user["id"]}


def session_principal(pool, cookie_header: str | None) -> Principal | None:
    token = parse_cookies(cookie_header).get(SESSION_COOKIE)
    if token:
        digest = _sha256(token)
        with pool.connection() as conn:
            row = conn.execute(
                """SELECT s.user_id, s.csrf_token, s.auth_method, s.auth_binding, s.auth_claims, u.display_name AS name, u.email
                   FROM admin_sessions s JOIN admin_users u ON u.id = s.user_id WHERE s.id_hash = %s AND s.expires_at > now()""",
                (digest,),
            ).fetchone()
            if row:
                if _session_authorized(row):
                    return Principal(row["user_id"], row["name"] or row["email"] or f"admin:{row['user_id']}", row["csrf_token"], False)
                # A session seen to be invalid is gone for good: restoring the old configuration does not revive it.
                conn.execute("DELETE FROM admin_sessions WHERE id_hash = %s", (digest,))
    cfg = config()
    if cfg.dev_admin and cfg.environment_name != "production":
        return Principal(None, cfg.dev_admin["displayName"], "dev", True)
    return None


def end_session(pool, cookie_header: str | None) -> None:
    token = parse_cookies(cookie_header).get(SESSION_COOKIE)
    if token:
        with pool.connection() as conn:
            conn.execute("DELETE FROM admin_sessions WHERE id_hash = %s", (_sha256(token),))
