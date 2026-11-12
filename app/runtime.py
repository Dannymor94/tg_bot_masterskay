import asyncio
import signal
from datetime import UTC, datetime
from pathlib import Path

from aiogram import Bot
from alembic.config import Config
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic import command
from app.bot.rbac import poll
from app.db.engine import DB_PATH, create_db
from app.db.models import kv
from app.jobs.outbox import Send
from app.jobs.runner import run_jobs
from app.services.invites import InviteService, InviteTokens
from app.settings import StudioConfig


def migrate(path: Path = DB_PATH) -> None:
    config = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    config.attributes["db_path"] = path
    command.upgrade(config, "head")


async def heartbeat(engine: AsyncEngine, now: datetime) -> None:
    value = now.astimezone(UTC).isoformat()
    statement = insert(kv).values(key="heartbeat", value=value)
    async with engine.begin() as connection:
        await connection.execute(
            statement.on_conflict_do_update(index_elements=[kv.c.key], set_={"value": value})
        )


async def serve(
    interval_seconds: int,
    path: Path = DB_PATH,
    *,
    send: Send | None = None,
    cfg: StudioConfig | None = None,
    bot: Bot | None = None,
    owner_id: int | None = None,
    tokens: InviteTokens | None = None,
) -> None:
    await asyncio.to_thread(migrate, path)
    engine = create_db(path)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)
    try:
        access = None
        if bot is not None:
            if cfg is None or owner_id is None or tokens is None:
                raise ValueError("RBAC configuration required")
            access = InviteService(engine, owner_id, cfg, tokens)
            await access.bootstrap(datetime.now(UTC))
        async with asyncio.TaskGroup() as group:
            if bot is not None and access is not None:
                group.create_task(poll(bot, access, stopped))
            if send is not None and cfg is not None:
                group.create_task(run_jobs(engine, send, cfg, stopped))
            while not stopped.is_set():
                await heartbeat(engine, datetime.now(UTC))
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=interval_seconds)
                except TimeoutError:
                    pass
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)
        await engine.dispose()
