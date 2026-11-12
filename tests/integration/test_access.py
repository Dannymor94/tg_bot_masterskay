import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.db.engine import create_db
from app.db.models import audit_log, outbox, users
from app.runtime import migrate
from app.services.access import AccessDenied, AccessService
from app.settings import load_studio

NOW = datetime(2026, 1, 1, 9, tzinfo=UTC)


@pytest.fixture
def access(tmp_path):
    path = tmp_path / "access.db"
    migrate(path)
    return path


def test_owner_bootstrap_is_idempotent_and_concurrent(access):
    async def scenario():
        engines = [create_db(access), create_db(access)]
        try:
            services = [AccessService(engine, 1, load_studio()) for engine in engines]
            await asyncio.gather(*(service.bootstrap(NOW) for service in services))
            await services[0].bootstrap(NOW)
            async with engines[0].connect() as db:
                row = (await db.execute(select(users))).mappings().one()
                assert row["telegram_user_id"] == 1 and row["is_owner"]
                assert row["role"] == "admin"
                assert len((await db.execute(select(audit_log))).all()) == 1
            with pytest.raises(AccessDenied):
                await AccessService(engines[0], 2, load_studio()).bootstrap(NOW)
            async with engines[0].connect() as db:
                assert len((await db.execute(select(users))).all()) == 1
        finally:
            for engine in engines:
                await engine.dispose()

    asyncio.run(scenario())


def test_service_checks_fresh_roles_and_revocation(access):
    async def scenario():
        engine = create_db(access)
        service = AccessService(engine, 1, load_studio())
        try:
            await service.bootstrap(NOW)
            async with engine.begin() as db:
                await db.execute(
                    users.insert().values(telegram_user_id=2, role="admin", created_at=NOW)
                )
                await db.execute(
                    users.insert().values(
                        telegram_user_id=3, role="admin", is_owner=True, created_at=NOW
                    )
                )
            assert await service.menu(2, "old-button", NOW) == "admin"
            for actor in [2, 3, 99]:
                with pytest.raises(AccessDenied):
                    await service.revoke(actor, 1, NOW)
            with pytest.raises(AccessDenied):
                await service.revoke(1, 1, NOW)
            with pytest.raises(AccessDenied):
                await service.menu(99, "unknown", NOW)
            assert await service.revoke(1, 2, NOW)
            assert not await service.revoke(1, 2, NOW)
            with pytest.raises(AccessDenied):
                await service.menu(2, "old-button", NOW)
            async with engine.connect() as db:
                assert (
                    await db.execute(select(outbox.c.state).where(outbox.c.telegram_user_id == 2))
                ).scalar_one() == "cancelled"
                actions = (await db.execute(select(audit_log.c.action))).scalars().all()
                assert actions == ["owner.bootstrap", "access.revoked"]
        finally:
            await engine.dispose()

    asyncio.run(scenario())
