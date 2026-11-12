from pathlib import Path

from sqlalchemy import URL, create_engine

from alembic import context
from app.db.engine import DB_PATH
from app.db.models import metadata

path = Path(context.config.attributes.get("db_path", DB_PATH))
path.parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(URL.create("sqlite", database=str(path)))
with engine.connect() as connection:
    connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    connection.commit()
    context.configure(connection=connection, target_metadata=metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
