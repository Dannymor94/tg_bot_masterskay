"""Private command and callback transport for native Telegram studio screens."""

import logging
from collections.abc import Callable
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot.callback_ack import acknowledge
from app.bot.tasks import private
from app.services.access import AccessDenied
from app.services.broadcasts import BroadcastService
from app.services.invites import InviteService
from app.services.rich_menu import RichMenuService
from app.services.tasks import TaskService

logger = logging.getLogger(__name__)


def build_router(
    service: RichMenuService,
    tasks: TaskService,
    invites: InviteService,
    broadcasts: BroadcastService,
    clock: Callable[[], datetime],
) -> Router:
    router = Router(name="rich_menu")

    @router.message(Command("menu"))
    async def menu(message: Message) -> None:
        actor = message.from_user
        if actor is None or actor.is_bot or not private(message, actor.id):
            return
        try:
            await service.show(actor.id, str(message.message_id), "home", clock())
        except (AccessDenied, ValueError):
            return
        except Exception:
            logger.error("Rich menu command failed actor=%s", actor.id)

    @router.callback_query(F.data.startswith("ui:"))
    async def button(query: CallbackQuery, bot: Bot) -> None:
        await acknowledge(query, bot)
        actor, message = query.from_user, query.message
        if actor.is_bot or not isinstance(message, Message) or not private(message, actor.id):
            return
        try:
            screen = (query.data or "")[3:]
            if screen == "queue":
                await tasks.queue(actor.id, query.id, clock())
            elif screen == "invite_teacher":
                await invites.issue(actor.id, "teacher", query.id, clock())
            elif screen == "invite_admin":
                await invites.issue(actor.id, "admin", query.id, clock())
            elif screen == "subscribe":
                await broadcasts.subscribe(actor.id, query.id, clock())
                await service.show(
                    actor.id, query.id, "home", clock(), message_id=message.message_id
                )
            elif screen == "unsubscribe":
                await broadcasts.unsubscribe(actor.id, clock())
                await service.show(
                    actor.id, query.id, "home", clock(), message_id=message.message_id
                )
            else:
                await service.show(
                    actor.id,
                    query.id,
                    screen,
                    clock(),
                    message_id=message.message_id,
                )
        except (AccessDenied, ValueError):
            return
        except Exception:
            logger.error("Rich menu callback failed actor=%s", actor.id)

    return router
