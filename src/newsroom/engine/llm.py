"""OpenAI-compatible chat calls, always through receipts. One model is enough: `default` is whatever the deployment names in LLM_BASE_URL / LLM_API_KEY /
LLM_MODEL, and every capability uses it unless the site, an environment variable or the admin's model page picks one of the site's named presets. Port of
providers/llm.ts."""

from __future__ import annotations

import json
import re
import time

import httpx

from newsroom.engine import models as M
from newsroom.engine.circuit import enter_model_circuit, record_model_health
from newsroom.engine.config import config, credential
from newsroom.engine.lib.ids import sha256
from newsroom.engine.receipts import (
    ModelOutputError,
    ModelUnavailableError,
    ProviderRejectedError,
    ReceiptRequest,
    assert_accepted,
    failure_kind,
    logical_key_for,
    paid_request,
    receipt_state,
    reject_received_response,
)
from newsroom.engine.sources.json_list import json_parse
from newsroom.engine.structured import response_format as structured_format

TIMEOUT_MS = 120_000  # a call without a backup waits this long for its answer
# A call with an evaluated backup gives up sooner: the backup takes over at once, and the primary's receipt stays unknown (it may be billed as well).
FAILOVER_TIMEOUT_MS = 30_000


class SchemaError(ValueError):
    """The model's answer is JSON but not the shape the step needs."""


