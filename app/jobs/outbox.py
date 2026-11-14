"""Serialized durable sender. A DB write lock spans the bounded Telegram request.

A crash after remote acceptance but before commit can repeat delivery: Telegram
has no idempotency key. No automatic mechanism can provide exactly-once there.
"""

import asyncio
import json
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
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import kv, outbox
from app.domain.delivery import delivery_time, retry_time
from app.services.broadcast_delivery import finish_broadcast, render_news
from app.services.invites import InviteTokens
from app.services.task_delivery import render_card
from app.services.telegram_recipients import is_blocked, mark_blocked
from app.settings import StudioConfig
from app.texts import ru

Send = Callable[[Mapping[str, Any]], Awaitable[None]]
COOLDOWN = "outbox:telegram_not_before"


class TelegramSender:
    def __init__(self, bot: Bot, *, tokens: InviteTokens | None = None):
        self.bot, self.tokens = bot, tokens

    async def __call__(self, row: Mapping[str, Any]) -> None:
        payload = row["payload_min"]
        if row.get("kind") == "rbac.invite":
            if self.tokens is None:
                raise ValueError("invite token provider required")
            token = self.tokens.derive(payload["nonce"]).get_secret_value()
            username = (await self.bot.me()).username
            payload = {"text": ru.INVITE_LINK.format(username=username, token=token)}
        markup = None
        if row.get("kind") == "task.card":
            buttons = []
            task_id = payload["task_id"]
            if payload.get("retry"):
                buttons.append(
                    InlineKeyboardButton(text=ru.CLIENT_RETRY, callback_data=f"cl:retry:{task_id}")
                )
            if payload["take"]:
                buttons.append(
                    InlineKeyboardButton(text=ru.TASK_TAKE, callback_data=f"task:take:{task_id}")
                )
            if payload["finish"]:
                buttons.append(
                    InlineKeyboardButton(text=ru.TASK_DONE, callback_data=f"task:done:{task_id}")
                )
                if payload["reply"]:
                    buttons.append(
                        InlineKeyboardButton(
                            text=ru.TASK_REPLY, callback_data=f"task:reply:{task_id}"
                        )
                    )
            if buttons:
                markup = InlineKeyboardMarkup(inline_keyboard=[buttons])
        elif "reply_markup" in payload:
            raw_markup = json.loads(payload["reply_markup"])
            if "inline_keyboard" in raw_markup:
                markup = InlineKeyboardMarkup.model_validate(raw_markup)
            elif "keyboard" in raw_markup:
                markup = ReplyKeyboardMarkup.model_validate(raw_markup)
            else:
                markup = ReplyKeyboardRemove.model_validate(raw_markup)
        if "photo_file_id" in payload:
            if markup is None:
                await self.bot.send_photo(
                    chat_id=row["telegram_user_id"],
                    photo=payload["photo_file_id"],
                    caption=payload.get("text"),
                )
            else:
                await self.bot.send_photo(
                    chat_id=row["telegram_user_id"],
                    photo=payload["photo_file_id"],
                    caption=payload.get("text"),
                    reply_markup=markup,
                )
        elif markup is not None:
            await self.bot.send_message(
                chat_id=row["telegram_user_id"], text=payload["text"], reply_markup=markup
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
            blocked = await is_blocked(connection, user_id)
            state, attempts, due = "sent", row["attempts"], row["next_attempt_at"]
            affected_broadcasts: set[int] = set()
            rendered = dict(row)
            skip_state = "cancelled"
            if row["kind"] == "task.card" and not blocked:
                rendered = await render_card(
                    connection, {str(key): value for key, value in row.items()}, cfg
                )
            elif row["kind"] == "broadcast.news" and not blocked:
                rendered, skip_state = await render_news(
                    connection, {str(key): value for key, value in row.items()}
                )
            if blocked:
                state = "blocked"
            elif rendered is None:
                state = skip_state
            else:
                attempts += 1
                try:
                    try:
                        async with asyncio.timeout(cfg.delivery.request_timeout_seconds):
                            await send({str(key): value for key, value in rendered.items()})
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
                    # A Telegram block also terminates other queued broadcasts
                    # to this user. They need their outcome counters reconciled.
                    pending_news = (
                        (
                            await connection.execute(
                                select(outbox.c.payload_min).where(
                                    outbox.c.telegram_user_id == user_id,
                                    outbox.c.kind == "broadcast.news",
                                    outbox.c.state.in_(["pending", "summary"]),
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    for item in pending_news:
                        ref = item.get("broadcast_id")
                        if type(ref) is int and ref > 0:
                            affected_broadcasts.add(ref)
                    await mark_blocked(connection, user_id, now)
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
            if row["kind"] == "broadcast.news" and state != "pending":
                identifier = row["payload_min"].get("broadcast_id")
                if type(identifier) is int and identifier > 0:
                    affected_broadcasts.add(identifier)
            for identifier in sorted(affected_broadcasts):
                await finish_broadcast(connection, identifier, cfg, now)
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
