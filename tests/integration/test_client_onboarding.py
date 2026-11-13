import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.db.engine import create_db
from app.db.models import audit_log, kv, outbox, tasks, users
from app.domain.client_matching import Candidate
from app.runtime import migrate
from app.services.access import AccessDenied
from app.services.client_onboarding import ClientService
from app.settings import load_studio

NOW = datetime(2026, 1, 1, 9, tzinfo=UTC)
PHONE = "+79000000001"


@pytest.fixture
def path(tmp_path):
    path = tmp_path / "client.db"
    migrate(path)
    return path


async def ready(service, user=2):
    await service.start(user, 10, "src_synthetic", NOW)
    assert await service.choice(user, "consent", 10, NOW)
    assert await service.choice(user, "yes", 10, NOW)


async def rows(engine, table):
    async with engine.connect() as db:
        return (await db.execute(select(table))).mappings().all()


@pytest.mark.parametrize("ids", [[], [50], [50, 51], None])
def test_lookup_outcomes_and_r4_privacy(path, ids, caplog):
    async def scenario():
        engine = create_db(path)
        lookup = AsyncMock()
        lookup.candidates.return_value = [
            Candidate(client_id=i, phone=SecretStr(PHONE)) for i in (ids or [])
        ]
        if ids is None:
            lookup.candidates.side_effect = RuntimeError(PHONE)
        service = ClientService(engine, 1, load_studio(), lookup)
        try:
            await service.bootstrap(NOW)
            await ready(service)
            assert not await service.contact(2, 3, SecretStr(PHONE), 11, NOW)
            assert not await service.contact(2, None, SecretStr(PHONE), 11, NOW)
            assert not await service.contact(2, 2, SecretStr(PHONE), 9, NOW)
            lookup.candidates.assert_not_called()
            assert await service.contact(2, 2, SecretStr(PHONE), 11, NOW)
            assert not await service.contact(2, 2, SecretStr(PHONE), 11, NOW)
            client = (await rows(engine, users))[1]
            assert client["consent_at"] == NOW and client["news_opt_in"]
            assert client["source"] == "src_synthetic"
            if ids == [50]:
                assert client["yclients_client_id"] == 50
                assert await rows(engine, tasks) == []
            else:
                assert client["yclients_client_id"] is None
                task = (await rows(engine, tasks))[0]
                assert task["body"] == PHONE and task["state"] == "open"
                assert task["due_at"] > NOW
                for actor in (2, 99):
                    with pytest.raises(AccessDenied):
                        await service.retry(actor, task["id"], "forged", NOW)
                lookup.candidates.side_effect = RuntimeError(PHONE)
                assert not await service.retry(1, task["id"], "unavailable", NOW)
                assert (await rows(engine, tasks))[0]["body"] == PHONE
                lookup.candidates.side_effect = None
                lookup.candidates.return_value = [Candidate(client_id=50, phone=SecretStr(PHONE))]
                assert await service.retry(1, task["id"], "success", NOW)
                assert not await service.retry(1, task["id"], "success", NOW)
                task = (await rows(engine, tasks))[0]
                assert task["body"] == "" and task["state"] == "done"
                assert (await rows(engine, users))[1]["yclients_client_id"] == 50
            for table in (users, kv, outbox, audit_log):
                assert PHONE not in repr(await rows(engine, table))
            assert PHONE not in caplog.text
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_stale_choices_and_revocation(path):
    async def scenario():
        engine = create_db(path)
        service = ClientService(engine, 1, load_studio())
        try:
            await service.start(2, 10, "invite_do-not-store", NOW)
            assert not await service.choice(2, "yes", 10, NOW)
            await service.start(2, 20, "src_later", NOW)
            assert not await service.choice(2, "consent", 10, NOW)
            assert await service.choice(2, "consent", 20, NOW)
            assert not await service.choice(2, "consent", 20, NOW)
            assert await service.choice(2, "no", 20, NOW)
            assert not await service.choice(2, "yes", 20, NOW)
            await service.start(2, 20, "", NOW)
            user = (await rows(engine, users))[0]
            assert user["news_opt_in"] is False and user["source"] is None
            async with engine.begin() as db:
                await db.execute(users.update().values(access_revoked=True))
            for call in (
                service.start(2, 30, "", NOW),
                service.choice(2, "yes", 20, NOW),
                service.contact(2, 2, SecretStr(PHONE), 21, NOW),
            ):
                with pytest.raises(AccessDenied):
                    await call
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_concurrent_restart_and_retry(path):
    async def scenario():
        engines = [create_db(path), create_db(path)]
        services = [ClientService(e, 1, load_studio()) for e in engines]
        try:
            await services[0].bootstrap(NOW)
            await ready(services[0])
            result = await asyncio.gather(
                *(s.contact(2, 2, SecretStr(PHONE), 11, NOW) for s in services)
            )
            assert sorted(result) == [False, True]
            assert len(await rows(engines[0], tasks)) == 1
            # A new service observes durable waiting state and never duplicates R4.
            await services[1].start(2, 30, "", NOW)
            assert not await services[1].contact(2, 2, SecretStr(PHONE), 31, NOW)
            lookup = AsyncMock()
            lookup.candidates.return_value = [Candidate(client_id=50, phone=SecretStr(PHONE))]
            for s in services:
                s.lookup = lookup
            result = await asyncio.gather(*(s.retry(1, 1, "same", NOW) for s in services))
            assert sorted(result) == [False, True]
            assert lookup.candidates.await_count == 1
            assert (await rows(engines[0], tasks))[0]["body"] == ""
            keys = [r["dedupe_key"] for r in await rows(engines[0], outbox)]
            assert len(keys) == len(set(keys))
        finally:
            for engine in engines:
                await engine.dispose()

    asyncio.run(scenario())


def test_binding_rolls_back_with_task_phone_on_effect_failure(path, monkeypatch):
    async def scenario():
        engine = create_db(path)
        service = ClientService(engine, 1, load_studio())
        try:
            await service.bootstrap(NOW)
            await ready(service)
            await service.contact(2, 2, SecretStr(PHONE), 11, NOW)
            lookup = AsyncMock()
            lookup.candidates.return_value = [Candidate(client_id=50, phone=SecretStr(PHONE))]
            service.lookup = lookup
            with monkeypatch.context() as patch:
                patch.setattr(service, "audit", AsyncMock(side_effect=RuntimeError("synthetic")))
                with pytest.raises(RuntimeError):
                    await service.retry(1, 1, "retry", NOW)
            assert (await rows(engine, users))[1]["yclients_client_id"] is None
            assert (await rows(engine, tasks))[0]["body"] == PHONE
            assert await service.retry(1, 1, "retry", NOW)
        finally:
            await engine.dispose()

    asyncio.run(scenario())
