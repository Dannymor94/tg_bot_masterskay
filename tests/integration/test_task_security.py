import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.engine import create_db
from app.db.models import audit_log, events, outbox, tasks, users
from app.domain.models import TaskDraft
from app.domain.tasks import rule
from app.services.access import AccessDenied
from app.services.tasks import TaskService, create_task
from app.settings import load_studio
from tests.integration.test_tasks import NOW, seed, task_db  # noqa: F401


@pytest.mark.parametrize("action", ["manual", "queue", "take", "finish", "reply", "prompt"])
@pytest.mark.parametrize("actor", [2, 3, 4, 99])
def test_fresh_permissions(task_db, action, actor):
    async def scenario():
        engine = create_db(task_db)
        service = TaskService(engine, 1, load_studio())
        try:
            await seed(service)
            task_id = await service.manual(1, "initial", "synthetic", NOW)
            if actor == 2:
                await service.revoke(1, 2, NOW)
            async with engine.connect() as db:
                before = [
                    len((await db.execute(select(t))).all()) for t in (tasks, outbox, audit_log)
                ]
            operations = {
                "manual": lambda: service.manual(actor, "new", "synthetic", NOW),
                "queue": lambda: service.queue(actor, "new", NOW),
                "take": lambda: service.take(actor, task_id, NOW),
                "finish": lambda: service.finish(actor, task_id, NOW),
                "reply": lambda: service.finish(actor, task_id, NOW, reply="synthetic"),
                "prompt": lambda: service.reply_prompt(actor, task_id, "new", NOW),
            }
            with pytest.raises(AccessDenied):
                await operations[action]()
            async with engine.connect() as db:
                assert before == [
                    len((await db.execute(select(t))).all()) for t in (tasks, outbox, audit_log)
                ]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["manual", "take", "finish", "reply"])
def test_audit_failure_rolls_back_all_effects(task_db, monkeypatch, action):
    async def scenario():
        engine = create_db(task_db)
        service = TaskService(engine, 1, load_studio())
        try:
            await seed(service)
            draft = rule("R3", "question", service.cfg, NOW, body="synthetic", requester_id=3)
            async with service.transaction(NOW) as db:
                task_id = await create_task(db, draft, service.cfg, NOW)
            if action in ("finish", "reply"):
                await service.take(1, task_id, NOW)
            async with engine.connect() as db:
                before = [(await db.execute(select(t))).all() for t in (tasks, outbox, audit_log)]

            async def fail(*args):
                raise RuntimeError("synthetic audit failure")

            monkeypatch.setattr(service, "audit", fail)
            with pytest.raises(RuntimeError):
                if action == "manual":
                    await service.manual(1, "new", "synthetic", NOW)
                elif action == "take":
                    await service.take(1, task_id, NOW)
                else:
                    await service.finish(
                        1, task_id, NOW, reply="answer" if action == "reply" else None
                    )
            async with engine.connect() as db:
                assert before == [
                    (await db.execute(select(t))).all() for t in (tasks, outbox, audit_log)
                ]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_event_task_notifications_rollback_and_content_conflict(task_db):
    async def scenario():
        engine = create_db(task_db)
        service = TaskService(engine, 1, load_studio())
        draft = rule("R3", "question", service.cfg, NOW, body="synthetic", requester_id=3)
        try:
            await seed(service)
            with pytest.raises(RuntimeError):
                async with service.transaction(NOW) as db:
                    await db.execute(
                        events.insert().values(
                            type="synthetic",
                            record_id=1,
                            payload_min={},
                            created_at=NOW,
                            dedupe_key="source",
                        )
                    )
                    await create_task(db, draft, service.cfg, NOW)
                    raise RuntimeError("synthetic transaction failure")
            async with engine.connect() as db:
                for table in (tasks, outbox, events):
                    assert (await db.execute(select(table))).all() == []
            async with service.transaction(NOW) as db:
                await create_task(db, draft, service.cfg, NOW)
            with pytest.raises(ValueError, match="content conflict"):
                async with service.transaction(NOW + timedelta(minutes=1)) as db:
                    await create_task(
                        db, TaskDraft(**{**draft.model_dump(), "body": "changed"}), service.cfg, NOW
                    )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
