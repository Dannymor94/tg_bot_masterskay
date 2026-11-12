import asyncio
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import select

from app.db.engine import create_db
from app.db.models import kv, outbox
from app.domain.models import OutboxDraft
from app.jobs.runner import run_jobs
from app.jobs.scheduler import Job, run_period, tick
from app.runtime import migrate
from app.services.outbox import enqueue
from app.settings import load_studio

NOW = datetime(2026, 11, 12, 9, tzinfo=UTC)
CFG = load_studio()


@pytest.mark.parametrize(
    "now,weekday,expected",
    [
        (NOW.replace(hour=3), None, "2026-11-11"),
        (NOW.replace(hour=4), None, "2026-11-12"),
        (NOW, 0, "2026-11-09"),
        (datetime(2026, 11, 9, 3, tzinfo=UTC), 0, "2026-11-02"),
        (datetime(2026, 11, 9, 4, tzinfo=UTC), 0, "2026-11-09"),
    ],
)
def test_latest_due_period_in_moscow(now, weekday, expected):
    async def action(connection, now):
        pass

    job = Job("synthetic", time(7), action, weekday)
    assert job.period(now, CFG.studio.timezone) == expected


def test_scheduler_concurrency_rollback_restart_and_next_period(tmp_path):
    path = tmp_path / "scheduler.db"
    migrate(path)

    async def scenario():
        engine, other = create_db(path), create_db(path)
        calls = []

        async def action(connection, now):
            period = now.date().isoformat()
            calls.append(period)
            await enqueue(
                connection,
                OutboxDraft(
                    telegram_user_id=1,
                    kind="text",
                    payload_min={"text": "synthetic"},
                    next_attempt_at=now,
                    dedupe_key=f"job:{period}",
                ),
                CFG,
                now,
            )

        async def fail(connection, now):
            await action(connection, now)
            raise RuntimeError("synthetic crash")

        try:
            with pytest.raises(RuntimeError):
                await run_period(engine, "morning", "2026-11-12", NOW, fail)
            async with engine.connect() as connection:
                assert not (await connection.execute(select(kv))).all()
                assert not (await connection.execute(select(outbox))).all()
            results = await asyncio.gather(
                run_period(engine, "morning", "2026-11-12", NOW, action),
                run_period(other, "morning", "2026-11-12", NOW, action),
            )
            assert sorted(results) == [False, True]
            await engine.dispose()
            engine = create_db(path)
            await tick(engine, [Job("morning", time(7), action)], NOW, CFG.studio.timezone)
            assert calls == ["2026-11-12", "2026-11-12"]  # rollback + committed run
            tomorrow = NOW + timedelta(days=1)
            await tick(engine, [Job("morning", time(7), action)], tomorrow, CFG.studio.timezone)
            async with engine.connect() as connection:
                assert len((await connection.execute(select(outbox))).all()) == 2
                keys = (await connection.execute(select(kv.c.key))).scalars().all()
                assert sorted(keys) == [
                    "scheduled:morning:2026-11-12",
                    "scheduled:morning:2026-11-13",
                ]
        finally:
            await engine.dispose()
            await other.dispose()

    asyncio.run(scenario())


def test_worker_runs_scheduled_effect_then_sender_and_stops(tmp_path):
    path = tmp_path / "worker.db"
    migrate(path)

    async def scenario():
        engine = create_db(path)
        stopped = asyncio.Event()
        calls = []

        async def action(connection, now):
            await enqueue(
                connection,
                OutboxDraft(
                    telegram_user_id=1,
                    kind="text",
                    payload_min={"text": "synthetic"},
                    next_attempt_at=now,
                    dedupe_key="worker",
                ),
                CFG,
                now,
            )

        async def send(row):
            calls.append(row["id"])
            stopped.set()

        try:
            await run_jobs(
                engine, send, CFG, stopped, [Job("worker", time(7), action)], clock=lambda: NOW
            )
            assert len(calls) == 1
            assert stopped.is_set()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
