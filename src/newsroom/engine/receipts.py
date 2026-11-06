"""Paid requests (models, SocialData, Jina) go through here. Port of providers/receipts.ts.

1. A logical request has a stable key bound to task, input revision, provider, model, prompt and config.
2. Before calling, a placeholder row and an attempt row are persisted; budgets count attempts.
3. The raw response is saved before any business write; recovery reuses a received response.
4. A request whose outcome is unknown (timeout after sending, crash mid-flight) is not re-sent by the caller. ops.recover releases it once after 30 minutes,
   so a lost answer costs at most one repeat; after that it waits for the admin."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg.types.json import Jsonb

from newsroom.engine.budget import BudgetExceededError, budget_message
from newsroom.engine.lib.ids import sha256, stable_json

PENDING_STALE = timedelta(minutes=10)
PROVIDER_STOP_SECONDS = 900  # how long a provider's access/billing refusal stops new calls to it


class ReceiptBusyError(Exception):
    pass


class ReceiptUnknownError(Exception):
    def __init__(self, receipt_id: int, message: str) -> None:
        super().__init__(message)
        self.receipt_id = receipt_id


class ProviderRejectedError(Exception):
    """Raised by a call when the provider clearly did not accept (and will not bill) the request."""

    def __init__(self, message: str, status: int | None, retryable: bool, access_refused: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.access_refused = access_refused


class ModelUnavailableError(Exception):
    """Model calls are switched off (MODEL_CALLS_ENABLED=false), or the model has no address or key: nothing was sent."""


class ModelOutputError(Exception):
    """A received answer that cannot be used; reject_received_response lets a later attempt pay for a fresh one."""

    def __init__(self, message: str, receipt_id: int | None = None) -> None:
        super().__init__(message)
        self.receipt_id = receipt_id


def failure_kind(error: BaseException) -> str:
    """What a failed paid call says about the work that made it: off, budget, busy, outage, unknown, refused, unusable or other."""
    if isinstance(error, ModelUnavailableError):
        return "off"
    if isinstance(error, BudgetExceededError):
        return "budget"
    if isinstance(error, ReceiptBusyError):
        return "busy"
    if isinstance(error, ProviderRejectedError):
        return "outage" if error.retryable else "refused"
    if isinstance(error, ReceiptUnknownError):
        return "unknown"
    if isinstance(error, ModelOutputError):
        return "unusable"
    return "other"


def failed_receipt_id(error: BaseException) -> int | None:
    """The receipt a failed call leaves to look at: its unusable answer, or the request whose outcome is unknown."""
    return error.receipt_id if isinstance(error, (ModelOutputError, ReceiptUnknownError)) else None


def _rejection_detail(body: str) -> tuple[str, str]:
    """The provider boundary accepts both OpenAI-compatible nested errors and flat provider errors: (code, message)."""
    try:
        data = json.loads(body)
    except ValueError:
        return "", body
    if not isinstance(data, dict):
        return "", body
    error = data["error"] if isinstance(data.get("error"), dict) else data
    code = error.get("code")
    kind = error.get("type")
    code_text = str(code) if isinstance(code, (str, int, float)) and not isinstance(code, bool) else kind if isinstance(kind, str) else ""
    message = error["message"] if isinstance(error.get("message"), str) else data["error"] if isinstance(data.get("error"), str) else body
    return code_text, message


_HTTP = re.compile(r"(?:^| )HTTP (\d{3})(?:: ?| )([\s\S]*)$")
_ACCOUNT_CODES = {"GoUsageLimitError", "FreeUsageLimitError", "CreditLimitExceeded", "credit_balance_exhausted", "organization_usage_limit_exceeded",
                  "organization_spend_limit_exceeded", "project_spend_limit_exceeded"}  # fmt: skip
_ZHIPU_CODES = {"1113", "1308", "1309", "1310", "1311", "1314", "1315", "1316", "1317", "1318", "1319", "1320", "1321"}
_BALANCE = re.compile(r"\binsufficient (?:account |credit )?balance\b|\b(?:credit )?balance (?:is )?(?:exhausted|insufficient)\b|賬戶.*欠費|餘額不足", re.I | re.A)


def provider_access_refused(service: str, error: str) -> bool:
    """Account or spending refusals, shared by live requests and their saved alert evidence."""
    m = _HTTP.search(error)
    if not m:
        return False
    status = int(m.group(1))
    if status in (401, 402, 403):
        return True
    if status != 429:
        return False
    code, message = _rejection_detail(m.group(2))
    if code in _ACCOUNT_CODES:
        return True
    if service == "zhipu" and code in _ZHIPU_CODES:
        return True
    if service == "dashscope":
        if code in ("CommodityNotPurchased", "PrepaidBillOverdue", "PostpaidBillOverdue", "BudgetLimitExceeded"):
            return True
        # AllocationQuota / insufficient_quota usually means TPM throttling here, not spent money.
        if code in ("Throttling.AllocationQuota", "insufficient_quota") and message.strip() == "Free allocated quota exceeded.":
            return True
    return _BALANCE.search(message) is not None


def provider_spending_refused(service: str, error: str) -> bool:
    """A refusal that every later request would meet too: the account cannot pay or the plan is spent."""
    m = _HTTP.search(error)
    if not m:
        return False
    status = int(m.group(1))
    if status == 402:
        return True
    return status == 429 and provider_access_refused(service, error)


def assert_accepted(service: str, status: int, body: str) -> None:
    """A temporary rate limit can pass; account/spending refusals need access restored first."""
    if 200 <= status < 300:
        return
    code, message = _rejection_detail(body)
    # Keep the structured code before truncating verbose text, so saved evidence classifies the same way.
    detail = json.dumps({"code": code, "message": message[:500]}, ensure_ascii=False, separators=(",", ":")) if code else body[:500]
    error = f"{service} HTTP {status}: {detail}"
    refused = provider_access_refused(service, error)
    raise ProviderRejectedError(error, status, status >= 500 or (status == 429 and not refused), refused)


@dataclass
class CallOutcome:
    response: object
    request_id: str | None = None
    usage: dict | None = None
    cost: dict | None = None  # {"amount", "currency", "basis"}


@dataclass
class ReceiptRequest:
    service: str
    purpose: str
    identity: object  # everything that determines the output: hashed into the logical key
    model: str | None = None
    subject: str | None = None
    request_summary: dict = field(default_factory=dict)  # stored for diagnosis; must not contain secrets
    attempt_tag: str | None = None
    fallback_for: str | None = None  # a backup request continues this logical request without resending its unknown primary


def logical_key_for(req: ReceiptRequest) -> str:
    identity = sha256(stable_json(req.identity))
    return ":".join([req.service, req.purpose, req.model if req.model is not None else "-", identity, req.attempt_tag if req.attempt_tag is not None else "0"])


def receipt_state(pool, req: ReceiptRequest) -> str | None:
    with pool.connection() as conn:
        row = conn.execute("SELECT status FROM receipts WHERE logical_key = %s", (logical_key_for(req),)).fetchone()
    return row["status"] if row else None


def provider_stop_key(service: str, model: str | None = None) -> str:
    """The stop is a state, not a finding: per model, because a quota can be one model's."""
    from urllib.parse import quote

    return f"provider.stop.{service}" + ("" if model is None else f":{quote(model, safe='-_.!~*()')}")


