import asyncio
from datetime import UTC, datetime, timedelta, timezone

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError, StatementError

from alembic import command
from app.db.engine import create_db
from app.db.models import answers, events, invites, kv, metadata, outbox, tasks, users

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "bot.db"
    config = Config("alembic.ini")
    config.attributes["db_path"] = path
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        assert set(inspect(connection).get_table_names()) == set(metadata.tables) | {
            "alembic_version"
        }
        assert compare_metadata(MigrationContext.configure(connection), metadata) == []
    engine.dispose()
    yield path
    command.downgrade(config, "base")
    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        assert inspect(connection).get_table_names() == ["alembic_version"]
    engine.dispose()
    command.upgrade(config, "head")


@pytest.mark.parametrize(
    "table,values",
    [
        (
            events,
            dict(
                type="record.created", record_id=1, payload_min={}, created_at=NOW, dedupe_key="e"
            ),
        ),
        (tasks, dict(code="R3", title="test", body="test", dedupe_key="t")),
        (
            outbox,
            dict(
                telegram_user_id=1, kind="text", payload_min={}, next_attempt_at=NOW, dedupe_key="o"
            ),
        ),
        (answers, dict(record_id=1, telegram_user_id=1, kind="rsvp", value="yes", answered_at=NOW)),
        (invites, dict(token_hash="digest", role="admin", created_by=1, expires_at=NOW)),
        (kv, dict(key="scheduled:job:period", value="done")),
    ],
)
def test_unique_effects(database, table, values):
    async def scenario():
        engine = create_db(database)
        try:
            async with engine.begin() as connection:
                await connection.execute(table.insert().values(**values))
            with pytest.raises(IntegrityError):
                async with engine.begin() as connection:
                    await connection.execute(table.insert().values(**values))
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_utc_wal_and_atomicity(database):
    async def scenario():
        engine = create_db(database)
        try:
            local = NOW.astimezone(timezone(timedelta(hours=3)))
            async with engine.begin() as connection:
                assert (
                    await connection.exec_driver_sql("PRAGMA journal_mode")
                ).scalar_one() == "wal"
                assert (await connection.exec_driver_sql("PRAGMA foreign_keys")).scalar_one() == 1
                await connection.execute(
                    users.insert().values(telegram_user_id=1, role="admin", created_at=local)
                )
            async with engine.connect() as connection:
                stored = (await connection.execute(select(users.c.created_at))).scalar_one()
                assert stored == NOW and stored.tzinfo == UTC
            with pytest.raises(StatementError):
                async with engine.begin() as connection:
                    await connection.execute(
                        users.insert().values(
                            telegram_user_id=2, role="client", created_at=NOW.replace(tzinfo=None)
                        )
                    )
            with pytest.raises(IntegrityError):
                async with engine.begin() as connection:
                    await connection.execute(kv.insert().values(key="rollback", value="test"))
                    await connection.execute(
                        tasks.insert().values(
                            code="R3", title="test", body="test", state="invalid", dedupe_key="bad"
                        )
                    )
            async with engine.connect() as connection:
                assert (await connection.execute(select(kv))).all() == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())
