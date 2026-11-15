"""Real button → ForceReply → service flows, with synthetic Telegram updates."""

import asyncio
import json
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, ForceReply, Message, Update, User
from pydantic import SecretStr
from sqlalchemy import select

from app.bot.rich_menu import build_router
from app.db.engine import create_db
from app.db.models import broadcasts, kv, outbox, tasks, users
from app.domain.models import User as AppUser
from app.jobs.outbox import TelegramSender, send_one
from app.services.access import AccessDenied
from app.services.broadcasts import BroadcastService
from app.services.invites import InviteService, InviteTokens
from app.services.questions import QuestionService
from app.services.rent_requests import RentRequestService
from app.services.rich_compose import RichComposeService
from app.services.rich_menu import RichMenuService
from app.services.rich_views import view_data
from app.services.tasks import TaskService
from app.settings import load_studio
from app.ui.rich_menu import render
from tests.integration.test_rbac_bot import NOW
from tests.integration.test_rich_menu import callback, populate


def reply(actor: int, text: str, prompt: str, update_id: int, *, prompt_from: int = 123):
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=NOW,
            from_user=User(id=actor, is_bot=False, first_name="Synthetic"),
            chat=Chat(id=actor, type="private"),
            text=text,
            reply_to_message=Message(
                message_id=update_id - 1,
                date=NOW,
                from_user=User(id=prompt_from, is_bot=True, first_name="Bot"),
                chat=Chat(id=actor, type="private"),
                text=prompt,
            ),
        ),
    )


async def label(engine, actor: int) -> str:
    async with engine.connect() as db:
        state = (
            await db.execute(select(kv.c.value).where(kv.c.key == f"ui:compose:{actor}"))
        ).scalar_one()
    return json.loads(state)["label"]


