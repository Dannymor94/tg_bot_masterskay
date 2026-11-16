"""Authenticated role journeys for calendar and explanatory report pages."""

import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from pydantic import SecretStr
from sqlalchemy import select

from app.bot.rich_menu import build_router
from app.db.engine import create_db
from app.db.models import tasks
from app.domain.models import Role, User
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
from app.ui.rich_menu import can_open, render
from tests.integration.test_rbac_bot import NOW
from tests.integration.test_rich_compose import label, reply
from tests.integration.test_rich_menu import callback, populate


def actor(number: int, role: Role, *, owner: bool = False) -> User:
    return User(telegram_user_id=number, role=role, is_owner=owner)


def test_client_teacher_calendar_owner_restrictions_and_live_artwork(task_db, monkeypatch):
    async def scenario():
        engine = create_db(task_db)
        cfg = load_studio()
        ui = RichMenuService(engine, 1, cfg)
        bot = Bot("123:synthetic")
        edit = AsyncMock()
        monkeypatch.setattr(bot, "edit_message_text", edit)
        try:
            await populate(ui)
            selected = (NOW + timedelta(days=3)).date()
            async with engine.connect() as db:
                data = await view_data(
                    db, actor(3, "client"), f"schedule:day:{selected.isoformat()}", today=NOW.date()
                )
                rental = await view_data(db, actor(4, "teacher"), "rent", today=NOW.date())
            html = (
                render(actor(3, "client"), f"schedule:day:{selected.isoformat()}", data).html or ""
            )
            assert "не свободный слот" in html
            assert f'data="ui:compose:booking:{selected}"' in html
            assert 'data="ui:compose:rent:' not in html
            assert 'data="ui:compose:rent"' in (
                render(actor(4, "teacher"), "rent", rental).html or ""
            )
            assert not can_open(actor(3, "client"), "reports:people")
            assert not can_open(actor(2, "admin"), "schedule")
            assert not can_open(actor(4, "teacher"), "schedule")
            with pytest.raises(AccessDenied):
                await ui.show(3, "forged", "reports:tasks", NOW)
            await ui.show(3, "calendar", f"schedule:day:{selected}", NOW, message_id=80)
            assert await send_one(engine, TelegramSender(bot), cfg, NOW)
            richer = edit.call_args.kwargs["rich_message"]
            assert "Календарь занятий" in (richer.html or "")
            assert richer.media and str(richer.media[0].media.media.path).endswith(
                "client-schedule.png"
            )
        finally:
            await bot.session.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_calendar_date_enters_real_r3_or_r1_request_not_yclients_booking(task_db, monkeypatch):
    async def scenario():
        engine = create_db(task_db)
        cfg = load_studio()
        ui = RichMenuService(engine, 1, cfg)
        compose = RichComposeService(engine, 1, cfg)
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
                compose,
                lambda: NOW,
            )
        )
        try:
            await populate(ui)
            selected = (NOW + timedelta(days=2)).date().isoformat()
            for person, kind, text, n in (
                (3, "booking", "Йога, после 16:00", 320),
                (4, "rent", "Малый зал, утром", 330),
            ):
                await dispatcher.feed_update(bot, callback(person, f"compose:{kind}:{selected}", n))
                prompt = await label(engine, person)
                assert date.fromisoformat(selected).strftime("%d.%m.%Y") in prompt
                await dispatcher.feed_update(bot, reply(person, text, prompt, n + 1))
            async with engine.connect() as db:
                rows = (await db.execute(select(tasks.c.code, tasks.c.body))).all()
            assert any(code == "R3" and selected in body and "Йога" in body for code, body in rows)
            assert any(
                code == "R1" and selected in body and "Малый зал" in body for code, body in rows
            )
            with pytest.raises(ValueError):
                await compose.begin(
                    3,
                    "past",
                    "booking",
                    NOW,
                    selected_date=(NOW - timedelta(days=1)).date().isoformat(),
                    menu_message_id=80,
                )
            with pytest.raises(ValueError):
                await compose.begin(
                    4,
                    "future",
                    "rent",
                    NOW,
                    selected_date=(NOW + timedelta(days=91)).date().isoformat(),
                    menu_message_id=80,
                )
        finally:
            await bot.session.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_owner_reports_explain_real_local_counts_and_restrict_scope(task_db):
    async def scenario():
        engine = create_db(task_db)
        cfg = load_studio()
        ui = RichMenuService(engine, 1, cfg)
        work = TaskService(engine, 1, cfg)
        try:
            await populate(ui)
            manual = await work.manual(1, "m1", "Проверить систему", NOW)
            await QuestionService(engine, 1, cfg).ask(3, "q1", "Есть вопрос", NOW)
            await RentRequestService(engine, 1, cfg).request(4, "r1", "Нужен зал", NOW)
            await work.take(2, manual, NOW)
            await work.finish(2, manual, NOW)
            await BroadcastService(engine, 1, cfg).create(
                1,
                "draft",
                "clients_all",
                "Новость",
                NOW,
            )
            async with engine.connect() as db:
                summary = await view_data(db, actor(1, "admin", owner=True), "reports")
                detail = await view_data(db, actor(1, "admin", owner=True), "reports:tasks")
                news = await view_data(db, actor(1, "admin", owner=True), "reports:news")
                people = await view_data(db, actor(1, "admin", owner=True), "reports:people")
            assert summary["report"]["states"] == {"open": 2, "taken": 0, "done": 1}
            assert detail["report"]["codes"]["R3"]["open"] == 1
            assert detail["report"]["codes"]["R1"]["open"] == 1
            assert news["report"]["news"]["draft"] == 1
            assert people["report"]["roles"] == {"admin": 2, "client": 2, "teacher": 1}
            view = render(actor(1, "admin", owner=True), "reports", summary).html or ""
            assert "Ожидают исполнителя" in view and "не загрузка залов YCLIENTS" in view
            assert 'data="ui:reports:news"' in view
            assert "Новость" not in view
            assert "Доставлено сообщений" in (
                render(actor(1, "admin", owner=True), "reports:news", news).html or ""
            )
            assert not can_open(actor(2, "admin"), "reports")
            assert not can_open(actor(3, "client"), "reports:people")
        finally:
            await engine.dispose()

    asyncio.run(scenario())
