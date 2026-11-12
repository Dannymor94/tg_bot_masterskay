"""Scheduled callbacks only write DB effects using the supplied transaction; no IO."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db.models import kv

JobAction = Callable[[AsyncConnection, datetime], Awaitable[None]]


@dataclass(frozen=True)
class Job:
    name: str
    at: time
    action: JobAction
    weekday: int | None = None

    def period(self, now: datetime, timezone: str) -> str:
        if now.utcoffset() is None:
            raise ValueError("aware datetime required")
        local = now.astimezone(ZoneInfo(timezone))
        day = local.date()
        if self.weekday is not None:
            if not 0 <= self.weekday <= 6:
                raise ValueError("invalid weekday")
            day -= timedelta(days=(day.weekday() - self.weekday) % 7)
        if local < datetime.combine(day, self.at, local.tzinfo):
            day -= timedelta(days=7 if self.weekday is not None else 1)
        return day.isoformat()


async def run_period(
    engine: AsyncEngine, job: str, period: str, now: datetime, action: JobAction
) -> bool:
    if now.utcoffset() is None:
        raise ValueError("aware datetime required")
    if not job or ":" in job or not period:
        raise ValueError("invalid scheduled key")
    async with engine.connect() as connection:
        await connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            key = f"scheduled:{job}:{period}"
            result = await connection.execute(
                insert(kv)
                .values(key=key, value=now.isoformat())
                .on_conflict_do_nothing()
                .returning(kv.c.key)
            )
            if result.scalar_one_or_none() is None:
                await connection.rollback()
                return False
            await action(connection, now)
            await connection.commit()
            return True
        except BaseException:
            await connection.rollback()
            raise


async def tick(engine: AsyncEngine, jobs: Sequence[Job], now: datetime, timezone: str) -> None:
    if now.utcoffset() is None:
        raise ValueError("aware datetime required")
    for job in jobs:
        await run_period(engine, job.name, job.period(now, timezone), now, job.action)
