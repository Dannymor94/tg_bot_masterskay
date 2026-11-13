import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.db.engine import create_db
from app.db.models import kv, outbox, tasks, users
from app.runtime import migrate
from app.services.access import AccessDenied
from app.services.client_onboarding import ClientService
from app.settings import load_studio
from tests.integration.test_client_onboarding import NOW, PHONE, ready, rows


@pytest.fixture
def path(tmp_path):
    result = tmp_path / "security.db"
    migrate(result)
    return result


@pytest.mark.parametrize("step", ["start", "consent", "news", "contact"])
def test_transition_effect_failure_is_atomic(path, step, monkeypatch):
    async def scenario():
        engine = create_db(path)
        service = ClientService(engine, 1, load_studio())
        try:
            if step != "start":
                await service.start(2, 10, "", NOW)
            if step in ("news", "contact"):
                await service.choice(2, "consent", 10, NOW)
            if step == "contact":
                await service.choice(2, "no", 10, NOW)
            before = [await rows(engine, table) for table in (users, kv, outbox, tasks)]

            async def action():
                if step == "start":
                    return await service.start(2, 10, "", NOW)
                if step == "contact":
                    return await service.contact(2, 2, SecretStr(PHONE), 11, NOW)
                return await service.choice(2, "consent" if step == "consent" else "yes", 10, NOW)

            with monkeypatch.context() as patch:
                patch.setattr(service, "reply", AsyncMock(side_effect=RuntimeError("synthetic")))
                with pytest.raises(RuntimeError):
                    await action()
            assert [await rows(engine, table) for table in (users, kv, outbox, tasks)] == before
            await action()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_contact_requires_completed_consent_flow_and_valid_phone(path):
    async def scenario():
        engine = create_db(path)
        lookup = AsyncMock()
        service = ClientService(engine, 1, load_studio(), lookup)
        try:
            with pytest.raises(AccessDenied):
                await service.contact(2, 2, SecretStr(PHONE), 11, NOW)
            await service.start(2, 10, "", NOW)
            assert not await service.contact(2, 2, SecretStr(PHONE), 11, NOW)
            await service.choice(2, "consent", 10, NOW)
            assert not await service.contact(2, 2, SecretStr(PHONE), 11, NOW)
            await service.choice(2, "no", 10, NOW)
            assert not await service.contact(2, 2, SecretStr("invalid"), 11, NOW)
            lookup.candidates.assert_not_called()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_retry_denies_revoked_target_and_role_change(path):
    async def scenario():
        engine = create_db(path)
        service = ClientService(engine, 1, load_studio())
        try:
            await service.bootstrap(NOW)
            await ready(service)
            await service.contact(2, 2, SecretStr(PHONE), 11, NOW)
            async with engine.begin() as db:
                await db.execute(
                    users.update().where(users.c.telegram_user_id == 2).values(access_revoked=True)
                )
            with pytest.raises(AccessDenied):
                await service.retry(1, 1, "revoked-target", NOW)
            async with engine.begin() as db:
                await db.execute(
                    users.update()
                    .where(users.c.telegram_user_id == 1)
                    .values(role="client", is_owner=False)
                )
            with pytest.raises(AccessDenied):
                await service.retry(1, 1, "stale-admin", NOW)
            async with engine.connect() as db:
                assert (await db.execute(select(tasks.c.body))).scalar_one() == PHONE
        finally:
            await engine.dispose()

    asyncio.run(scenario())
