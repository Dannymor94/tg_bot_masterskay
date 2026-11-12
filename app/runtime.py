import asyncio
import signal
from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic import command
from app.db.engine import DB_PATH, create_db
from app.db.models import kv


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


async def serve(interval_seconds: int, path: Path = DB_PATH) -> None:
    await asyncio.to_thread(migrate, path)
    engine = create_db(path)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)
    try:
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
