import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from pydantic import SecretStr
from sqlalchemy import select

from app.bot.rich_menu import build_router
from app.db.engine import create_db
from app.db.models import outbox, users
from app.jobs.outbox import TelegramSender, send_one
from app.services.access import AccessDenied
from app.services.broadcasts import BroadcastService
from app.services.invites import InviteService, InviteTokens
from app.services.rich_menu import RichMenuService
from app.services.tasks import TaskService
from app.settings import load_studio
from tests.integration.test_rbac_bot import NOW, update


def callback(actor_id: int, page: str, event: int, *, chat_id: int | None = None) -> Update:
    return Update(
        update_id=event,
        callback_query=CallbackQuery(
            id=str(event),
            from_user=User(id=actor_id, is_bot=False, first_name="Synthetic"),
            chat_instance="synthetic",
            data=f"ui:{page}",
            message=Message(
                message_id=80,
                date=NOW,
                from_user=User(id=888, is_bot=True, first_name="Bot"),
                chat=Chat(id=actor_id if chat_id is None else chat_id, type="private"),
            ),
        ),
    )


async def populate(service: RichMenuService) -> None:
    await service.bootstrap(NOW)
    async with service.engine.begin() as db:
        await db.execute(users.insert().values(telegram_user_id=2, role="admin", created_at=NOW))
        await db.execute(
            users.insert().values(
                telegram_user_id=3,
                role="client",
                yclients_client_id=23,
                consent_at=NOW,
                created_at=NOW,
            )
        )
        await db.execute(
            users.insert().values(
                telegram_user_id=4,
                role="teacher",
                teacher_kind="staff",
                yclients_staff_id=44,
                created_at=NOW,
            )
        )
        await db.execute(users.insert().values(telegram_user_id=5, role="client", created_at=NOW))


