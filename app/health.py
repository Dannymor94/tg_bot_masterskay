import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from app.db.engine import DB_PATH
from app.settings import load_studio
from app.texts import ru


def healthy(path: Path, now: datetime, max_age_seconds: int) -> bool:
    try:
        uri = f"file:{quote(str(path.resolve()))}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            row = connection.execute("SELECT value FROM kv WHERE key='heartbeat'").fetchone()
        finally:
            connection.close()
        if row is None:
            return False
        timestamp = datetime.fromisoformat(row[0])
        if timestamp.utcoffset() is None:
            return False
        age = (now - timestamp).total_seconds()
        return 0 <= age <= max_age_seconds
    except (sqlite3.Error, ValueError, TypeError):
        return False


def main() -> int:
    try:
        config = load_studio()
    except ValueError as error:
        print(str(error))
        return 1
    ok = healthy(DB_PATH, datetime.now(UTC), config.runtime.heartbeat_max_age_seconds)
    print(ru.HEALTH_OK if ok else ru.HEALTH_FAILED)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
