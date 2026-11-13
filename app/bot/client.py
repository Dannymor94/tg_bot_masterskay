"""Authenticated private-chat transport; state and authorization live in services."""

import logging
from collections.abc import Callable
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message
from pydantic import SecretStr

from app.services.access import AccessDenied
from app.services.client_onboarding import ClientService

logger = logging.getLogger(__name__)


def private_actor(message: Message, actor_id: int) -> bool:
    return message.chat.type == "private" and message.chat.id == actor_id


def build_router(service: ClientService, clock: Callable[[], datetime]) -> Router:
    router = Router(name="client")

    @router.message(CommandStart())
    async def start(message: Message, command: CommandObject) -> None:
        actor = message.from_user
        if actor is None or actor.is_bot or not private_actor(message, actor.id):
            return
        # Invite links belong exclusively to the preceding T-201 router.
        if (command.args or "").startswith("invite_"):
            return
        try:
            await service.start(actor.id, message.message_id, command.args or "", clock())
        except AccessDenied:
            return
        except Exception:
            logger.error("Client start failed actor=%s", actor.id)

    @router.message(F.contact)
    async def contact(message: Message) -> None:
        actor, contact = message.from_user, message.contact
        if actor is None or actor.is_bot or contact is None or not private_actor(message, actor.id):
            return
        try:
            await service.contact(
                actor.id,
                contact.user_id,
                SecretStr(contact.phone_number),
                message.message_id,
                clock(),
            )
        except AccessDenied:
            return
        except Exception:
            # Do not let framework error handlers log the original contact Update.
            logger.error("Client contact failed actor=%s", actor.id)

    @router.callback_query(F.data.startswith("cl:"))
    async def choice(query: CallbackQuery) -> None:
        actor, message = query.from_user, query.message
        if actor.is_bot or not isinstance(message, Message) or not private_actor(message, actor.id):
            return
        pieces = (query.data or "").split(":")
        if len(pieces) != 3 or not pieces[2].isascii() or not pieces[2].isdecimal():
            return
        try:
            if pieces[1] == "retry":
                await service.retry(actor.id, int(pieces[2]), query.id, clock())
            elif pieces[1] in ("consent", "yes", "no"):
                await service.choice(actor.id, pieces[1], int(pieces[2]), clock())
        except AccessDenied:
            return
        except Exception:
            logger.error("Client callback failed actor=%s", actor.id)

    return router
