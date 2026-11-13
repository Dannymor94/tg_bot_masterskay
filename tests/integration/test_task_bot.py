import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import select

from app.bot.tasks import build_router
from app.db.engine import create_db
from app.db.models import outbox, tasks, users
from app.domain.tasks import rule
from app.jobs.outbox import TelegramSender, send_one
from app.services.tasks import TaskService, create_task
from app.settings import load_studio
from tests.integration.test_rbac_bot import update
from tests.integration.test_tasks import NOW, seed


def button(actor, action, task_id, seq, chat_id=None):
    return Update(
        update_id=seq,
        callback_query=CallbackQuery(
            id=str(seq),
            from_user=User(id=actor, is_bot=False, first_name="Synthetic"),
            chat_instance="synthetic",
            data=f"task:{action}:{task_id}",
            message=Message(
                message_id=seq,
                date=NOW,
                from_user=User(id=999, is_bot=True, first_name="Bot"),
                chat=Chat(id=actor if chat_id is None else chat_id, type="private"),
            ),
        ),
    )


def test_commands_buttons_and_identity(task_db):
    async def scenario():
        engine = create_db(task_db)
        cfg = load_studio()
        service = TaskService(engine, 1, cfg)
        dispatcher = Dispatcher()
        dispatcher.include_router(build_router(service, lambda: NOW))
        bot = Bot("123:synthetic")
        try:
            await seed(service)
            for request in [
                update(1, "/task private", chat_id=2),
                update(3, "/task denied"),
                update(1, "/take -1"),
                update(1, "/reply invalid"),
            ]:
                await dispatcher.feed_update(bot, request)
            async with engine.connect() as db:
                assert not (await db.execute(select(tasks))).all()
            await dispatcher.feed_update(bot, update(1, "/task synthetic", 10))
            await dispatcher.feed_update(bot, update(1, "/task synthetic", 10))
            await dispatcher.feed_update(bot, update(2, "/tasks", 11))
            async with engine.connect() as db:
                task_id = (await db.execute(select(tasks.c.id))).scalar_one()
            await dispatcher.feed_update(bot, button(1, "take", task_id, 12, chat_id=2))
            await dispatcher.feed_update(bot, button(3, "take", task_id, 13))
            async with engine.connect() as db:
                assert (await db.execute(select(tasks.c.state))).scalar_one() == "open"
            await dispatcher.feed_update(bot, button(2, "take", task_id, 14))
            await dispatcher.feed_update(bot, button(2, "done", task_id, 15))
            draft = rule("R3", "question", cfg, NOW, body="synthetic question", requester_id=3)
            assert draft is not None
            async with service.transaction(NOW) as db:
                question = await create_task(db, draft, cfg, NOW)
            await dispatcher.feed_update(bot, update(2, f"/take {question}", 16))
            await dispatcher.feed_update(bot, button(2, "reply", question, 17))
            await dispatcher.feed_update(bot, update(2, f"/reply {question} synthetic answer", 18))
            await service.revoke(1, 2, NOW)
            await dispatcher.feed_update(bot, update(2, "/task after revoke", 19))
            await dispatcher.feed_update(bot, button(2, "done", question, 20))
            async with engine.connect() as db:
                assert (await db.execute(select(tasks.c.state))).scalars().all() == ["done", "done"]
                replies = (
                    (await db.execute(select(outbox).where(outbox.c.kind == "task.reply")))
                    .mappings()
                    .all()
                )
                assert len(replies) == 1 and replies[0]["telegram_user_id"] == 3
        finally:
            await bot.session.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_sender_renders_current_state_and_cancels_downgraded_admin(task_db, monkeypatch):
    async def scenario():
        engine = create_db(task_db)
        cfg = load_studio()
        service = TaskService(engine, 1, cfg)
        bot = Bot("123:synthetic")
        send = AsyncMock()
        monkeypatch.setattr(bot, "send_message", send)
        try:
            await seed(service)
            task_id = await service.manual(1, "create", "synthetic body", NOW)
            sender = TelegramSender(bot)
            assert await send_one(engine, sender, cfg, NOW)
            kwargs = send.call_args.kwargs
            assert "synthetic body" in kwargs["text"]
            assert (
                kwargs["reply_markup"].inline_keyboard[0][0].callback_data == f"task:take:{task_id}"
            )
            async with engine.begin() as db:
                await db.execute(
                    users.update().where(users.c.telegram_user_id == 2).values(role="client")
                )
            assert await send_one(engine, sender, cfg, NOW + timedelta(seconds=1))
            assert send.await_count == 1
            async with engine.connect() as db:
                assert (
                    await db.execute(select(outbox.c.state).where(outbox.c.telegram_user_id == 2))
                ).scalar_one() == "cancelled"
            await service.take(1, task_id, NOW)
            assert await send_one(engine, sender, cfg, NOW + timedelta(seconds=2))
            assert kwargs != send.call_args.kwargs
            assert (
                send.call_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
                == f"task:done:{task_id}"
            )
            async with engine.connect() as db:
                assert all(
                    "synthetic body" not in repr(r)
                    for r in (await db.execute(select(outbox))).all()
                )
        finally:
            await bot.session.close()
            await engine.dispose()

    asyncio.run(scenario())
