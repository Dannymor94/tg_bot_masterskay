"""Private Telegram transport for admin announcements and user opt-out."""

import logging
from collections.abc import Callable
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from app.services.access import AccessDenied
from app.services.broadcasts import BroadcastService

logger = logging.getLogger(__name__)


def private(message: Message, actor_id: int) -> bool:
    return message.chat.type == "private" and message.chat.id == actor_id


def number(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or int(value) <= 0:
        raise ValueError("invalid broadcast number")
    return int(value)


def build_router(service: BroadcastService, clock: Callable[[], datetime]) -> Router:
    router = Router(name="broadcasts")

    @router.message(Command("announce", "unsubscribe"))
    async def command(message: Message, data: CommandObject) -> None:
        actor = message.from_user
        if actor is None or actor.is_bot or not private(message, actor.id):
            return
        try:
            if data.command == "unsubscribe" and not data.args:
                await service.unsubscribe(actor.id, clock())
            elif data.command == "announce":
                segment_name, body = (data.args or "").split(maxsplit=1)
                photo = message.photo[-1].file_id if message.photo else None
                await service.create(
                    actor.id,
                    str(message.message_id),
                    segment_name,
                    body,
                    clock(),
                    photo_file_id=photo,
                )
        except (AccessDenied, ValueError):
            return
        except Exception:
            # Never leak the announcement text or the original Telegram Update.
            logger.error("Broadcast command failed actor=%s", actor.id)

    @router.callback_query(F.data.startswith("bc:"))
    async def callback(query: CallbackQuery) -> None:
        actor, message = query.from_user, query.message
        if actor.is_bot or not isinstance(message, Message) or not private(message, actor.id):
            return
        try:
            prefix, action, identifier = (query.data or "").split(":")
            if prefix != "bc":
                return
            broadcast_id = number(identifier)
            if action == "send":
                await service.confirm(actor.id, broadcast_id, clock())
            elif action == "cancel":
                await service.cancel(actor.id, broadcast_id, clock())
            elif action == "off":
                await service.unsubscribe(actor.id, clock(), broadcast_id=broadcast_id)
        except (AccessDenied, ValueError):
            return
        except Exception:
            logger.error("Broadcast callback failed actor=%s", actor.id)

    return router
