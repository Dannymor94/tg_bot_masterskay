"""Serialized durable sender. A DB write lock spans the bounded Telegram request.

A crash after remote acceptance but before commit can repeat delivery: Telegram
has no idempotency key. No automatic mechanism can provide exactly-once there.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import kv, outbox, users
from app.domain.delivery import delivery_time, retry_time
from app.settings import StudioConfig

Send = Callable[[Mapping[str, Any]], Awaitable[None]]
COOLDOWN = "outbox:telegram_not_before"


class TelegramSender:
    def __init__(self, bot: Bot):
        self.bot = bot

    async def __call__(self, row: Mapping[str, Any]) -> None:
        payload = row["payload_min"]
        if "photo_file_id" in payload:
            await self.bot.send_photo(
                chat_id=row["telegram_user_id"],
                photo=payload["photo_file_id"],
                caption=payload.get("text"),
            )
        else:
            await self.bot.send_message(chat_id=row["telegram_user_id"], text=payload["text"])


async def send_one(
    engine: AsyncEngine,
    send: Send,
    cfg: StudioConfig,
    now: datetime,
    *,
    clock: Callable[[], datetime] | None = None,
) -> bool:
    # Enqueue enforces quiet hours; retrying messages also wait through the night.
    if delivery_time(now, cfg) > now:
        return False
    async with engine.connect() as connection:
        await connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            cooldown = (
                await connection.execute(select(kv.c.value).where(kv.c.key == COOLDOWN))
            ).scalar_one_or_none()
            if cooldown and datetime.fromisoformat(cooldown) > now:
                await connection.rollback()
                return False
            row = (
                (
                    await connection.execute(
                        select(outbox)
                        .where(outbox.c.state == "pending", outbox.c.next_attempt_at <= now)
                        .order_by(outbox.c.next_attempt_at, outbox.c.id)
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                await connection.rollback()
                return False
            user_id = row["telegram_user_id"]
            blocked = (
                await connection.execute(
                    select(users.c.blocked_at).where(users.c.telegram_user_id == user_id)
                )
            ).scalar_one_or_none()
            state, attempts, due = "sent", row["attempts"], row["next_attempt_at"]
            if blocked is not None:
                state = "blocked"
            else:
                attempts += 1
                try:
                    try:
                        async with asyncio.timeout(cfg.delivery.request_timeout_seconds):
                            await send({str(key): value for key, value in row.items()})
                    finally:
                        if clock is not None:
                            now = clock()
                except TelegramRetryAfter as error:
                    state = "pending"
                    due = max(
                        retry_time(now, attempts, cfg), now + timedelta(seconds=error.retry_after)
                    )
                    statement = insert(kv).values(key=COOLDOWN, value=due.isoformat())
                    await connection.execute(
                        statement.on_conflict_do_update(
                            index_elements=[kv.c.key], set_={"value": due.isoformat()}
                        )
                    )
                except TelegramForbiddenError:
                    state = "blocked"
                    await connection.execute(
                        users.update()
                        .where(users.c.telegram_user_id == user_id)
                        .values(blocked_at=now)
                    )
                    await connection.execute(
                        outbox.update()
                        .where(
                            outbox.c.telegram_user_id == user_id,
                            outbox.c.state.in_(["pending", "summary"]),
                        )
                        .values(state="blocked")
                    )
                except (TelegramBadRequest, KeyError, TypeError, ValueError):
                    state = "failed"
                except (TelegramNetworkError, TelegramServerError, OSError, TimeoutError):
                    state = "pending"
                    due = retry_time(now, attempts, cfg)
            await connection.execute(
                outbox.update()
                .where(outbox.c.id == row["id"])
                .values(state=state, attempts=attempts, next_attempt_at=due)
            )
            # Persist a global rate limit across process restarts, including successful sends.
            if state != "pending":
                due = now + timedelta(seconds=1 / cfg.broadcasts.messages_per_second)
                statement = insert(kv).values(key=COOLDOWN, value=due.isoformat())
                await connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[kv.c.key], set_={"value": due.isoformat()}
                    )
                )
            await connection.commit()
            return True
        except BaseException:
            await connection.rollback()
            raise
