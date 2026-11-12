"""Durable Telegram delivery blocks, independent of user registration."""

from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import kv, users


def blocked_key(telegram_user_id: int) -> str:
    return f"telegram:blocked:{telegram_user_id}"


async def is_blocked(connection: AsyncConnection, telegram_user_id: int) -> bool:
    return bool(
        (
            await connection.execute(
                select(
                    or_(
                        select(kv).where(kv.c.key == blocked_key(telegram_user_id)).exists(),
                        select(users)
                        .where(
                            users.c.telegram_user_id == telegram_user_id,
                            or_(users.c.blocked_at.is_not(None), users.c.access_revoked.is_(True)),
                        )
                        .exists(),
                    )
                )
            )
        ).scalar_one()
    )


async def mark_blocked(connection: AsyncConnection, telegram_user_id: int, now: datetime) -> None:
    # Both writes belong to the sender's transaction with the outbox result.
    statement = insert(kv).values(
        key=blocked_key(telegram_user_id), value=now.astimezone(UTC).isoformat()
    )
    await connection.execute(statement.on_conflict_do_nothing(index_elements=[kv.c.key]))
    await connection.execute(
        users.update().where(users.c.telegram_user_id == telegram_user_id).values(blocked_at=now)
    )
