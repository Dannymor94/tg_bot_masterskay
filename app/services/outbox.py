"""Enqueue in the caller's transaction, alongside its event/task."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import outbox, users
from app.domain.delivery import delivery_time
from app.domain.models import OutboxDraft
from app.settings import StudioConfig


async def enqueue(
    connection: AsyncConnection, draft: OutboxDraft, cfg: StudioConfig, now: datetime
) -> bool:
    allowed = delivery_time(max(now, draft.next_attempt_at), cfg)
    result = await connection.execute(
        insert(outbox)
        .values(**draft.model_dump(exclude={"next_attempt_at"}), next_attempt_at=allowed)
        .on_conflict_do_nothing(index_elements=[outbox.c.dedupe_key])
        .returning(outbox.c.id)
    )
    identity = draft.model_dump(exclude={"next_attempt_at"})
    if result.scalar_one_or_none() is None:
        previous = (
            (
                await connection.execute(
                    select(outbox).where(outbox.c.dedupe_key == draft.dedupe_key)
                )
            )
            .mappings()
            .one()
        )
        if any(previous[key] != value for key, value in identity.items()):
            raise ValueError("outbox dedupe_key content conflict")
        return False
    recipient = (
        (
            await connection.execute(
                select(users).where(users.c.telegram_user_id == draft.telegram_user_id)
            )
        )
        .mappings()
        .first()
    )
    state = "pending"
    if recipient is not None and recipient["blocked_at"] is not None:
        state = "blocked"
    elif (
        recipient is not None
        and recipient["role"] == "admin"
        and draft.kind in cfg.notifications.admin_instant_events
        and delivery_time(now, cfg) > now
    ):
        state = "summary"
    await connection.execute(
        outbox.update().where(outbox.c.dedupe_key == draft.dedupe_key).values(state=state)
    )
    return True
