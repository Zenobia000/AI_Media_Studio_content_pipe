"""/api/admin/*: queries are GET, creation POST, edits PATCH, business commands POST (port of apps/api/src/routes/admin.ts). Every route goes through
admin_handler (session + CSRF); manual changes are audited in the modules."""

from __future__ import annotations

import base64
import re

from fastapi import FastAPI, Request, Response

from newsroom.api.respond import problem
from newsroom.api.routes.admin_auth import BadRequest, admin_handler
from newsroom.engine.admin import feedback as fb
from newsroom.engine.admin import settings as st
from newsroom.engine.admin import sources as src
from newsroom.engine.admin.auth import Principal, actor_of
from newsroom.engine.audit import list_audit


def q(request: Request) -> dict[str, str]:
    return dict(request.query_params)


def page_of(request: Request) -> int:
    try:
        return max(1, int(float(q(request).get("page") or 0)) or 1)
    except ValueError:
        return 1


def body(request: Request) -> dict:
    return request.state.body if isinstance(request.state.body, dict) else {}


def not_found(request: Request) -> Response:
    return problem(request, 404, "not_found", "Not found.")


def or_not_found(request: Request, value):
    return not_found(request) if value is None else value


def decode_image(data_url) -> bytes:
    m = re.fullmatch(r"data:image/(png|jpeg|webp);base64,(.+)", str(data_url if data_url is not None else ""), re.S)
    if not m:
        raise BadRequest("image must be a PNG, JPEG or WebP data URL")
    return base64.b64decode(m.group(2))


def register_admin(app: FastAPI) -> None:
    # Sources
    @app.get("/api/admin/sources", include_in_schema=False)
    @admin_handler
    def sources_list(request: Request, admin: Principal):
        f = q(request)
        return src.list_sources(
            request.app.state.pool,
            {"q": f.get("q"), "kind": f.get("kind"), "health": f.get("health"), "mode": f.get("mode"), "use": f.get("use"), "enabled": f.get("enabled"), "page": page_of(request)},
        )

    @app.post("/api/admin/sources", include_in_schema=False)
    @admin_handler
    def sources_create(request: Request, admin: Principal):
        return src.create_source(request.app.state.pool, request.state.body, actor_of(admin))

    @app.post("/api/admin/sources/preview", include_in_schema=False)
    @admin_handler
    def sources_preview(request: Request, admin: Principal):
        return src.preview_source(request.app.state.pool, body(request))

    @app.get("/api/admin/sources/{source_id}", include_in_schema=False)
    @admin_handler
    def sources_detail(request: Request, admin: Principal, source_id: str):
        return or_not_found(request, src.source_detail(request.app.state.pool, source_id))

    @app.patch("/api/admin/sources/{source_id}", include_in_schema=False)
    @admin_handler
    def sources_update(request: Request, admin: Principal, source_id: str):
        return or_not_found(request, src.update_source(request.app.state.pool, source_id, body(request), actor_of(admin)))

    @app.post("/api/admin/sources/{source_id}/preview", include_in_schema=False)
    @admin_handler
    def sources_preview_stored(request: Request, admin: Principal, source_id: str):
        return or_not_found(request, src.preview_stored_source(request.app.state.pool, source_id))

    @app.post("/api/admin/sources/{source_id}/fetch", include_in_schema=False)
    @admin_handler
    def sources_fetch(request: Request, admin: Principal, source_id: str):
        return or_not_found(request, src.fetch_now(request.app.state.pool, source_id, actor_of(admin)))

    # Feedback
    @app.get("/api/admin/feedback", include_in_schema=False)
    @admin_handler
    def feedback_list(request: Request, admin: Principal):
        f = q(request)
        return fb.list_feedback(
            request.app.state.pool,
            {"status": f.get("status"), "kind": f.get("kind"), "idea": f.get("idea"), "q": f.get("q"), "page": page_of(request)},
        )

    @app.patch("/api/admin/feedback/{feedback_id}", include_in_schema=False)
    @admin_handler
    def feedback_update(request: Request, admin: Principal, feedback_id: str):
        b = body(request)
        result = fb.update_feedback(
            request.app.state.pool,
            int(feedback_id),
            b.get("status"),
            b.get("kind"),
            b.get("idea"),
            b.get("note"),
            str(b.get("version")),
            actor_of(admin),
            set(b),
        )
        return or_not_found(request, result)

    @app.post("/api/admin/feedback/{feedback_id}/erase", include_in_schema=False)
    @admin_handler
    def feedback_erase(request: Request, admin: Principal, feedback_id: str):
        return or_not_found(
            request, fb.erase_feedback(request.app.state.pool, int(feedback_id), str(body(request).get("reason") or ""), actor_of(admin))
        )

    @app.get("/api/admin/feedback/{feedback_id}/screenshot", include_in_schema=False)
    @admin_handler
    def feedback_screenshot(request: Request, admin: Principal, feedback_id: str):
        file = fb.feedback_screenshot(request.app.state.pool, int(feedback_id))
        try:
            data = file.read_bytes() if file else None
        except OSError:
            data = None
        if data is None:
            return not_found(request)
        ext = file.suffix.lstrip(".")
        return Response(data, media_type="image/jpeg" if ext in ("jpeg", "jpg") else f"image/{ext}")

    @app.post("/api/admin/feedback-bans", include_in_schema=False)
    @admin_handler
    def feedback_ban(request: Request, admin: Principal):
        b = body(request)
        if b.get("feedbackId") is not None:
            fb.ban_feedback_email(request.app.state.pool, int(b["feedbackId"]), str(b.get("reason")), actor_of(admin))
        else:
            fb.ban_source(request.app.state.pool, str(b.get("sourceHash")), str(b.get("reason")), actor_of(admin))
        return Response(status_code=204)

    @app.delete("/api/admin/feedback-bans/{source_hash}", include_in_schema=False)
    @admin_handler
    def feedback_unban(request: Request, admin: Principal, source_hash: str):
        fb.unban_source(request.app.state.pool, source_hash, actor_of(admin))
        return Response(status_code=204)

    # Settings
    @app.get("/api/admin/settings", include_in_schema=False)
    @admin_handler
    def settings(request: Request, admin: Principal):
        return st.settings_overview(request.app.state.pool)

    @app.post("/api/admin/settings/contact-qr", include_in_schema=False)
    @admin_handler
    def contact_qr(request: Request, admin: Principal):
        b = body(request)
        return st.replace_contact_qr(request.app.state.pool, b.get("slot"), decode_image(b.get("image")), actor_of(admin))

    @app.post("/api/admin/notify-targets/{key}", include_in_schema=False)
    @admin_handler
    def notify_target(request: Request, admin: Principal, key: str):
        b = body(request)
        return or_not_found(request, st.set_target_enabled(request.app.state.pool, key, bool(b.get("enabled")), b.get("reason"), actor_of(admin)))

    @app.put("/api/admin/budgets/{service}", include_in_schema=False)
    @admin_handler
    def budget(request: Request, admin: Principal, service: str):
        b = body(request)
        return st.update_budget(
            request.app.state.pool, service, b.get("perMinute"), b.get("perHour"), b.get("perDay"), b.get("reason"), actor_of(admin)
        )

    # The audit trail.
    @app.get("/api/admin/audit", include_in_schema=False)
    @admin_handler
    def audit_trail(request: Request, admin: Principal):
        f = q(request)
        with request.app.state.pool.connection() as conn:
            return list_audit(conn, page_of(request), f.get("subject"), f.get("action"))
