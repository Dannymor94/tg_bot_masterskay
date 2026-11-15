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
from app.services.questions import QuestionService
from app.services.rent_requests import RentRequestService
from app.services.rich_compose import RichComposeService
from app.services.rich_menu import RichMenuService
from app.services.rich_views import BOOKING_PREFIX, screen_parts
from app.services.tasks import TaskService

logger = logging.getLogger(__name__)


def positive(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or len(value) > 19:
        raise ValueError("invalid UI reference")
    result = int(value)
    if result <= 0 or result > 2**63 - 1:
        raise ValueError("invalid UI reference")
    return result


def build_router(
    service: RichMenuService,
    tasks: TaskService,
    invites: InviteService,
    broadcasts: BroadcastService,
    questions: QuestionService,
    rent: RentRequestService,
    composer: RichComposeService,
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

    @router.message(F.reply_to_message, F.text, ~F.text.startswith("/"))
    async def compose_reply(message: Message, bot: Bot) -> None:
        actor = message.from_user
        if (
            actor is None
            or actor.is_bot
            or not private(message, actor.id)
            or message.reply_to_message is None
            or message.reply_to_message.from_user is None
            or message.reply_to_message.from_user.id != bot.id
        ):
            return
        try:
            state = await composer.claim(actor.id, message.reply_to_message.text or "", clock())
            if state is None:
                return
            text = message.text or ""
            action = state["action"]
            if action == "task":
                await tasks.manual(actor.id, str(message.message_id), text, clock())
                view = "tasks"
            elif action in ("question", "booking"):
                body = f"{BOOKING_PREFIX}{text}" if action == "booking" else text
                await questions.ask(actor.id, str(message.message_id), body, clock())
                view = "question" if action == "question" else "bookings"
            elif action == "rent":
                await rent.request(actor.id, str(message.message_id), text, clock())
                view = "rent"
            elif action == "announce":
                new_id = await broadcasts.create(
                    actor.id, str(message.message_id), state["segment"], text, clock()
                )
                view = f"broadcasts:detail:{new_id}"
            else:
                raise ValueError("invalid compose action")
            await service.show(
                actor.id,
                str(message.message_id),
                view,
                clock(),
                message_id=state["menu_message_id"],
            )
        except (AccessDenied, ValueError):
            await composer.feedback(actor.id, str(message.message_id), clock())
            return
        except Exception:
            logger.error("Rich compose reply failed actor=%s", actor.id)

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
            elif screen.startswith("compose:"):
                parts = screen.split(":")
                if len(parts) not in (2, 3):
                    raise ValueError("invalid compose action")
                await composer.begin(
                    actor.id,
                    query.id,
                    parts[1],
                    clock(),
                    segment=parts[2] if len(parts) == 3 else "",
                    menu_message_id=message.message_id,
                )
            elif screen.startswith("task:"):
                parts = screen.split(":")
                if len(parts) != 5 or parts[1] not in ("take", "done", "reply"):
                    raise ValueError("invalid task UI action")
                _, action, reference, status, page = parts
                # A task action must name both the task and a valid return view.
                task_id = positive(reference)
                view = f"tasks:{status}:{page}"
                screen_parts(view)
                if action == "take":
                    await tasks.take(actor.id, task_id, clock())
                elif action == "done":
                    await tasks.finish(actor.id, task_id, clock())
                else:
                    await tasks.reply_prompt(actor.id, task_id, query.id, clock())
                await service.show(actor.id, query.id, view, clock(), message_id=message.message_id)
            elif screen.startswith("bc:"):
                parts = screen.split(":")
                if len(parts) != 3 or parts[1] not in ("confirm", "cancel"):
                    raise ValueError("invalid broadcast UI action")
                broadcast_id = positive(parts[2])
                if parts[1] == "confirm":
                    await broadcasts.confirm(actor.id, broadcast_id, clock())
                else:
                    await broadcasts.cancel(actor.id, broadcast_id, clock())
                await service.show(
                    actor.id,
                    query.id,
                    f"broadcasts:detail:{broadcast_id}",
                    clock(),
                    message_id=message.message_id,
                )
            elif screen.startswith("team:revoke:"):
                member_id = positive(screen.removeprefix("team:revoke:"))
                await invites.revoke(actor.id, member_id, clock())
                await service.show(
                    actor.id, query.id, "team", clock(), message_id=message.message_id
                )
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