def test_compose_task_replay_and_real_force_reply_at_night(task_db, monkeypatch):
    async def scenario():
        engine = create_db(task_db)
        cfg = load_studio()
        ui = RichMenuService(engine, 1, cfg)
        compose = RichComposeService(engine, 1, cfg)
        bot = Bot("123:synthetic")
        send = AsyncMock()
        monkeypatch.setattr(bot, "send_message", send)
        monkeypatch.setattr(bot, "answer_callback_query", AsyncMock())
        dispatcher = Dispatcher()
        dispatcher.include_router(
            build_router(
                ui,
                TaskService(engine, 1, cfg),
                InviteService(engine, 1, cfg, InviteTokens(SecretStr("synthetic"))),
                BroadcastService(engine, 1, cfg),
                QuestionService(engine, 1, cfg),
                RentRequestService(engine, 1, cfg),
                compose,
                lambda: NOW,
            )
        )
        try:
            await populate(ui)
            await dispatcher.feed_update(bot, callback(1, "compose:task", 110))
            prompt = await label(engine, 1)
            async with engine.connect() as db:
                rows = (await db.execute(select(outbox))).mappings().all()
            assert len(rows) == 1 and rows[0]["kind"] == "ui.compose.prompt"
            assert "secret" not in rows[0]["payload_min"]
            # Prompt is sent even when quiet-hours defer normal task updates.
            assert await send_one(engine, TelegramSender(bot), cfg, NOW.replace(hour=19))
            assert isinstance(send.call_args.kwargs["reply_markup"], ForceReply)
            assert send.call_args.kwargs["chat_id"] == 1

            await dispatcher.feed_update(bot, reply(1, "Fake task", "wrong prompt", 111))
            await dispatcher.feed_update(bot, reply(1, "Fake task", prompt, 112, prompt_from=999))
            async with engine.connect() as db:
                assert (await db.execute(select(tasks.c.id))).scalars().all() == []
            await dispatcher.feed_update(bot, reply(1, "Рабочая задача <важно>", prompt, 113))
            await dispatcher.feed_update(bot, reply(1, "Повтор", prompt, 114))
            await dispatcher.feed_update(bot, callback(1, "compose:task", 115))
            new_prompt = await label(engine, 1)
            await dispatcher.feed_update(bot, reply(1, "   ", new_prompt, 116))
            async with engine.connect() as db:
                result = (await db.execute(select(tasks))).mappings().all()
                assert len(result) == 1
                assert result[0]["code"] == "R8"
                assert result[0]["body"] == "Рабочая задача <важно>"
                assert (
                    await db.execute(select(kv.c.value).where(kv.c.key == "ui:compose:1"))
                ).scalar_one_or_none() is None
                errors = (
                    (await db.execute(select(outbox).where(outbox.c.kind == "ui.compose.feedback")))
                    .mappings()
                    .all()
                )
                assert len(errors) == 1
                assert "Рабочая задача" not in errors[0]["payload_min"]
        finally:
            await bot.session.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_client_booking_and_question_teacher_rent_and_announcement_draft(task_db, monkeypatch):
    async def scenario():
        engine = create_db(task_db)
        cfg = load_studio()
        ui = RichMenuService(engine, 1, cfg)
        bot = Bot("123:synthetic")
        monkeypatch.setattr(bot, "answer_callback_query", AsyncMock())
        dispatcher = Dispatcher()
        dispatcher.include_router(
            build_router(
                ui,
                TaskService(engine, 1, cfg),
                InviteService(engine, 1, cfg, InviteTokens(SecretStr("synthetic"))),
                BroadcastService(engine, 1, cfg),
                QuestionService(engine, 1, cfg),
                RentRequestService(engine, 1, cfg),
                RichComposeService(engine, 1, cfg),
                lambda: NOW,
            )
        )
        try:
            await populate(ui)
            for actor, action, msg, event in (
                (3, "question", "Не могу прийти", 220),
                (3, "booking", "В среду на йогу 18:00", 230),
                (4, "rent", "Нужен малый зал в четверг", 240),
                (1, "announce:clients_all", "Новая практика", 250),
            ):
                await dispatcher.feed_update(bot, callback(actor, "compose:" + action, event))
                prompt = await label(engine, actor)
                await dispatcher.feed_update(bot, reply(actor, msg, prompt, event + 1))
            async with engine.connect() as db:
                actual = (await db.execute(select(tasks))).mappings().all()
                drafts = (await db.execute(select(broadcasts))).mappings().all()
                pending_news = (
                    (await db.execute(select(outbox).where(outbox.c.kind == "broadcast.news")))
                    .mappings()
                    .all()
                )
            assert sorted(t["code"] for t in actual) == ["R1", "R3", "R3"]
            assert any(t["body"] == "Не могу прийти" and t["requester_id"] == 3 for t in actual)
            assert any(t["body"].startswith("Запрос записи на занятие: ") for t in actual)
            assert any(t["body"] == "Нужен малый зал в четверг" for t in actual)
            assert len(drafts) == 1 and drafts[0]["state"] == "draft"
            assert drafts[0]["text"] == "Новая практика"
            assert pending_news == []  # Sending requires a separate confirmation.
            async with engine.connect() as db:
                client_bookings = await view_data(
                    db, AppUser(telegram_user_id=3, role="client"), "bookings"
                )
                other_bookings = await view_data(
                    db, AppUser(telegram_user_id=5, role="client"), "bookings"
                )
            assert len(client_bookings["booking_requests"]) == 1
            assert "В среду" in (
                render(AppUser(telegram_user_id=3, role="client"), "bookings", client_bookings).html
                or ""
            )
            assert other_bookings["booking_requests"] == []
        finally:
            await bot.session.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_compose_permissions_expiry_and_revocation_are_fail_closed(task_db):
    async def scenario():
        engine = create_db(task_db)
        cfg = load_studio()
        ui = RichMenuService(engine, 1, cfg)
        service = RichComposeService(engine, 1, cfg)
        try:
            await populate(ui)
            for actor, action in ((3, "task"), (2, "rent"), (4, "announce"), (5, "question")):
                with pytest.raises(AccessDenied):
                    await service.begin(
                        actor,
                        "not-allowed",
                        action,
                        NOW,
                        menu_message_id=80,
                        segment="clients_all" if action == "announce" else "",
                    )
            with pytest.raises(ValueError):
                await service.begin(1, "bad", "announce", NOW, segment="bad", menu_message_id=80)
            await service.begin(3, "valid", "question", NOW, menu_message_id=80)
            prompt = await label(engine, 3)
            assert await service.claim(3, prompt, NOW + timedelta(minutes=31)) is None
            assert await service.claim(3, prompt, NOW) is None
            async with engine.connect() as db:
                feedback = (
                    (await db.execute(select(outbox).where(outbox.c.kind == "ui.compose.feedback")))
                    .mappings()
                    .all()
                )
                assert len(feedback) == 1 and "истекло" in feedback[0]["payload_min"]["text"]
            await service.begin(3, "new", "question", NOW, menu_message_id=80)
            fresh = await label(engine, 3)
            assert await service.claim(3, fresh, NOW) is not None
            assert await service.claim(3, fresh, NOW) is None
            # Authorization rechecks role at the business operation; stale grant
            # never permits revoked access, even if a prompt was issued earlier.
            await service.begin(4, "teacher-prompt", "rent", NOW, menu_message_id=80)
            async with engine.begin() as db:
                await db.execute(
                    users.update().where(users.c.telegram_user_id == 4).values(access_revoked=True)
                )
            with pytest.raises(AccessDenied):
                await RentRequestService(engine, 1, cfg).request(4, "revoked", "Зал", NOW)
        finally:
            await engine.dispose()

    asyncio.run(scenario())
