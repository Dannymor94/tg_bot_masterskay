import asyncio
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.engine import create_db
from app.db.models import kv
from app.health import healthy
from app.runtime import heartbeat, migrate

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_heartbeat_survives_restart(tmp_path):
    path = tmp_path / "bot.db"
    migrate(path)

    async def write(now):
        engine = create_db(path)
        try:
            await heartbeat(engine, now)
            async with engine.connect() as connection:
                assert len((await connection.execute(select(kv))).all()) == 1
        finally:
            await engine.dispose()

    asyncio.run(write(NOW))
    assert healthy(path, NOW, 90)
    asyncio.run(write(NOW + timedelta(seconds=30)))
    assert healthy(path, NOW + timedelta(seconds=120), 90)
    assert not healthy(path, NOW + timedelta(seconds=121), 90)
    assert not healthy(path, NOW, 90)


@pytest.mark.parametrize("value", [None, "invalid", "2026-01-01T00:00:00"])
def test_health_rejects_missing_or_invalid_heartbeat(tmp_path, value):
    path = tmp_path / "bot.db"
    assert not healthy(path, NOW, 90)
    assert not path.exists()
    migrate(path)
    if value is not None:
        import sqlite3

        with sqlite3.connect(path) as connection:
            connection.execute("INSERT INTO kv VALUES ('heartbeat', ?)", (value,))
    assert not healthy(path, NOW, 90)


def test_cli_without_tokens(tmp_path):
    repository = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", "app"],
        cwd=tmp_path,
        env={"HOME": str(tmp_path), "PYTHONPATH": str(repository), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert result.stderr.strip() == "нет переменной YCLIENTS_API_HOST"
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "data").exists()
