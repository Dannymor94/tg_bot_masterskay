from pathlib import Path

from sqlalchemy import URL, event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

DB_PATH = Path("data/bot.db")


def create_db(path: Path = DB_PATH) -> AsyncEngine:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(URL.create("sqlite+aiosqlite", database=str(path)))

    @event.listens_for(engine.sync_engine, "connect")
    def configure(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine
