"""Idempotent task maintenance, in scheduler-owned write transactions."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db.models import outbox, tasks
from app.domain.tasks import reminder_due
from app.jobs.scheduler import run_period
from app.services.tasks import notify_admins
from app.settings import StudioConfig


async def remind(db: AsyncConnection, now: datetime, cfg: StudioConfig) -> None:
    rows = (await db.execute(select(tasks).where(tasks.c.state == "open"))).mappings().all()
    for row in rows:
        last = row["last_ping_at"]
        if last is not None and reminder_due(last, now, cfg):
            await notify_admins(db, row["id"], f"remind:{now.isoformat()}", cfg, now)
            await db.execute(tasks.update().where(tasks.c.id == row["id"]).values(last_ping_at=now))


async def cleanup(db: AsyncConnection, now: datetime, cfg: StudioConfig) -> None:
    ids = list(
        (
            await db.execute(
                select(tasks.c.id).where(
                    tasks.c.state == "done",
                    tasks.c.done_at <= now - timedelta(days=cfg.tasks.done_ttl_days),
                    or_(tasks.c.title != "", tasks.c.body != "", tasks.c.ref.is_not(None)),
                )
            )
        ).scalars()
    )
    for task_id in ids:
        await db.execute(
            tasks.update().where(tasks.c.id == task_id).values(title="", body="", ref=None)
        )
        # Replies are the only text-bearing task effects. Clear their copies too,
        # cancelling undelivered messages; keep unique keys and audit intact.
        rows = (
            (
                await db.execute(
                    select(outbox).where(
                        outbox.c.kind.in_(["task.card", "task.reply", "task.reply_help"]),
                        outbox.c.payload_min["task_id"].as_integer() == task_id,
                    )
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            state = "cancelled" if row["state"] in ("pending", "summary") else row["state"]
            await db.execute(
                outbox.update()
                .where(outbox.c.id == row["id"])
                .values(
                    payload_min={"task_id": task_id},
                    state=state,
                )
            )


async def maintain(engine: AsyncEngine, cfg: StudioConfig, now: datetime) -> None:
    local = now.astimezone(ZoneInfo(cfg.studio.timezone))

    async def reminders(db: AsyncConnection, instant: datetime) -> None:
        await remind(db, instant, cfg)

    async def retention(db: AsyncConnection, instant: datetime) -> None:
        await cleanup(db, instant, cfg)

    await run_period(engine, "task-reminders", local.isoformat(timespec="minutes"), now, reminders)
    await run_period(engine, "task-retention", local.date().isoformat(), now, retention)
