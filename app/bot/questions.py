"""Thin private-chat transport for R3 questions; never logs message text."""

import logging
from collections.abc import Callable
from datetime import datetime

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.services.access import AccessDenied
from app.services.questions import QuestionService

logger = logging.getLogger(__name__)


def build_router(service: QuestionService, clock: Callable[[], datetime]) -> Router:
    router = Router(name="questions")

    @router.message(Command("ask"))
    async def ask(message: Message, command: CommandObject) -> None:
        sender = message.from_user
        if (
            sender is None
            or sender.is_bot
            or message.chat.type != "private"
            or message.chat.id != sender.id
        ):
            return
        try:
            await service.ask(
                sender.id, str(message.message_id), command.args or "", clock()
            )
        except (AccessDenied, ValueError):
            return
        except Exception:
            # Avoid framework default logging of raw Telegram Update / question.
            logger.error("Question command failed actor=%s", sender.id)

    return router
