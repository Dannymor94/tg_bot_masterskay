import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from aiogram.methods import SendMessage
from sqlalchemy import select

from app.db.engine import create_db
from app.db.models import outbox, tasks, users
from app.domain.models import OutboxDraft
from app.jobs.outbox import send_one
from app.runtime import migrate
from app.services.outbox import enqueue
from app.settings import load_studio

NOW = datetime(2026, 11, 12, 9, tzinfo=UTC)
CFG = load_studio()
METHOD = SendMessage(chat_id=1, text="synthetic")


@pytest.fixture
def path(tmp_path):
    path = tmp_path / "delivery.db"
    migrate(path)
    return path


def draft(key="one", user=1, kind="text"):
    return OutboxDraft(
        telegram_user_id=user,
        kind=kind,
        payload_min={"text": "synthetic"},
        next_attempt_at=NOW,
        dedupe_key=key,
    )


async def put(engine, item=None, now=NOW):
    async with engine.begin() as connection:
        return await enqueue(connection, item or draft(), CFG, now)


async def rows(engine):
    async with engine.connect() as connection:
        return (await connection.execute(select(outbox).order_by(outbox.c.id))).mappings().all()


def test_enqueue_concurrency_conflict_and_atomicity(path):
    async def scenario():
        engine, other = create_db(path), create_db(path)
        try:
            assert sorted(await asyncio.gather(put(engine), put(other))) == [False, True]
            with pytest.raises(ValueError, match="content conflict"):
                await put(engine, draft().model_copy(update={"payload_min": {"text": "changed"}}))
            with pytest.raises(RuntimeError):
                async with engine.begin() as connection:
                    await connection.execute(
                        tasks.insert().values(
                            code="R3", title="synthetic", body="synthetic", dedupe_key="task"
                        )
                    )
                    await enqueue(connection, draft("rolled-back"), CFG, NOW)
                    raise RuntimeError("synthetic rollback")
            assert len(await rows(engine)) == 1
            async with engine.connect() as connection:
                assert not (await connection.execute(select(tasks))).all()
        finally:
            await engine.dispose()
            await other.dispose()

    asyncio.run(scenario())


def test_quiet_hours_enqueue_retry_and_summary_retention(path):
    async def scenario():
        engine = create_db(path)
        calls = []

        async def send(row):
            calls.append(row["id"])

        try:
            async with engine.begin() as connection:
                await connection.execute(
                    users.insert().values(telegram_user_id=1, role="admin", created_at=NOW)
                )
            night = NOW.replace(hour=20)
            morning = (NOW + timedelta(days=1)).replace(hour=4)
            await put(engine, draft("summary", kind="record.cancelled"), night)
            await put(engine, draft("normal", user=2), night)
            stored = await rows(engine)
            assert [r["state"] for r in stored] == ["summary", "pending"]
            assert all(r["next_attempt_at"] == morning for r in stored)
            assert not await send_one(engine, send, CFG, night)
            await engine.dispose()
            engine = create_db(path)
            assert await send_one(engine, send, CFG, morning)
            assert len(calls) == 1
            assert (await rows(engine))[0]["state"] == "summary"
            # Messages already due before night are also withheld.
            await put(engine, draft("retry", user=2))
            assert not await send_one(engine, send, CFG, night)
        finally:
            await engine.dispose()

    asyncio.run(scenario())
