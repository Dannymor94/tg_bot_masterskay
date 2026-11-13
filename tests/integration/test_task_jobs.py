import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage
from sqlalchemy import select

from app.db.engine import create_db
from app.db.models import audit_log, kv, outbox, tasks
from app.domain.tasks import rule
from app.jobs.outbox import send_one
from app.jobs.tasks import maintain
from app.services.tasks import TaskService, create_task
from app.settings import load_studio
from tests.integration.test_tasks import NOW, seed


def test_reminders_restart_quiet_hours_and_429(task_db):
    async def scenario():
        engine = create_db(task_db)
        cfg = load_studio()
        service = TaskService(engine, 1, cfg)
        send = AsyncMock(
            side_effect=TelegramRetryAfter(
                method=SendMessage(chat_id=1, text="synthetic"),
                message="synthetic",
                retry_after=100,
            )
        )
        try:
            await seed(service)
            task_id = await service.manual(1, "create", "synthetic", NOW)
            await maintain(engine, cfg, NOW + timedelta(minutes=59))
            async with engine.connect() as db:
                assert len((await db.execute(select(outbox))).all()) == 2
            due = NOW + timedelta(minutes=cfg.tasks.overdue_ping_minutes)
            await asyncio.gather(maintain(engine, cfg, due), maintain(engine, cfg, due))
            await maintain(engine, cfg, due)
            async with engine.connect() as db:
                assert len((await db.execute(select(outbox))).all()) == 4
            assert await send_one(engine, send, cfg, due)
            assert not await send_one(engine, send, cfg, due + timedelta(seconds=99))
            assert send.await_count == 1
            night = NOW.replace(hour=20)
            await maintain(engine, cfg, night)
            assert not await send_one(engine, send, cfg, night)
            async with engine.connect() as db:
                assert len((await db.execute(select(outbox))).all()) == 4
                assert (await db.execute(select(tasks.c.last_ping_at))).scalar_one() == due
            await service.take(1, task_id, due)
            await maintain(engine, cfg, due + timedelta(hours=2))
            async with engine.connect() as db:
                assert len((await db.execute(select(outbox))).all()) == 5  # take confirmation only
                assert (
                    await db.execute(
                        select(kv.c.key).where(kv.c.key.like("scheduled:task-reminders:%"))
                    )
                ).all()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_ttl_keeps_keys_audit_open_tasks_and_scrubs_reply_copies(task_db):
    async def scenario():
        engine = create_db(task_db)
        cfg = load_studio()
        service = TaskService(engine, 1, cfg)
        draft = rule("R3", "question", cfg, NOW, body="synthetic question", requester_id=3)
        assert draft is not None
        try:
            await seed(service)
            async with service.transaction(NOW) as db:
                task_id = await create_task(db, draft, cfg, NOW)
            open_id = await service.manual(1, "open", "keep open", NOW)
            await service.take(1, task_id, NOW)
            await service.finish(1, task_id, NOW, reply="synthetic response")
            before = NOW + timedelta(days=cfg.tasks.done_ttl_days) - timedelta(days=1)
            await maintain(engine, cfg, before)
            async with engine.connect() as db:
                assert (
                    await db.execute(select(tasks.c.body).where(tasks.c.id == task_id))
                ).scalar_one() == "synthetic question"
                audit = (await db.execute(select(audit_log))).all()
            expired = NOW + timedelta(days=cfg.tasks.done_ttl_days)
            await maintain(engine, cfg, expired)
            await maintain(engine, cfg, expired)
            async with service.transaction(expired) as db:
                assert await create_task(db, draft, cfg, expired) == task_id
            async with engine.connect() as db:
                closed = (
                    (await db.execute(select(tasks).where(tasks.c.id == task_id))).mappings().one()
                )
                assert closed["state"] == "done" and closed["dedupe_key"] == draft.dedupe_key
                assert closed["title"] == closed["body"] == "" and closed["ref"] is None
                assert (
                    await db.execute(select(tasks.c.body).where(tasks.c.id == open_id))
                ).scalar_one() == "keep open"
                assert (await db.execute(select(audit_log))).all() == audit
                rows = (
                    (
                        await db.execute(
                            select(outbox).where(
                                outbox.c.payload_min["task_id"].as_integer() == task_id
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                assert all(r["payload_min"] == {"task_id": task_id} for r in rows)
                assert all(r["state"] == "cancelled" for r in rows)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_maintenance_failure_retries_without_scheduled_marker(task_db, monkeypatch):
    from app.jobs import tasks as task_jobs

    async def scenario():
        engine = create_db(task_db)
        cfg = load_studio()
        service = TaskService(engine, 1, cfg)
        try:
            await seed(service)
            await service.manual(1, "create", "synthetic", NOW)
            original = task_jobs.notify_admins

            async def fail(*args):
                await original(*args)
                raise RuntimeError("synthetic maintenance failure")

            monkeypatch.setattr(task_jobs, "notify_admins", fail)
            due = NOW + timedelta(minutes=cfg.tasks.overdue_ping_minutes)
            with pytest.raises(RuntimeError):
                await maintain(engine, cfg, due)
            async with engine.connect() as db:
                assert len((await db.execute(select(outbox))).all()) == 2
                assert (await db.execute(select(tasks.c.last_ping_at))).scalar_one() == NOW
                assert not (
                    await db.execute(select(kv.c.key).where(kv.c.key.like("scheduled:%")))
                ).all()
            monkeypatch.setattr(task_jobs, "notify_admins", original)
            await maintain(engine, cfg, due)
            async with engine.connect() as db:
                assert len((await db.execute(select(outbox))).all()) == 4
        finally:
            await engine.dispose()

    asyncio.run(scenario())
