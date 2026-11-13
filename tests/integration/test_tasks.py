import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.db.engine import create_db
from app.db.models import audit_log, outbox, tasks, users
from app.domain.tasks import rule
from app.services.access import AccessDenied
from app.services.tasks import TaskService, create_task
from app.settings import load_studio

NOW = datetime(2026, 1, 1, 9, tzinfo=UTC)


async def seed(service):
    await service.bootstrap(NOW)
    async with service.engine.begin() as db:
        await db.execute(
            users.insert(),
            [
                {"telegram_user_id": i, "role": role, "created_at": NOW}
                for i, role in [(2, "admin"), (3, "client"), (4, "teacher")]
            ],
        )


def test_competing_takes_replays_and_ownership(task_db):
    async def scenario():
        engines = [create_db(task_db), create_db(task_db)]
        services = [TaskService(e, 1, load_studio()) for e in engines]
        try:
            a, b = services
            await seed(a)
            task_id = await a.manual(1, "10", "synthetic", NOW)
            assert await a.manual(1, "10", "synthetic", NOW) == task_id
            results = await asyncio.gather(a.take(1, task_id, NOW), b.take(2, task_id, NOW))
            assert sorted(results) == [False, True]
            winner = results.index(True) + 1
            loser = 3 - winner
            with pytest.raises(AccessDenied):
                await deny_other(a, task_id)
            # Owner may finish any taken task; ordinary admin only their own.
            assert await a.finish(winner, task_id, NOW)
            assert not await a.finish(winner, task_id, NOW)
            assert not await a.take(loser, task_id, NOW)
            assert await a.queue(1, "list", NOW) == []
            async with engines[0].connect() as db:
                assert len((await db.execute(select(tasks))).all()) == 1
                actions = (await db.execute(select(audit_log.c.action))).scalars().all()
                assert actions.count("tasks.taken") == actions.count("tasks.done") == 1
                assert actions.count("tasks.created") == 1
        finally:
            for engine in engines:
                await engine.dispose()

    # A third admin is always neither the winner nor owner.
    async def deny_other(service, task_id):
        async with service.engine.begin() as db:
            await db.execute(
                users.insert().values(telegram_user_id=5, role="admin", created_at=NOW)
            )
        await service.finish(5, task_id, NOW)

    asyncio.run(scenario())


@pytest.mark.parametrize("code", ["R3", "R4"])
def test_source_create_reply_and_phone_lifetime(task_db, code):
    async def scenario():
        engine = create_db(task_db)
        service = TaskService(engine, 1, load_studio())
        try:
            await seed(service)
            draft = rule(
                code, "source:1", service.cfg, NOW, body="synthetic private input", requester_id=3
            )
            assert draft is not None
            async with service.transaction(NOW) as db:
                task_id = await create_task(db, draft, service.cfg, NOW)
                assert await create_task(db, draft, service.cfg, NOW) == task_id
            assert await service.queue(2, "list", NOW) == [task_id]
            await service.take(2, task_id, NOW)
            if code == "R3":
                assert await service.finish(2, task_id, NOW, reply="synthetic answer")
                assert not await service.finish(2, task_id, NOW, reply="retry")
            else:
                async with engine.connect() as db:
                    assert (await db.execute(select(tasks.c.body))).scalar_one() == ""
                await service.finish(2, task_id, NOW)
            async with service.transaction(NOW) as db:
                assert await create_task(db, draft, service.cfg, NOW) == task_id
            async with engine.connect() as db:
                rows = (await db.execute(select(outbox))).mappings().all()
                assert "synthetic private input" not in repr(rows)
                replies = [r for r in rows if r["kind"] == "task.reply"]
                assert len(replies) == int(code == "R3")
                if replies:
                    assert replies[0]["telegram_user_id"] == 3
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("code", ["R1", "R2", "R3", "R4", "R5", "R6", "R8"])
def test_all_rule_state_transitions(task_db, code):
    from datetime import timedelta

    async def scenario():
        engine = create_db(task_db)
        service = TaskService(engine, 1, load_studio())
        try:
            await seed(service)
            draft = rule(
                code,
                "state-matrix",
                service.cfg,
                NOW,
                body="synthetic",
                requester_id=3,
                starts_at=NOW + timedelta(days=1),
                score=1,
                rank=1,
            )
            assert draft is not None
            async with service.transaction(NOW) as db:
                task_id = await create_task(db, draft, service.cfg, NOW)
            assert not await service.finish(1, task_id, NOW)
            assert await service.take(1, task_id, NOW)
            assert not await service.take(2, task_id, NOW)
            assert await service.finish(1, task_id, NOW)
            assert not await service.take(1, task_id, NOW)
            assert not await service.finish(1, task_id, NOW)
        finally:
            await engine.dispose()

    asyncio.run(scenario())
