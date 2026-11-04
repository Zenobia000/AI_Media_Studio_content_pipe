"""Runtime configuration: port of packages/backend/src/config.ts. Secrets are loaded per group (models, collectors, integrations, auth) from
optional dotenv files, and only by the processes that need them. (core/config.py is the smaller reader the collect/fetch part started with;
this one is the engine's, with the same rules.)"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from newsroom.engine import pack

REPO_ROOT = Path(__file__).resolve().parents[3] / "AIHOT"  # where the TypeScript engine's paths were relative to: AIHOT/
CREDENTIAL_GROUPS = ("models", "collectors", "integrations", "auth")
_PLACEHOLDER = re.compile(r"^(changeme|placeholder|dummy|test|xxx+|your[-_ ].*|<.*>)$", re.I)


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else None


def _str(name: str, fallback: str) -> str:
    return _env(name) or fallback


def _int(name: str, fallback: int) -> int:
    value = _env(name)
    if value is None:
        return fallback
    try:
        return int(value)
    except ValueError as e:
        raise ValueError(f"Environment variable {name} must be an integer") from e


def _bool(name: str, fallback: bool) -> bool:
    """On only when set to true, like every switch read straight from the environment."""
    value = _env(name)
    return fallback if value is None else value == "true"


def is_production() -> bool:
    return os.environ.get("NODE_ENV") == "production"


@dataclass(frozen=True)
class Config:
    database_url: str
    api_port: int
    site_url: str
    egress_proxy_url: str | None
    allow_private_network_fetch: bool
    private_feed_hosts: tuple[str, ...]
    feishu_content_push_enabled: bool
    index_now_submit_enabled: bool
    index_now_key: str | None
    credentials_dir: Path | None
    data_dir: Path
    environment_name: str
    model_calls_enabled: bool
    dev_admin: dict | None
    admin_password: str | None
    admin_union_ids: tuple[str, ...]
    admin_emails: tuple[str, ...]


def _csv(name: str, lower: bool = False) -> tuple[str, ...]:
    return tuple(v.lower() if lower else v for v in (x.strip() for x in os.environ.get(name, "").split(",")) if v)


def load_config() -> Config:
    deployment = pack.site()["DEPLOYMENT"]
    credentials_dir = _env("AIHOT_CREDENTIALS_DIR") or deployment["credentialsDir"]
    key = os.environ.get("INDEXNOW_KEY", "")
    return Config(
        database_url=_str("DATABASE_URL", "postgres://127.0.0.1:5432/aihot"),
        api_port=_int("API_PORT", 3001),
        site_url=_str("SITE_URL", pack.site()["SITE"]["defaultUrl"]).rstrip("/"),
        egress_proxy_url=_env("EGRESS_PROXY_URL"),
        allow_private_network_fetch=_bool("ALLOW_PRIVATE_NETWORK_FETCH", False),
        private_feed_hosts=_csv("PRIVATE_FEED_HOSTS", lower=True),
        feishu_content_push_enabled=_bool("FEISHU_CONTENT_PUSH_ENABLED", False),
        index_now_submit_enabled=_bool("INDEXNOW_SUBMIT_ENABLED", False),
        index_now_key=key if re.fullmatch(r"[0-9a-f]{32}", key) else None,
        credentials_dir=(REPO_ROOT / credentials_dir).resolve() if credentials_dir else None,
        data_dir=Path(_str("AIHOT_DATA_DIR", str(REPO_ROOT / ".data"))),
        environment_name=_str("AIHOT_ENVIRONMENT", "production" if is_production() else "development"),
        model_calls_enabled=_bool("MODEL_CALLS_ENABLED", False),
        dev_admin={"displayName": os.environ.get("DEV_AUTH_DISPLAY_NAME") or "Dev Admin"} if os.environ.get("DEV_AUTH_ROLE") == "admin" else None,
        admin_password=_env("ADMIN_PASSWORD"),
        admin_union_ids=_csv("ADMIN_FEISHU_UNION_IDS"),
        admin_emails=_csv("ADMIN_EMAILS", lower=True),
    )


def config() -> Config:
    """Read at call time (the TypeScript module read it once at import); tests change the environment between calls."""
    return load_config()


def _parse_env_file(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


@cache
def _group(group: str, directory: str | None) -> dict[str, str]:
    if not directory:
        return {}
    file = Path(directory) / (pack.site()["DEPLOYMENT"]["credentialFiles"].get(group) or f"{group}.env")
    parsed = _parse_env_file(file.read_text(encoding="utf-8")) if file.exists() else {}
    return {k: os.environ.get(k, v) for k, v in parsed.items()}  # values in the environment always win


def credentials(group: str) -> dict[str, str]:
    cfg = config()
    return dict(_group(group, str(cfg.credentials_dir) if cfg.credentials_dir else None))


def credential(group: str, name: str) -> str | None:
    value = os.environ.get(name) or credentials(group).get(name)
    return value if value and value.strip() else None


def assert_production_secrets(names: list[tuple[str, str]]) -> None:
    """Production refuses to start with missing or placeholder critical secrets, or dev-login bypasses."""
    if not is_production():
        return
    problems: list[str] = []
    for group, name in names:
        value = credential(group, name)
        if not value or _PLACEHOLDER.match(value) or len(value) < 8:
            problems.append(name)
    problems += [f"{k} (dev login bypass)" for k in os.environ if k.startswith("DEV_AUTH_")]
    if config().allow_private_network_fetch:
        problems.append("ALLOW_PRIVATE_NETWORK_FETCH")
    if problems:
        raise RuntimeError(f"Refusing to start in production: {', '.join(problems)}")
