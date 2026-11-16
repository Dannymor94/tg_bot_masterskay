"""Synthetic end-to-end journeys for each role's native Telegram screens."""

import asyncio
from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher
from pydantic import SecretStr
from sqlalchemy import select

from app.bot.rich_menu import build_router
from app.db.engine import create_db
from app.db.models import broadcasts, outbox, tasks, users
from app.domain.models import User
from app.domain.tasks import rule
from app.jobs.outbox import TelegramSender, send_one
from app.services.broadcasts import BroadcastService
from app.services.invites import InviteService, InviteTokens
from app.services.questions import QuestionService
from app.services.rent_requests import RentRequestService
from app.services.rich_compose import RichComposeService
from app.services.rich_menu import RichMenuService
from app.services.rich_views import view_data
from app.services.tasks import TaskService, create_task
from app.settings import load_studio
from app.ui.rich_menu import can_open, render
from tests.integration.test_rbac_bot import NOW
from tests.integration.test_rich_menu import callback, populate


def owner() -> User:
    return User(telegram_user_id=1, role="admin", is_owner=True)


async def actors(db):
    return (await db.execute(select(users).where(users.c.telegram_user_id == 3))).mappings().one()


def test_owner_admin_tasks_are_live_paginated_and_html_escaped(task_db, monkeypatch):
    async def scenario():
        engine = create_db(task_db)
        cfg = load_studio()
        ui = RichMenuService(engine, 1, cfg)
        work = TaskService(engine, 1, cfg)
        bot = Bot("123:synthetic")
        edit = AsyncMock()
        monkeypatch.setattr(bot, "edit_message_text", edit)
        try:
            await populate(ui)
            for i in range(7):
                await work.manual(1, f"unique-{i}", f"Private <b>user{i}</b>", NOW)
            async with engine.connect() as db:
                first = await view_data(db, owner(), "tasks:active:0")
                second = await view_data(db, owner(), "tasks:active:1")
            assert first["count"] == 7 and len(first["tasks"]) == 4
            assert second["count"] == 7 and len(second["tasks"]) == 3
            html = render(owner(), "tasks:active:0", first).html or ""
            assert "&lt;b&gt;" in html and "<b>user" not in html
            assert 'data="ui:tasks:active:1"' in html
            newest = first["tasks"][0]["id"]
            assert f'data="ui:task:take:{newest}:active:0"' in html
            assert 'data="ui:tasks:done:0"' in html

            # At night, queued push task cards remain deferred; the explicit UI
            # edit renders real current tasks immediately from the database.
            night = NOW.replace(hour=19)
            await ui.show(1, "rich-tasks", "tasks:active:0", night, message_id=80)
            assert await send_one(engine, TelegramSender(bot), cfg, night)
            assert edit.await_count == 1
            assert f"#{newest}" in edit.call_args.kwargs["rich_message"].html
        finally:
            await bot.session.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_task_take_done_buttons_are_live_and_forged_client_is_denied(task_db, monkeypatch):
    async def scenario():
        engine = create_db(task_db)
        cfg = load_studio()
        ui = RichMenuService(engine, 1, cfg)
        work = TaskService(engine, 1, cfg)
        dispatcher = Dispatcher()
        dispatcher.include_router(
            build_router(
                ui,
                work,
                InviteService(engine, 1, cfg, InviteTokens(SecretStr("synthetic"))),
                BroadcastService(engine, 1, cfg),
                QuestionService(engine, 1, cfg),
                RentRequestService(engine, 1, cfg),
                RichComposeService(engine, 1, cfg),
                lambda: NOW,
            )
        )
        bot = Bot("123:synthetic")
        ack = AsyncMock()
        monkeypatch.setattr(bot, "answer_callback_query", ack)
        try:
            await populate(ui)
            identifier = await work.manual(1, "new", "Synthetic task", NOW)
            await dispatcher.feed_update(bot, callback(3, f"task:take:{identifier}:active:0", 101))
            async with engine.connect() as db:
                assert (await db.execute(select(tasks.c.state))).scalar_one() == "open"
            await dispatcher.feed_update(bot, callback(2, f"task:take:{identifier}:active:0", 102))
            async with engine.connect() as db:
                row = (await db.execute(select(tasks.c.state, tasks.c.taken_by))).one()
                assert row == ("taken", 2)
                data = await view_data(db, User(telegram_user_id=2, role="admin"), "tasks:active:0")
                rich = (
                    render(User(telegram_user_id=2, role="admin"), "tasks:active:0", data).html
                    or ""
                )
            assert f'data="ui:task:done:{identifier}:active:0"' in rich
            await dispatcher.feed_update(bot, callback(2, f"task:done:{identifier}:active:0", 103))
            async with engine.connect() as db:
                assert (await db.execute(select(tasks.c.state))).scalar_one() == "done"
            assert ack.await_count == 3
        finally:
            await bot.session.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_client_and_teacher_only_see_own_questions(task_db):
    async def scenario():
        engine = create_db(task_db)
        cfg = load_studio()
        service = RichMenuService(engine, 1, cfg)
        questions = QuestionService(engine, 1, cfg)
        try:
            await populate(service)
            first = await questions.ask(3, "question-3", "Private client question", NOW)
            second = await questions.ask(4, "question-4", "Private teacher question", NOW)
            async with engine.connect() as db:
                mine = await view_data(db, User(telegram_user_id=3, role="client"), "question")
                theirs = await view_data(db, User(telegram_user_id=4, role="teacher"), "question")
            assert [q["id"] for q in mine["questions"]] == [first]
            assert [q["id"] for q in theirs["questions"]] == [second]
            assert f"Вопрос #{second} " not in (
                render(User(telegram_user_id=3, role="client"), "question", mine).html or ""
            )
            assert not can_open(User(telegram_user_id=3, role="client"), "tasks")
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_broadcast_review_confirm_and_noncreator_cannot_send(task_db, monkeypatch):
    async def scenario():
        engine = create_db(task_db)
        cfg = load_studio()
        ui = RichMenuService(engine, 1, cfg)
        service = BroadcastService(engine, 1, cfg)
        dispatcher = Dispatcher()
        dispatcher.include_router(
            build_router(
                ui,
                TaskService(engine, 1, cfg),
                InviteService(engine, 1, cfg, InviteTokens(SecretStr("synthetic"))),
                service,
                QuestionService(engine, 1, cfg),
                RentRequestService(engine, 1, cfg),
                RichComposeService(engine, 1, cfg),
                lambda: NOW,
            )
        )
        bot = Bot("123:synthetic")
        monkeypatch.setattr(bot, "answer_callback_query", AsyncMock())
        try:
            await populate(ui)
            await service.subscribe(3, "optin", NOW)
            identifier = await service.create(1, "draft", "clients_all", "News <private>", NOW)
            async with engine.connect() as db:
                preview = await view_data(db, owner(), f"broadcasts:detail:{identifier}")
            html = render(owner(), f"broadcasts:detail:{identifier}", preview).html or ""
            assert "&lt;private&gt;" in html and "<private>" not in html
            assert f'data="ui:bc:confirm:{identifier}"' in html
            await dispatcher.feed_update(bot, callback(2, f"bc:confirm:{identifier}", 201))
            async with engine.connect() as db:
                assert (await db.execute(select(broadcasts.c.state))).scalar_one() == "draft"
            await dispatcher.feed_update(bot, callback(1, f"bc:confirm:{identifier}", 202))
            async with engine.connect() as db:
                assert (await db.execute(select(broadcasts.c.state))).scalar_one() == "sending"
                sent = (
                    (await db.execute(select(outbox).where(outbox.c.kind == "broadcast.news")))
                    .mappings()
                    .all()
                )
                assert len(sent) == 1 and sent[0]["telegram_user_id"] == 3
        finally:
            await bot.session.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_owner_team_revoke_needs_authorization_and_reports_are_local(task_db, monkeypatch):
    async def scenario():
        engine = create_db(task_db)
        cfg = load_studio()
        ui = RichMenuService(engine, 1, cfg)
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
        bot = Bot("123:synthetic")
        monkeypatch.setattr(bot, "answer_callback_query", AsyncMock())
        try:
            await populate(ui)
            async with engine.connect() as db:
                team = await view_data(db, owner(), "team")
                report = await view_data(db, owner(), "reports")
            assert any(m["id"] == 4 for m in team["members"])
            assert 'data="ui:team:detail:4"' in (render(owner(), "team", team).html or "")
            assert not can_open(User(telegram_user_id=2, role="admin"), "team:detail:4")
            assert "не загрузка залов YCLIENTS" in (render(owner(), "reports", report).html or "")
            await dispatcher.feed_update(bot, callback(2, "team:revoke:4", 301))
            async with engine.connect() as db:
                assert not (
                    await db.execute(
                        select(users.c.access_revoked).where(users.c.telegram_user_id == 4)
                    )
                ).scalar_one()
            await dispatcher.feed_update(bot, callback(1, "team:revoke:4", 302))
            async with engine.connect() as db:
                assert (
                    await db.execute(
                        select(users.c.access_revoked).where(users.c.telegram_user_id == 4)
                    )
                ).scalar_one()
        finally:
            await bot.session.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_r4_private_contact_redacted_and_screen_payloads_fail_closed(task_db):
    async def scenario():
        engine = create_db(task_db)
        cfg = load_studio()
        ui = RichMenuService(engine, 1, cfg)
        try:
            await populate(ui)
            fake_phone = "+79990001122"
            draft = rule("R4", "fake-r4", cfg, NOW, body=fake_phone, requester_id=3)
            assert draft is not None
            async with ui.transaction(NOW) as db:
                r4 = await create_task(db, draft, cfg, NOW)
            async with engine.connect() as db:
                model = await view_data(db, owner(), "tasks")
            html = render(owner(), "tasks", model).html or ""
            assert fake_phone not in html
            assert f"#{r4}" in html
            assert "Ожидается повторная привязка" in html
            assert f'data="cl:retry:{r4}"' not in html
            for invalid in (
                "tasks:done:-1",
                "tasks:open:99999",
                "team:detail:-1",
                "broadcasts:detail:0",
                "tasks:other:0",
            ):
                assert not can_open(owner(), invalid)
        finally:
            await engine.dispose()

    asyncio.run(scenario())
