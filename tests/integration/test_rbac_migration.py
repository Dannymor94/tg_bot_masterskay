from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command


def test_upgrade_preserves_existing_users(tmp_path):
    path = tmp_path / "upgrade.db"
    config = Config("alembic.ini")
    config.attributes["db_path"] = path
    command.upgrade(config, "499d79fa6668")
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO users (telegram_user_id, role, created_at) "
                "VALUES (1, 'admin', '2026-01-01 00:00:00')"
            )
        )
    command.upgrade(config, "head")
    with engine.connect() as db:
        assert db.execute(text("SELECT role, access_revoked FROM users")).one() == ("admin", 0)
    command.downgrade(config, "499d79fa6668")
    with engine.connect() as db:
        assert db.execute(text("SELECT role FROM users")).scalar_one() == "admin"
    engine.dispose()