def js_length(text: str) -> int:
    """String.prototype.length: UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


def escape_control_chars_in_strings(text: str) -> str:
    """Models sometimes emit raw newlines or tabs inside JSON strings (multi-line posts); escape only those."""
    out: list[str] = []
    in_string = escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            elif ch < " ":
                out.append("\\n" if ch == "\n" else "\\r" if ch == "\r" else "\\t" if ch == "\t" else f"\\u{ord(ch):04x}")
                continue
        elif ch == '"':
            in_string = True
        out.append(ch)
    return "".join(out)


_FENCE = re.compile(r"^```(?:json)?\s*([\s\S]*?)\s*```$", re.I)


def extract_json(text: str):
    t = text.strip()
    fence = _FENCE.match(t)
    if fence:
        t = fence.group(1)
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        raise ModelOutputError("No JSON object in model output")
    body = t[start : end + 1]
    try:
        return json_parse(body)
    except ValueError:
        return json_parse(escape_control_chars_in_strings(body))


def request_for(opts: dict, spec: dict):
    temperature = opts["temperature"] if opts.get("temperature") is not None else 0.2
    # Never below 512: a smaller cap cut short structured answers down to an empty response.
    max_tokens = max(opts["max_tokens"] if opts.get("max_tokens") is not None else 1500, 512) + (spec.get("reasoningTokens") or 0)
    user = opts["user"]
    user_text = user if isinstance(user, str) else json.dumps(user, ensure_ascii=False, separators=(",", ":"))
    request = ReceiptRequest(
        service=spec["service"], model=spec["model"], purpose=opts["purpose"], subject=opts.get("subject"),
        identity={
            "model": spec["model"], "promptVersion": opts["prompt_version"], "system": sha256(opts["system"]), "user": sha256(user_text), "temperature": temperature,
            "maxTokens": max_tokens, "extra": spec.get("extra"),
        },
        request_summary={
            "promptVersion": opts["prompt_version"], "systemHash": sha256(opts["system"]), "userHash": sha256(user_text), "userChars": js_length(user_text),
            "temperature": temperature, "maxTokens": max_tokens,
        },
        attempt_tag=opts.get("attempt_tag"),
    )  # fmt: skip
    return request, temperature, max_tokens, user_text


def chat_json(pool, opts: dict) -> dict:
    """With a backup (opts["fallback_model"]: models.fallback_model_for picks one at another provider), a primary whose outcome is unknown, or that the
    provider turns away for now, is continued by the backup at once. Returns {"data", "receiptId", "reused", "model", "usage"}."""
    if not opts.get("fallback_model"):
        return _chat_once(pool, opts)
    registry = M.models()
    backup_opts = {**opts, "model": opts["fallback_model"]}
    fallback_for = logical_key_for(request_for(opts, registry[opts["model"]])[0])
    saved = receipt_state(pool, request_for(backup_opts, registry[opts["fallback_model"]])[0])
    # Finish an existing backup attempt before choosing a provider again, even after recovery/restart.
    if saved and saved != "failed":
        return _chat_once(pool, backup_opts, True, fallback_for)
    try:
        return _chat_once(pool, opts, True)
    except Exception as error:
        if failure_kind(error) not in ("unknown", "outage"):
            raise
        return _chat_once(pool, backup_opts, True, fallback_for)


def _chat_once(pool, opts: dict, circuit: bool = False, fallback_for: str | None = None) -> dict:
    spec = M.models().get(opts["model"])
    if not spec:
        raise RuntimeError(f"Unknown model {opts['model']}")
    if not config().model_calls_enabled:
        raise ModelUnavailableError("Model calls are disabled (MODEL_CALLS_ENABLED=false)")
    base_url = credential("models", spec["baseUrlEnv"])
    api_key = credential("models", spec["apiKeyEnv"])
    if not base_url or not api_key or not spec["model"]:
        raise ModelUnavailableError(
            f"Model {opts['model']} is not configured ({spec['baseUrlEnv']}, {spec['apiKeyEnv']}{', LLM_MODEL' if spec['key'] == 'default' else ''})"
        )
    request, temperature, max_tokens, user_text = request_for(opts, spec)
    timeout_ms = opts.get("timeout_ms") or (FAILOVER_TIMEOUT_MS if circuit else TIMEOUT_MS)
    user = opts["user"]
    body: dict = {
        "model": spec["model"],
        "messages": [
            # A prompt given as one user message (the title/summary prompts) has no system message.
            *([{"role": "system", "content": opts["system"]}] if opts["system"] else []),
            # Multimodal parts go through as parts; plain objects are sent as JSON text.
            {"role": "user", "content": user if isinstance(user, (str, list)) else user_text},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        # json_object is the original's; LLM_STRUCTURED_OUTPUT=true sends a step's own schema instead (structured.py)
        **(
            {"response_format": structured_format(opts["purpose"]) or {"type": "json_object"}}
            if spec.get("jsonMode") and opts.get("json") is not False
            else {}
        ),
        **(spec.get("extra") or {}),
    }
    request.fallback_for = fallback_for

    def call(permit=None):
        started = time.perf_counter()
        try:
            res = httpx.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"content-type": "application/json", "authorization": f"Bearer {api_key}"},
                content=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(),
                timeout=timeout_ms / 1000,
            )
            text = res.content.decode("utf-8", "replace")
            assert_accepted(spec["service"], res.status_code, text)
        except Exception as error:
            failure = (
                ProviderRejectedError(f"connect failed: {error}", None, True)
                if isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout))
                else error
            )
            # A refusal is the provider answering; any other failure counts against its availability.
            if circuit:
                record_model_health(pool, spec["service"], permit, failure_kind(failure) != "refused")
            if failure is error:
                raise
            raise failure from error
        if circuit:
            record_model_health(pool, spec["service"], permit, False)
        try:
            data = json_parse(text)
            if not isinstance(data, dict):
                raise ValueError("Expected a response object")
        except ValueError:
            data = {"unparsable": text[:20000]}
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
        from newsroom.engine.receipts import CallOutcome

        return CallOutcome(
            response={**data, "_latencyMs": int((time.perf_counter() - started) * 1000 + 0.5)},
            request_id=data.get("id") if data.get("id") is not None else res.headers.get("x-request-id"),
            usage=usage,
            cost=None,
        )

    receipt = paid_request(
        pool, request, call, (lambda conn: enter_model_circuit(conn, spec["service"], timeout_ms)) if circuit else None,
    )  # fmt: skip
    response = receipt["response"] or {}
    choices = response.get("choices") or [{}]
    content = (choices[0].get("message") or {}).get("content") or ""
    try:
        raw = opts["parse"](content) if opts.get("parse") else extract_json(content)
        parsed = opts["schema"](raw)
    except Exception as error:
        # Unusable output: record it and let a later attempt pay for a fresh answer. A reasoning model that reasoned up to the output limit leaves an empty or
        # cut-off answer; say so, and where to give it room.
        if choices[0].get("finish_reason") == "length":
            where = "LLM_REASONING_TOKENS" if spec["key"] == "default" else f"reasoningTokens on preset {spec['key']}"
            detail = f"output token limit reached (finish_reason=length): a reasoning model may have spent it reasoning; give it room with {where}. {error}"
        else:
            detail = str(error)
        reject_received_response(pool, receipt["receiptId"], f"unusable output: {detail[:500]}")
        raise ModelOutputError(
            f"Model {opts['model']} returned unusable output for {opts.get('subject')}: {detail[:300]}", receipt["receiptId"]
        ) from error
    return {"data": parsed, "receiptId": receipt["receiptId"], "reused": receipt["reused"], "model": spec["key"], "usage": response.get("usage")}