def test_menu_service_deduplicates_and_fails_closed_on_roles(task_db):
    async def scenario():
        engine = create_db(task_db)
        service = RichMenuService(engine, 1, load_studio())
        try:
            await populate(service)
            await service.show(1, "first", "home", NOW)
            await service.show(1, "first", "home", NOW)
            await service.show(2, "tasks", "tasks", NOW)
            await service.show(3, "client", "schedule", NOW)
            await service.show(4, "teacher", "rent", NOW)
            with pytest.raises(AccessDenied):
                await service.show(3, "forged", "tasks", NOW)
            with pytest.raises(AccessDenied):
                await service.show(2, "forged", "reports", NOW)
            with pytest.raises(AccessDenied):
                await service.show(5, "unlinked", "home", NOW)
            with pytest.raises(ValueError):
                await service.show(1, "invalid", "home", NOW, message_id=-1)
            async with engine.connect() as db:
                rows = (await db.execute(select(outbox))).mappings().all()
            assert len(rows) == 4
            assert all("role" not in row["payload_min"] for row in rows)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_rich_sender_delivery_navigation_and_revoked_revalidation(task_db, monkeypatch):
    night = NOW.replace(hour=19)

    async def scenario():
        engine = create_db(task_db)
        service = RichMenuService(engine, 1, load_studio())
        bot = Bot("123:synthetic")
        send_rich = AsyncMock()
        edit_rich = AsyncMock()
        monkeypatch.setattr(bot, "send_rich_message", send_rich)
        monkeypatch.setattr(bot, "edit_message_text", edit_rich)
        try:
            await populate(service)
            await service.show(1, "night", "home", night)
            assert await send_one(engine, TelegramSender(bot), service.cfg, night)
            assert send_rich.await_count == 1
            assert send_rich.call_args.kwargs["chat_id"] == 1
            assert "Владелец" in send_rich.call_args.kwargs["rich_message"].html

            await service.show(2, "edit", "tasks", night, message_id=80)
            assert await send_one(
                engine, TelegramSender(bot), service.cfg, night + timedelta(seconds=1)
            )
            assert edit_rich.await_count == 1
            assert edit_rich.call_args.kwargs["message_id"] == 80
            assert 'data="ui:queue"' in edit_rich.call_args.kwargs["rich_message"].html

            await service.show(4, "teacher", "home", night)
            async with engine.begin() as db:
                await db.execute(
                    users.update().where(users.c.telegram_user_id == 4).values(access_revoked=True)
                )
            assert await send_one(
                engine, TelegramSender(bot), service.cfg, night + timedelta(seconds=2)
            )
            assert send_rich.await_count == 1
            async with engine.connect() as db:
                states = (
                    (await db.execute(select(outbox.c.state).where(outbox.c.telegram_user_id == 4)))
                    .scalars()
                    .all()
                )
            assert states == ["blocked"]
        finally:
            await bot.session.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_telegram_handler_ack_and_real_queue_callback(task_db, monkeypatch):
    async def scenario():
        engine = create_db(task_db)
        cfg = load_studio()
        service = RichMenuService(engine, 1, cfg)
        tasks = TaskService(engine, 1, cfg)
        dispatcher = Dispatcher()
        dispatcher.include_router(
            build_router(
                service,
                tasks,
                InviteService(engine, 1, cfg, InviteTokens(SecretStr("synthetic"))),
                BroadcastService(engine, 1, cfg),
                lambda: NOW,
            )
        )
        bot = Bot("123:synthetic")
        acknowledgement = AsyncMock()
        monkeypatch.setattr(bot, "answer_callback_query", acknowledgement)
        try:
            await populate(service)
            await dispatcher.feed_update(bot, update(2, "/menu", 10, chat_id=3))
            await dispatcher.feed_update(bot, update(2, "/menu", 11))
            await dispatcher.feed_update(bot, callback(3, "tasks", 12))
            await dispatcher.feed_update(bot, callback(2, "queue", 13))
            await dispatcher.feed_update(bot, callback(2, "reports", 14))
            await dispatcher.feed_update(bot, callback(2, "tasks", 15, chat_id=3))
            await dispatcher.feed_update(bot, callback(2, "invite_teacher", 16))
            await dispatcher.feed_update(bot, callback(3, "invite_admin", 17))
            await dispatcher.feed_update(bot, callback(3, "subscribe", 18))
            await dispatcher.feed_update(bot, callback(3, "unsubscribe", 19))
            async with engine.connect() as db:
                records = (await db.execute(select(outbox))).mappings().all()
                opted = (
                    await db.execute(
                        select(users.c.news_opt_in).where(users.c.telegram_user_id == 3)
                    )
                ).scalar_one()
            assert [r["kind"] for r in records] == [
                "ui.rich.new",
                "task.queue_empty",
                "rbac.invite",
                "broadcast.subscribed",
                "ui.rich.edit",
                "broadcast.unsubscribed",
                "ui.rich.edit",
            ]
            assert opted is False
            assert acknowledgement.await_count == 8
        finally:
            await bot.session.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_delivery_rejects_stale_admin_screen_after_role_change(task_db):
    async def scenario():
        engine = create_db(task_db)
        service = RichMenuService(engine, 1, load_studio())
        sent = []

        async def capture(message):
            sent.append(message)

        try:
            await populate(service)
            await service.show(2, "screen-before-demotion", "tasks", NOW)
            async with engine.begin() as db:
                await db.execute(
                    users.update()
                    .where(users.c.telegram_user_id == 2)
                    .values(role="client", yclients_client_id=22, consent_at=NOW)
                )
            assert await send_one(engine, capture, service.cfg, NOW)
            assert sent == []
            async with engine.connect() as db:
                state = (
                    await db.execute(select(outbox.c.state).where(outbox.c.telegram_user_id == 2))
                ).scalar_one()
            assert state == "cancelled"
        finally:
            await engine.dispose()

    asyncio.run(scenario())
