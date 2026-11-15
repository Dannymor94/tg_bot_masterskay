"""Localhost-only Mini App API for existing SQLite tasks; no YCLIENTS writes."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from aiohttp import web
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import tasks
from app.domain.models import User
from app.miniapp.auth import InvalidInitData, telegram_user_id
from app.services.access import AccessDenied
from app.services.tasks import TaskService
from app.settings import StudioConfig

STATIC = Path(__file__).parent / "static"


def make_app(
    engine: AsyncEngine,
    cfg: StudioConfig,
    owner_id: int,
    bot_token: str,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> web.Application:
    service = TaskService(engine, owner_id, cfg)

    async def identity(request: web.Request) -> int:
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("tma "):
            raise web.HTTPUnauthorized()
        try:
            return telegram_user_id(authorization[4:], bot_token, clock())
        except InvalidInitData:
            raise web.HTTPUnauthorized() from None

    async def registered(request: web.Request) -> tuple[int, User]:
        actor = await identity(request)
        async with engine.connect() as db:
            user = await service.user(db, actor)
            if (
                user is None
                or user.access_revoked
                or user.blocked_at is not None
                or (user.is_owner and actor != owner_id)
            ):
                raise web.HTTPForbidden()
            return actor, user

    async def me(request: web.Request) -> web.Response:
        actor, user = await registered(request)
        return web.json_response(
            {"user_id": actor, "role": user.role, "is_owner": user.is_owner},
            headers={"Cache-Control": "no-store"},
        )

    async def listing(request: web.Request) -> web.Response:
        actor, _ = await registered(request)
        async with engine.connect() as db:
            try:
                await service.authorize(db, actor, "tasks.list")
            except AccessDenied:
                raise web.HTTPForbidden() from None
            rows = (
                (
                    await db.execute(
                        select(tasks).where(tasks.c.state != "done").order_by(tasks.c.id).limit(200)
                    )
                )
                .mappings()
                .all()
            )
        items = [
            {
                "id": row["id"],
                "code": row["code"],
                "title": row["title"],
                # R4 may contain a phone: never disclose it in this API.
                "body": "" if row["code"] == "R4" else row["body"],
                "state": row["state"],
                "taken_by": row["taken_by"],
                "due_at": row["due_at"].isoformat() if row["due_at"] else None,
            }
            for row in rows
        ]
        return web.json_response({"items": items}, headers={"Cache-Control": "no-store"})

    async def create(request: web.Request) -> web.Response:
        actor, _ = await registered(request)
        header = request.headers.get("Idempotency-Key", "")
        try:
            request_id = str(UUID(header))
            payload = await request.json()
            if (
                not isinstance(payload, dict)
                or set(payload) != {"title"}
                or not isinstance(payload["title"], str)
            ):
                raise ValueError()
            task_id = await service.manual(
                actor, f"miniapp:{request_id}", payload["title"], clock()
            )
        except AccessDenied:
            raise web.HTTPForbidden() from None
        except (ValueError, TypeError):
            raise web.HTTPBadRequest() from None
        return web.json_response({"id": task_id}, status=201, headers={"Cache-Control": "no-store"})

    async def transition(request: web.Request) -> web.Response:
        actor, _ = await registered(request)
        task_id = int(request.match_info["task_id"])
        if task_id <= 0:
            raise web.HTTPBadRequest()
        try:
            action = request.match_info["action"]
            if action == "take":
                ok = await service.take(actor, task_id, clock())
            elif action == "done":
                ok = await service.finish(actor, task_id, clock())
            else:
                raise web.HTTPNotFound()
        except AccessDenied:
            raise web.HTTPForbidden() from None
        return web.json_response({"ok": ok}, headers={"Cache-Control": "no-store"})

    async def index(request: web.Request) -> web.StreamResponse:
        if not (STATIC / "index.html").is_file():
            raise web.HTTPServiceUnavailable()
        response = web.FileResponse(STATIC / "index.html")
        response.headers["Cache-Control"] = "no-store"
        return response

    app = web.Application(client_max_size=4096)
    app.router.add_get("/miniapp", index)
    app.router.add_get("/miniapp/api/me", me)
    app.router.add_get("/miniapp/api/tasks", listing)
    app.router.add_post("/miniapp/api/tasks", create)
    app.router.add_post("/miniapp/api/tasks/{task_id:\\d+}/{action:take|done}", transition)
    # Static files are public; no tokens, customer data or service credentials.
    if STATIC.is_dir():
        app.router.add_static("/miniapp/static/", STATIC, follow_symlinks=False)
    return app


async def serve_http(
    engine: AsyncEngine,
    cfg: StudioConfig,
    owner_id: int,
    bot_token: str,
    port: int,
    stopped: asyncio.Event,
) -> None:
    app = make_app(engine, cfg, owner_id, bot_token)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    try:
        site = web.TCPSite(runner, host="127.0.0.1", port=port)
        await site.start()
        await stopped.wait()
    finally:
        await runner.cleanup()
