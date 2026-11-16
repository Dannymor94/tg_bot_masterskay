"""Evidence that owner diagnostics count persisted bot data, not CRM data."""

import asyncio
from datetime import timedelta

import pytest

from app.db.engine import create_db
from app.db.models import outbox
from app.domain.models import User
from app.services.questions import QuestionService
from app.services.rich_menu import RichMenuService
from app.services.rich_views import screen_parts, view_data
from app.settings import load_studio
from app.ui.rich_menu import can_open, render
from tests.integration.test_rbac_bot import NOW
from tests.integration.test_rich_menu import populate


def actor(number: int, *, owner: bool = False) -> User:
    return User(telegram_user_id=number, role="admin", is_owner=owner)


def test_owner_operational_report_is_real_bounded_and_private(task_db):
    async def scenario():
        engine = create_db(task_db)
        cfg = load_studio()
        rich = RichMenuService(engine, 1, cfg)
        try:
            await populate(rich)
            await QuestionService(engine, 1, cfg).ask(
                3, "overdue-question", "Синтетический вопрос", NOW
            )
            # Outbox statuses represent Telegram delivery state, not customer attendance.
            async with engine.begin() as db:
                for i, status in enumerate(
                    ("sent", "failed", "blocked", "cancelled", "unsubscribed", "summary")
                ):
                    await db.execute(
                        outbox.insert().values(
                            telegram_user_id=1,
                            kind="synthetic.report",
                            payload_min={"text": "internal"},
                            state=status,
                            attempts=0,
                            next_attempt_at=NOW,
                            dedupe_key=f"report-synthetic-{i}",
                        )
                    )
            as_of = NOW + timedelta(hours=2)
            async with engine.connect() as db:
                report = await view_data(
                    db, actor(1, owner=True), "reports:operations", as_of=as_of
                )
                overview = await view_data(db, actor(1, owner=True), "reports", as_of=as_of)
                forbidden = await view_data(db, actor(2), "reports:operations", as_of=as_of)
            assert report["report"]["overdue"] == {"R3": 1}
            delivery = report["report"]["delivery"]
            assert delivery["pending"] >= 1  # includes the deferred summary state
            assert delivery["sent"] == 1
            assert delivery["failed"] == 1
            assert delivery["blocked"] == 1
            assert delivery["cancelled"] == 1
            assert delivery["unsubscribed"] == 1
            assert overview["report"]["overdue"] == {"R3": 1}
            assert forbidden.get("report") is None
            assert can_open(actor(1, owner=True), "reports:operations")
            assert not can_open(actor(2), "reports:operations")
            assert not can_open(User(telegram_user_id=3, role="client"), "reports:operations")
            html = render(actor(1, owner=True), "reports:operations", report).html or ""
            assert "Просроченные по направлениям" in html
            assert "Вопросы и заявки" in html
            assert "Приняты API Telegram" in html
            assert "Принято API не означает" in html
            assert "Синтетический вопрос" not in html
            assert 'data="ui:reports:operations"' in html
            assert "YCLIENTS" in html
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_operational_report_excludes_done_tasks_and_missing_deadlines(task_db):
    async def scenario():
        engine = create_db(task_db)
        cfg = load_studio()
        rich = RichMenuService(engine, 1, cfg)
        try:
            await populate(rich)
            question = await QuestionService(engine, 1, cfg).ask(
                3, "completed", "Можно закончить?", NOW
            )
            from app.services.tasks import TaskService

            tasks = TaskService(engine, 1, cfg)
            await tasks.take(2, question, NOW)
            await tasks.finish(2, question, NOW, reply="Ответ")
            await tasks.manual(1, "no-due", "Ручная задача без срока", NOW)
            async with engine.connect() as db:
                now_report = await view_data(
                    db, actor(1, owner=True), "reports:operations", as_of=NOW + timedelta(days=7)
                )
            assert now_report["report"]["overdue"] == {}
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("screen", ["reports:operations:1", "reports:ops", "reports:Operations"])
def test_operational_report_invalid_screen_rejected(screen):
    with pytest.raises(ValueError):
        screen_parts(screen)
