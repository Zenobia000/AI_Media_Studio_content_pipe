"""Model per capability: the code default (the site's choice in the pack's models, else `default`, the deployment's own model), an environment override,
and an admin switch kept in settings (every switch is audited). Read at call time and cached for a minute. Port of editorial/models.ts and the registry of
providers/llm.ts."""

from __future__ import annotations

import os
import time

from newsroom.engine import pack

CACHE_SECONDS = 60


def _wording() -> list:
    return pack.load("wording").get("READER_WORDING", [])


def _cap(label: str, env: str, key: str, purposes: list[str], vision: bool = False, idle=None) -> dict:
    return {"label": label, "env": env, "default": pack.load("models").get("DEFAULTS", {}).get(key) or "default", "purposes": purposes, "vision": vision, "idle": idle}


def capabilities() -> dict[str, dict]:
    """Every step of the engine and its settings."""
    return {
        "prefilter": _cap("精選預篩（是否屬於這個行業，寬召回）", "PREFILTER_MODEL", "prefilter", ["prefilter_article"]),
        "score": _cap("精選評分（兩次獨立評分，按信源分級與類別的門檻）", "SCORE_MODEL", "score", ["score_article"]),
        "understand": _cap("內容理解（入選和接近入選的標題、摘要、推薦理由，能看圖時看首圖）", "UNDERSTAND_MODEL", "understand", ["understand_article"]),
        "summarize": _cap("標題摘要（其餘文章的中文標題與摘要）", "SUMMARIZE_MODEL", "summarize", ["summarize_article"]),
        "wording": _cap("改用詞（標題、摘要、理由用了本站不用的詞時，只改這些詞）", "WORDING_MODEL", "wording", ["mend_wording"], idle=lambda: not _wording()),
        "structure": _cap("結構抽取（分類、標籤、主體公司、事件事實，不寫讀者文字）", "STRUCTURE_MODEL", "structure", ["structure_article"]),
        "group": _cap("事件歸組（新報道與候選事實的關係：同一次發生、同一事件的進展、無關；被同一篇報道連起來的兩個事件是否同一事件）", "GROUP_MODEL", "group", ["group_article", "group_signal", "group_story"]),
        "groupReview": _cap("歸組複核（相似度不高的合併、兩個事件的合併，寫入前再讀一遍；最好換一家模型）", "GROUP_REVIEW_MODEL", "groupReview", ["group_review", "group_story_review"]),
        "digest": _cap("事件綜述", "DIGEST_MODEL", "digest", ["story_digest"]),
        "report": _cap("週報月報的總述與主題（日報由規則算出，不用模型）", "REPORT_MODEL", "report", ["report_weekly", "report_monthly", "report_lead", "report_daily"]),
        "translate": _cap("精選全文翻譯（含引用帖）", "TRANSLATE_MODEL", "translate", ["translate_body", "translate_quoted"]),
    }  # fmt: skip


def models() -> dict[str, dict]:
    """The registered models: `default` (named by the environment) and the pack's presets."""
    default = {
        "key": "default", "service": "llm", "baseUrlEnv": "LLM_BASE_URL", "apiKeyEnv": "LLM_API_KEY", "model": os.environ.get("LLM_MODEL", ""),
        "jsonMode": os.environ.get("LLM_JSON_MODE") != "false", "vision": os.environ.get("LLM_VISION") == "true",
    }  # fmt: skip
    return {"default": default, **{key: {"key": key, **preset} for key, preset in pack.load("models").get("PRESETS", {}).items()}}


_cache: dict | None = None


def overrides(conn) -> dict[str, str]:
    global _cache
    if _cache and time.monotonic() - _cache["at"] < CACHE_SECONDS:
        return _cache["overrides"]
    registry = models()
    out: dict[str, str] = {}
    for r in conn.execute("SELECT key, value FROM settings WHERE key LIKE 'models.%'").fetchall():
        model = (r["value"] or {}).get("model")
        if model and model in registry:
            out[r["key"][len("models.") :]] = model
    _cache = {"at": time.monotonic(), "overrides": out}
    return out


def invalidate_model_cache() -> None:
    global _cache
    _cache = None


def capability_accepts_model(capability: dict, model: dict) -> bool:
    """Whether a step can use a registered model. A model that reads images writes text as well."""
    return not capability.get("vision") or model.get("vision") is True


def model_sources(conn) -> dict[str, dict]:
    """Where the current choice comes from, for the admin page."""
    o = overrides(conn)
    registry = models()
    out: dict[str, dict] = {}
    for key, c in capabilities().items():
        env = os.environ.get(c["env"])
        if o.get(key):
            out[key] = {"model": o[key], "source": "admin"}
        elif env and env in registry:
            out[key] = {"model": env, "source": "env"}
        else:
            out[key] = {"model": c["default"], "source": "default"}
    return out


def model_for(conn, capability: str) -> str:
    """The model a capability uses now: admin switch, else environment, else the code default."""
    c = capabilities().get(capability)
    if not c:
        raise KeyError(f"unknown model step: {capability}")
    chosen = overrides(conn).get(capability) or os.environ.get(c["env"]) or c["default"]
    return chosen if chosen in models() else c["default"]


def fallback_model_for(capability: str, primary: str) -> str | None:
    registry = models()
    for model in registry.values():
        if (model.get("fallbackFor") or {}).get(capability) == primary and model["service"] != registry[primary]["service"]:
            return model["key"]
    return None


def model_route(conn, capability: str) -> dict:
    """Preserve an explicit primary choice; only the site's evaluated pair may fail over."""
    model = model_for(conn, capability)
    return {"model": model, "fallbackModel": fallback_model_for(capability, model)}


def steps_on_service(conn, service: str, only_models: set[str] | None = None) -> list[str]:
    """The steps that use a model service now (primary or backup), for the alerts. Current routing describes possible impact."""
    current = model_sources(conn)
    registry = models()
    out: list[str] = []
    for key, c in capabilities().items():
        if c["idle"] and c["idle"]():
            continue
        primary = current[key]["model"]
        for candidate in (primary, fallback_model_for(key, primary)):
            model = registry.get(candidate) if candidate else None
            if model and model["service"] == service and (only_models is None or model["model"] in only_models):
                out.append(c["label"].split("（")[0])
                break
    return list(dict.fromkeys(out))
