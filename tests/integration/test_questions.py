"""T-304: synthetic end-to-end R3 question and reply; no external network."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update, User
from sqlalchemy import select

from app.bot.questions import build_router
from app.db.engine import create_db
from app.db.models import audit_log, outbox, tasks, users
from app.services.access import AccessDenied
from app.services.questions import QuestionService
from app.services.tasks import TaskService
from app.settings import load_studio

NOW = datetime(2026, 1, 1, 9, tzinfo=UTC)


async def seeded(engine):
    question = QuestionService(engine, 1, load_studio())
    await question.bootstrap(NOW)
    async with engine.begin() as db:
        await db.execute(
            users.insert(),
            [
                {
                    "telegram_user_id": 2,
                    "role": "client",
                    "consent_at": NOW,
                    "yclients_client_id": 52,
                    "created_at": NOW,
                },
                {
                    "telegram_user_id": 3,
                    "role": "teacher",
                    "teacher_kind": "staff",
                    "yclients_staff_id": 53,
                    "created_at": NOW,
                },
                {"telegram_user_id": 4, "role": "admin", "created_at": NOW},
                {"telegram_user_id": 5, "role": "admin", "created_at": NOW},
                {"telegram_user_id": 6, "role": "client", "created_at": NOW},
                {
                    "telegram_user_id": 7,
                    "role": "teacher",
                    "teacher_kind": "staff",
                    "yclients_staff_id": 57,
                    "access_revoked": True,
                    "created_at": NOW,
                },
                {
                    "telegram_user_id": 8,
                    "role": "client",
                    "yclients_client_id": 58,
                    "blocked_at": NOW,
                    "created_at": NOW,
                },
            ],
        )
    return question, TaskService(engine, 1, load_studio())


def test_question_through_admin_reply_and_exactly_once(task_db):
    async def scenario():
        engine = create_db(task_db)
        try:
            service, admin = await seeded(engine)
            task_id = await service.ask(2, "msg-10", "Synthetic private question", NOW)
            assert await service.ask(
                2, "msg-10", "Synthetic private question", NOW + timedelta(seconds=25)
            ) == task_id
            with pytest.raises(ValueError):
                await service.ask(2, "msg-10", "altered question", NOW)
            async with engine.connect() as db:
                rows = (await db.execute(select(tasks))).mappings().all()
                effects = (await db.execute(select(outbox))).mappings().all()
                audit = (await db.execute(select(audit_log))).mappings().all()
            assert len(rows) == 1
            assert rows[0]["code"] == "R3"
            assert rows[0]["requester_id"] == 2
            assert rows[0]["body"] == "Synthetic private question"
            assert len([r for r in effects if r["kind"] == "question.ack"]) == 1
            assert len([r for r in effects if r["kind"] == "task.card"]) == 3
            assert all("Synthetic private question" not in repr(r["payload_min"]) for r in effects)
            assert [r["action"] for r in audit].count("questions.created") == 1

            assert await admin.take(4, task_id, NOW)
            with pytest.raises(AccessDenied):
                await admin.finish(5, task_id, NOW, reply="Synthetic answer")
            assert await admin.finish(4, task_id, NOW, reply="Synthetic answer")
            assert not await admin.finish(4, task_id, NOW, reply="Synthetic answer")
            async with engine.connect() as db:
                reply_rows = (
                    (await db.execute(select(outbox).where(outbox.c.kind == "task.reply")))
                    .mappings()
                    .all()
                )
                updated = (await db.execute(select(tasks))).mappings().one()
            assert len(reply_rows) == 1
            assert reply_rows[0]["telegram_user_id"] == 2
            assert "Synthetic answer" in reply_rows[0]["payload_min"]["text"]
            assert updated["state"] == "done"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_teacher_and_permissions_fail_closed(task_db):
    async def scenario():
        engine = create_db(task_db)
        try:
            service, _ = await seeded(engine)
            assert await service.ask(3, "teacher-1", "Question from teacher", NOW) > 0
            for actor in (1, 4, 6, 7, 8, 999):
                with pytest.raises(AccessDenied):
                    await service.ask(actor, f"rejected-{actor}", "Synthetic", NOW)
            for invalid in ("", "  ", "x" * 1025):
                with pytest.raises(ValueError):
                    await service.ask(2, "invalid", invalid, NOW)
            async with engine.connect() as db:
                all_tasks = (await db.execute(select(tasks))).mappings().all()
            assert len(all_tasks) == 1
            assert all_tasks[0]["requester_id"] == 3
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_queue_and_ack_roll_back_together(task_db, monkeypatch):
    from app.services import questions

    async def scenario():
        engine = create_db(task_db)
        try:
            service, _ = await seeded(engine)
            original = questions.enqueue

            async def fail(*args, **kwargs):
                raise RuntimeError("synthetic enqueue failure")

            monkeypatch.setattr(questions, "enqueue", fail)
            with pytest.raises(RuntimeError):
                await service.ask(2, "rollback", "Question", NOW)
            async with engine.connect() as db:
                assert (await db.execute(select(tasks))).all() == []
                assert (await db.execute(select(outbox))).all() == []
            monkeypatch.setattr(questions, "enqueue", original)
            assert await service.ask(2, "rollback", "Question", NOW) > 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def make_update(
    actor: int,
    text: str,
    *,
    chat: int | None = None,
    kind: str = "private",
    is_bot: bool = False,
) -> Update:
    return Update(
        update_id=10,
        message=Message(
            message_id=10,
            date=NOW,
            chat=Chat(id=actor if chat is None else chat, type=kind),
            from_user=User(id=actor, is_bot=is_bot, first_name="Synthetic"),
            text=text,
        ),
    )


def test_router_trusts_telegram_identity_and_hides_private_error(caplog):
    async def scenario():
        service = AsyncMock(spec=QuestionService)
        bot = Bot("123:synthetic")
        dispatcher = Dispatcher()
        dispatcher.include_router(build_router(service, lambda: NOW))
        try:
            for update in (
                make_update(2, "/ask forged", chat=3),
                make_update(2, "/ask group", chat=-2, kind="group"),
                make_update(2, "/ask bot", is_bot=True),
                make_update(2, "/start"),
            ):
                await dispatcher.feed_update(bot, update)
            service.ask.assert_not_called()
            await dispatcher.feed_update(bot, make_update(2, "/ask Synthetic question"))
            service.ask.assert_awaited_once_with(2, "10", "Synthetic question", NOW)
            service.ask.side_effect = RuntimeError("PRIVATE_MARKER")
            await dispatcher.feed_update(bot, make_update(2, "/ask PRIVATE_MARKER"))
            assert "PRIVATE_MARKER" not in caplog.text
        finally:
            await bot.session.close()

    asyncio.run(scenario())