def _stop_until(conn, key: str) -> float | None:
    row = conn.execute("SELECT value FROM settings WHERE key = %s", (key,)).fetchone()
    until = (row["value"] or {}).get("until") if row else None
    try:
        ms = datetime.fromisoformat(str(until).replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return None
    return ms if ms > time.time() * 1000 else None


def _stop_for_refusal(conn, key: str) -> None:
    until = (datetime.now(UTC) + timedelta(seconds=PROVIDER_STOP_SECONDS)).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(time.time() * 1000) % 1000:03d}Z"
    conn.execute(
        """INSERT INTO settings (key, value, updated_by) VALUES (%s, jsonb_build_object('until', %s::text, 'refusedAt', now()), 'receipts')
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
        (key, until),
    )


def _clear_stop(conn, key: str, attempt_id: int) -> None:
    """An answer to a call sent after the refusal proves access is back; one already in flight may have had the last of the quota."""
    conn.execute(
        """DELETE FROM settings WHERE key = %s
           AND coalesce((value->>'refusedAt')::timestamptz, 'epoch') < (SELECT started_at FROM receipt_attempts WHERE id = %s)""",
        (key, attempt_id),
    )


def _check_budget(conn, service: str, model: str | None) -> None:
    stopped = _stop_until(conn, provider_stop_key(service, model))
    if stopped is not None:
        raise BudgetExceededError(service, "provider", -((stopped - time.time() * 1000) // -1000))
    budget = conn.execute("SELECT per_minute, per_hour, per_day FROM budgets WHERE service = %s", (service,)).fetchone()
    if not budget:
        return  # default rows come with the migrations; a service an operator removed is unlimited
    c = conn.execute(
        """SELECT count(*) FILTER (WHERE started_at > now() - interval '1 minute') AS minute,
                  count(*) FILTER (WHERE started_at > now() - interval '1 hour') AS hour, count(*) AS day
           FROM receipt_attempts WHERE service = %s AND origin = 'live' AND started_at > now() - interval '1 day'""",
        (service,),
    ).fetchone()
    if budget["per_minute"] <= 0 or budget["per_hour"] <= 0 or budget["per_day"] <= 0:
        raise BudgetExceededError(service, "stopped", 3600)
    if c["minute"] >= budget["per_minute"]:
        raise BudgetExceededError(service, "minute", 60)
    if c["hour"] >= budget["per_hour"]:
        raise BudgetExceededError(service, "hour", 600)
    if c["day"] >= budget["per_day"]:
        raise BudgetExceededError(service, "day", 3600)


def _start_attempt(conn, receipt_id: int, attempt: int, req: ReceiptRequest) -> int:
    return conn.execute(
        "INSERT INTO receipt_attempts (receipt_id, attempt, service, model, status) VALUES (%s, %s, %s, %s, 'pending') RETURNING id",
        (receipt_id, attempt, req.service, req.model),
    ).fetchone()["id"]


def _mark_unknown(conn, receipt_id: int, reason: str) -> None:
    conn.execute("UPDATE receipts SET status = 'unknown', error = %s, updated_at = now() WHERE id = %s", (reason, receipt_id))
    conn.execute("UPDATE receipt_attempts SET status = 'unknown', error = %s, finished_at = now() WHERE receipt_id = %s AND status = 'pending'", (reason, receipt_id))


def _round(x: float) -> int:
    return int(x + 0.5)


def paid_request(pool, req: ReceiptRequest, call, before_request=None, shutdown=None) -> dict:
    """Runs a paid request at most once per logical key and returns its raw response: {"receiptId", "response", "reused"}. A caller persisting business
    results completes the receipt in the same transaction."""
    logical_key = logical_key_for(req)
    with pool.connection() as conn, conn.transaction():
        # Serialise budget checks per service so concurrent workers cannot overshoot.
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("budget:" + req.service,))
        existing = conn.execute("SELECT id, status, response, created_at, updated_at FROM receipts WHERE logical_key = %s FOR UPDATE", (logical_key,)).fetchone()
        if existing and existing["status"] in ("received", "completed"):
            return {"receiptId": existing["id"], "response": existing["response"], "reused": True}
        # Finish business writes from saved answers during shutdown, but never reserve or send the next paid page/batch.
        if shutdown is not None and shutdown.is_set():
            raise InterruptedError("worker is stopping")
        if existing:
            if existing["status"] == "pending":
                if datetime.now(UTC) - existing["updated_at"] < PENDING_STALE:
                    raise ReceiptBusyError(f"Receipt {existing['id']} is in flight")
                _mark_unknown(conn, existing["id"], "placeholder went stale without a recorded result")
                unknown = existing["id"]
            elif existing["status"] == "unknown":
                unknown = existing["id"]
            else:  # failed: the provider did not take the request, or its answer was unusable; a new attempt is allowed
                unknown = None
            if unknown is not None:
                # the transaction ends with the stale marking kept: leave through the normal path below
                claimed: tuple[Any, ...] = ("unknown", unknown)
            else:
                _check_budget(conn, req.service, req.model)
                permit = before_request(conn) if before_request else None
                attempts = conn.execute(
                    "UPDATE receipts SET status = 'pending', attempts = attempts + 1, error = NULL, updated_at = now() WHERE id = %s RETURNING attempts", (existing["id"],)
                ).fetchone()["attempts"]
                claimed = ("call", existing["id"], _start_attempt(conn, existing["id"], attempts, req), permit)
        else:
            _check_budget(conn, req.service, req.model)
            permit = before_request(conn) if before_request else None
            receipt_id = conn.execute(
                """INSERT INTO receipts (logical_key, service, model, purpose, subject, status, request, attempts)
                   VALUES (%s, %s, %s, %s, %s, 'pending', %s, 1) RETURNING id""",
                (logical_key, req.service, req.model, req.purpose, req.subject, Jsonb(req.request_summary or {})),
            ).fetchone()["id"]
            if req.fallback_for:
                conn.execute("UPDATE receipts SET request = request || %s WHERE logical_key = %s", (Jsonb({"fallbackReceiptId": receipt_id}), req.fallback_for))
            claimed = ("call", receipt_id, _start_attempt(conn, receipt_id, 1, req), permit)
    if claimed[0] == "unknown":
        raise ReceiptUnknownError(claimed[1], f"Receipt {claimed[1]} has an unknown outcome; it is released once automatically, then from the admin")
    _, receipt_id, attempt_id, permit = claimed
    started = time.perf_counter()
    try:
        outcome: CallOutcome = call(permit) if before_request else call()
    except Exception as error:
        # An account/spending refusal (not a throttle or a private resource) also stops new calls to the service.
        spending_refused = isinstance(error, ProviderRejectedError) and provider_spending_refused(req.service, str(error))
        status = "failed" if isinstance(error, ProviderRejectedError) else "unknown"
        # "unknown": the request may have reached the provider (timeout, reset): do not re-send automatically.
        message = (str(error) if isinstance(error, ProviderRejectedError) else f"{type(error).__name__}: {error}")[:2000]
        with pool.connection() as conn, conn.transaction():
            conn.execute("UPDATE receipts SET status = %s, error = %s, updated_at = now() WHERE id = %s", (status, message, receipt_id))
            conn.execute(
                "UPDATE receipt_attempts SET status = %s, error = %s, latency_ms = %s, finished_at = now() WHERE id = %s",
                (status, message, _round((time.perf_counter() - started) * 1000), attempt_id),
            )
            if spending_refused:
                _stop_for_refusal(conn, provider_stop_key(req.service, req.model))
        if status == "unknown":
            raise ReceiptUnknownError(receipt_id, message) from error
        raise
    cost = outcome.cost or {}
    usage = Jsonb(outcome.usage) if outcome.usage else None
    with pool.connection() as conn, conn.transaction():
        conn.execute(
            """UPDATE receipts SET status = 'received', response = %s, request_id = %s, usage = %s, cost = %s, currency = %s, cost_basis = %s,
                 received_at = now(), updated_at = now() WHERE id = %s""",
            (Jsonb(outcome.response if outcome.response is not None else None), outcome.request_id, usage, cost.get("amount"), cost.get("currency"), cost.get("basis"), receipt_id),
        )
        conn.execute(
            """UPDATE receipt_attempts SET status = 'received', request_id = %s, usage = %s, cost = %s, currency = %s, cost_basis = %s, latency_ms = %s, finished_at = now()
               WHERE id = %s""",
            (outcome.request_id, usage, cost.get("amount"), cost.get("currency"), cost.get("basis"), _round((time.perf_counter() - started) * 1000), attempt_id),
        )
        # A real answer is the only proof that access is back; it releases a stop without waiting for it.
        _clear_stop(conn, provider_stop_key(req.service, req.model), attempt_id)
    return {"receiptId": receipt_id, "response": outcome.response, "reused": False}


def mark_stale_pending_receipts(pool) -> int:
    """Placeholders left behind by a process that stopped mid-request become "unknown", so they are released like any other unknown outcome."""
    reason = "placeholder went stale without a recorded result"
    with pool.connection() as conn, conn.transaction():
        stale = conn.execute(
            "UPDATE receipts SET status = 'unknown', error = %s, updated_at = now() WHERE status = 'pending' AND updated_at < %s RETURNING id",
            (reason, datetime.now(UTC) - PENDING_STALE),
        ).fetchall()
        if stale:
            conn.execute(
                "UPDATE receipt_attempts SET status = 'unknown', error = %s, finished_at = now() WHERE receipt_id = ANY(%s) AND status = 'pending'", (reason, [r["id"] for r in stale])
            )
        return len(stale)


def release_unknown_receipt(conn, receipt_id: int, error: str) -> dict | None:
    """Releases an unknown receipt: marked failed, so the next attempt of its request calls again. None when the receipt is not, or no longer, unknown."""
    released = conn.execute(
        "UPDATE receipts SET status = 'failed', error = %s, updated_at = now() WHERE id = %s AND status = 'unknown' RETURNING subject, purpose", (error, receipt_id)
    ).fetchone()
    if released:
        conn.execute("UPDATE receipt_attempts SET status = 'failed', error = %s WHERE receipt_id = %s AND status = 'unknown'", (error, receipt_id))
    return released


def complete_receipt(conn, receipt_id: int) -> None:
    conn.execute("UPDATE receipts SET status = 'completed', completed_at = coalesce(completed_at, now()), updated_at = now() WHERE id = %s", (receipt_id,))


def reject_received_response(pool, receipt_id: int, reason: str) -> None:
    """Marks a received response that could not be used (e.g. unparsable) so a fresh attempt can be made."""
    with pool.connection() as conn:
        conn.execute("UPDATE receipts SET status = 'failed', error = %s, updated_at = now() WHERE id = %s", (reason[:2000], receipt_id))


__all__ = ["budget_message"]
