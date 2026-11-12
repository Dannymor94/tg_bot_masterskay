"""Tick registered DB jobs and deliver committed messages until shutdown."""

import asyncio
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine

from app.jobs.outbox import Send, send_one
from app.jobs.scheduler import Job, tick
from app.settings import StudioConfig


async def run_jobs(
    engine: AsyncEngine,
    send: Send,
    cfg: StudioConfig,
    stopped: asyncio.Event,
    jobs: Sequence[Job] = (),
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    while not stopped.is_set():
        await tick(engine, jobs, clock(), cfg.studio.timezone)
        await send_one(engine, send, cfg, clock(), clock=clock)
        try:
            await asyncio.wait_for(stopped.wait(), timeout=cfg.delivery.poll_seconds)
        except TimeoutError:
            pass
