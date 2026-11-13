"""Thin authenticated command transport; all permission decisions are services."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message
from pydantic import SecretStr

from app.bot.client import build_router as build_client_router
from app.bot.tasks import build_router as task_router
from app.services.access import AccessDenied
from app.services.client_onboarding import ClientService
from app.services.invites import InviteService
from app.services.tasks import TaskService

logger = logging.getLogger(__name__)


def build_router(service: InviteService, clock: Callable[[], datetime]) -> Router:
    router = Router(name="rbac")

    @router.message(Command("access", "invite", "accept", "revoke"))
    @router.message(CommandStart(deep_link=True, magic=F.args.startswith("invite_")))
    async def command(message: Message, command: CommandObject) -> None:
        actor = message.from_user
        if (
            actor is None
            or actor.is_bot
            or message.chat.type != "private"
            or message.chat.id != actor.id
        ):
            return
        args = (command.args or "").split()
        request_id = str(message.message_id)
        try:
            if command.command == "access" and not args:
                await service.menu(actor.id, request_id, clock())
            elif command.command == "invite" and len(args) == 1:
                await service.issue(actor.id, args[0], request_id, clock())
            elif command.command == "accept" and len(args) == 1:
                await service.redeem(actor.id, SecretStr(args[0]), clock())
            elif command.command == "start" and len(args) == 1 and args[0].startswith("invite_"):
                await service.redeem(actor.id, SecretStr(args[0].removeprefix("invite_")), clock())
            elif (
                command.command == "revoke"
                and len(args) == 1
                and args[0].isascii()
                and args[0].isdecimal()
            ):
                await service.revoke(actor.id, int(args[0]), clock())
        except AccessDenied:
            # Never echo user input (especially bearer tokens) into an error message.
            return
        except Exception:
            # Framework error logging includes the original Update. Do not expose it.
            logger.error("RBAC command failed actor=%s", actor.id)

    return router


async def poll(bot: Bot, service: InviteService, stopped: asyncio.Event) -> None:
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(service, lambda: datetime.now(UTC)))
    dispatcher.include_router(
        task_router(
            TaskService(service.engine, service.owner_id, service.cfg), lambda: datetime.now(UTC)
        )
    )
    dispatcher.include_router(
        build_client_router(
            ClientService(service.engine, service.owner_id, service.cfg), lambda: datetime.now(UTC)
        )
    )
    polling = asyncio.create_task(
        dispatcher.start_polling(
            bot,
            handle_signals=False,
            close_bot_session=False,
            allowed_updates=["message", "callback_query"],
            handle_as_tasks=False,
        )
    )
    stop = asyncio.create_task(stopped.wait())
    try:
        done, _ = await asyncio.wait([polling, stop], return_when=asyncio.FIRST_COMPLETED)
        if polling in done:
            await polling
            stopped.set()
    finally:
        polling.cancel()
        stop.cancel()
        await asyncio.gather(polling, stop, return_exceptions=True)
