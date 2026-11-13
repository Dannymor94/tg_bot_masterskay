"""Private Telegram identity is the sole source of task actor IDs."""

import logging
from collections.abc import Callable
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from app.services.access import AccessDenied
from app.services.tasks import TaskService

logger = logging.getLogger(__name__)


def private(message: Message, actor_id: int) -> bool:
    return message.chat.type == "private" and message.chat.id == actor_id


def task_number(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or int(value) <= 0:
        raise ValueError("invalid task number")
    return int(value)


def build_router(service: TaskService, clock: Callable[[], datetime]) -> Router:
    router = Router(name="tasks")

    @router.message(Command("task", "tasks", "take", "done", "reply"))
    async def command(message: Message, command: CommandObject) -> None:
        actor = message.from_user
        if actor is None or actor.is_bot or not private(message, actor.id):
            return
        text = (command.args or "").strip()
        try:
            now = clock()
            if command.command == "task":
                await service.manual(actor.id, str(message.message_id), text, now)
            elif command.command == "tasks" and not text:
                await service.queue(actor.id, str(message.message_id), now)
            elif command.command in ("take", "done"):
                task_id = task_number(text)
                if command.command == "take":
                    await service.take(actor.id, task_id, now)
                else:
                    await service.finish(actor.id, task_id, now)
            elif command.command == "reply":
                number, reply = text.split(maxsplit=1)
                await service.finish(actor.id, task_number(number), now, reply=reply)
        except (AccessDenied, ValueError):
            return
        except Exception:
            # Do not let framework logs include task text / Telegram Update.
            logger.error("Task command failed actor=%s", actor.id)

    @router.callback_query(F.data.startswith("task:"))
    async def button(query: CallbackQuery) -> None:
        actor, message = query.from_user, query.message
        if actor.is_bot or not isinstance(message, Message) or not private(message, actor.id):
            return
        try:
            _, action, number = (query.data or "").split(":")
            task_id, now = task_number(number), clock()
            if action == "take":
                await service.take(actor.id, task_id, now)
            elif action == "done":
                await service.finish(actor.id, task_id, now)
            elif action == "reply":
                await service.reply_prompt(actor.id, task_id, query.id, now)
        except (AccessDenied, ValueError):
            return
        except Exception:
            logger.error("Task callback failed actor=%s", actor.id)

    return router
