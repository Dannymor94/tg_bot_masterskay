"""Signed Telegram Mini App transport backed by real synthetic SQLite."""

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from app.db.engine import create_db
from app.miniapp.auth import InvalidInitData, telegram_user_id
from app.miniapp.server import make_app
from app.services.tasks import TaskService
from app.settings import load_studio
from tests.integration.test_tasks import NOW, seed

BOT_TOKEN = "synthetic-bot-secret"


def signed(actor: int, when: datetime = NOW, *, token: str = BOT_TOKEN) -> str:
    items = {
        "auth_date": str(int(when.timestamp())),
        "user": json.dumps({"id": actor, "first_name": "Synthetic"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={value}" for key, value in sorted(items.items()))
    key = hmac.digest(b"WebAppData", token.encode(), "sha256")
    items["hash"] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(items)


def authorization(actor: int) -> dict[str, str]:
    return {"Authorization": f"tma {signed(actor)}"}


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "garbage",
        signed(2, NOW - timedelta(hours=2)),
        signed(2, NOW + timedelta(seconds=1)),
        signed(2, token="wrong"),
        signed(2).replace("Synthetic", "Someone"),
        f"{signed(2)}&user=%7B%7D",
        signed(2).replace("auth_date", "auth_bad"),
        signed(2).replace("hash=", "hash=bad"),
    ],
)
def test_telegram_auth_fails_closed(raw):
    with pytest.raises(InvalidInitData):
        telegram_user_id(raw, BOT_TOKEN, NOW)


def test_telegram_auth_validates_signature_and_freshness():
    assert telegram_user_id(signed(2), BOT_TOKEN, NOW) == 2
    # Telegram can include the separate Ed25519 third-party signature.
    assert telegram_user_id(f"{signed(2)}&signature=synthetic", BOT_TOKEN, NOW) == 2
    assert telegram_user_id(signed(2), BOT_TOKEN, NOW + timedelta(minutes=59)) == 2
    with pytest.raises(InvalidInitData):
        telegram_user_id(signed(2), BOT_TOKEN, NOW + timedelta(hours=1, seconds=1))


def test_miniapp_uses_verified_identity_and_real_tasks(task_db):
    async def scenario():
        engine = create_db(task_db)
        service = TaskService(engine, 1, load_studio())
        await seed(service)
        app = make_app(engine, service.cfg, 1, BOT_TOKEN, clock=lambda: NOW)
        try:

            async def call(
                path: str,
                method: str = "GET",
                *,
                headers: dict[str, str] | None = None,
                payload: dict[str, str] | None = None,
                task_id: int | None = None,
                action: str | None = None,
            ) -> tuple[int, dict]:
                routes = [
                    route
                    for route in app.router.routes()
                    if route.method == method
                    and route.resource is not None
                    and route.resource.canonical == path
                ]
                assert len(routes) == 1
                request = cast(
                    web.Request,
                    SimpleNamespace(
                        headers=headers or {},
                        match_info={"task_id": str(task_id), "action": action},
                        json=AsyncMock(return_value=payload),
                    ),
                )
                try:
                    response = await routes[0].handler(request)
                except web.HTTPException as exc:
                    return exc.status, {}
                assert isinstance(response, web.Response)
                assert isinstance(response.body, (bytes, bytearray))
                return response.status, json.loads(response.body)

            status, body = await call("/miniapp/api/me", headers=authorization(2))
            assert status == 200
            assert body == {
                "user_id": 2,
                "role": "admin",
                "is_owner": False,
            }
            _, body = await call("/miniapp/api/me", headers=authorization(1))
            assert body["is_owner"] is True

            for headers in ({}, authorization(3), authorization(9876543)):
                status, _ = await call("/miniapp/api/tasks", headers=headers)
                assert status in (401, 403)

            request_headers = {
                **authorization(2),
                "Idempotency-Key": "ee693110-2606-466d-a737-a657d227980b",
            }
            status, body = await call(
                "/miniapp/api/tasks",
                "POST",
                headers=request_headers,
                payload={"title": "Synthetic"},
            )
            assert status == 201
            task_id = body["id"]
            _, replay = await call(
                "/miniapp/api/tasks",
                "POST",
                headers=request_headers,
                payload={"title": "Synthetic"},
            )
            assert replay["id"] == task_id
            _, body = await call("/miniapp/api/tasks", headers=authorization(2))
            assert [item["id"] for item in body["items"]] == [task_id]
            assert body["items"][0]["body"] == "Synthetic"

            endpoint = "/miniapp/api/tasks/{task_id}/{action}"
            status, _ = await call(
                endpoint, "POST", task_id=task_id, action="take", headers=authorization(3)
            )
            assert status == 403
            status, body = await call(
                endpoint, "POST", task_id=task_id, action="take", headers=authorization(2)
            )
            assert status == 200 and body == {"ok": True}
            _, body = await call(
                endpoint, "POST", task_id=task_id, action="take", headers=authorization(2)
            )
            assert body == {"ok": False}
            _, body = await call(
                endpoint, "POST", task_id=task_id, action="done", headers=authorization(2)
            )
            assert body == {"ok": True}
            _, body = await call("/miniapp/api/tasks", headers=authorization(2))
            assert body == {"items": []}

            for headers, payload, expected in [
                (authorization(2), {"title": ""}, 400),
                (authorization(2), {"title": "safe", "role": "admin"}, 400),
                (authorization(3), {"title": "forged"}, 403),
                ({}, {"title": "forged"}, 401),
            ]:
                status, _ = await call(
                    "/miniapp/api/tasks",
                    "POST",
                    headers={
                        **headers,
                        "Idempotency-Key": "c4e543b9-47ae-497a-b0e7-d6eb33d39f48",
                    },
                    payload=payload,
                )
                assert status == expected
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_real_router_resolves_api_paths_without_opening_socket(task_db):
    async def scenario():
        engine = create_db(task_db)
        try:
            app = make_app(engine, load_studio(), 1, BOT_TOKEN)
            for method, path, expected in [
                ("GET", "/miniapp/api/me", {}),
                ("GET", "/miniapp/api/tasks", {}),
                ("POST", "/miniapp/api/tasks", {}),
                ("POST", "/miniapp/api/tasks/17/take", {"task_id": "17", "action": "take"}),
                ("POST", "/miniapp/api/tasks/17/done", {"task_id": "17", "action": "done"}),
            ]:
                request = make_mocked_request(method, path, app=app)
                route = await app.router.resolve(request)
                assert route.http_exception is None
                for field, value in expected.items():
                    assert route[field] == value
        finally:
            await engine.dispose()

    asyncio.run(scenario())
