"""Issue #14: actual onboarding, queue, sender and transport used together."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, MessageEntity, Update, User
from pydantic import SecretStr

from app.bot.client import build_router as client_router
from app.bot.rbac import build_router as access_router
from app.bot.tasks import build_router as task_router
from app.db.engine import create_db
from app.db.models import audit_log, kv, outbox, tasks, users
from app.domain.client_matching import Candidate
from app.jobs.outbox import TelegramSender, send_one
from app.jobs.tasks import maintain
from app.services.access import AccessDenied
from app.services.client_onboarding import ClientService
from app.services.invites import InviteService, InviteTokens
from app.services.tasks import TaskService
from app.settings import load_studio
from app.texts import ru
from tests.integration.test_client_bot import message
from tests.integration.test_client_onboarding import NOW, PHONE, ready, rows


async def setup(engine):
    cfg = load_studio()
    clients = ClientService(engine, 1, cfg)
    queue = TaskService(engine, 1, cfg)
    await clients.bootstrap(NOW)
    async with engine.begin() as db:
        await db.execute(users.insert().values(telegram_user_id=3, role="admin", created_at=NOW))
    await ready(clients)
    await clients.contact(2, 2, SecretStr(PHONE), 11, NOW)
    return clients, queue


@pytest.mark.parametrize("actor", [1, 3])
def test_take_r4_preserves_viable_retry_and_privacy(task_db, actor, caplog):
    async def scenario():
        engine = create_db(task_db)
        try:
            clients, queue = await setup(engine)
            task = (await rows(engine, tasks))[0]
            assert task["last_ping_at"] == NOW
            assert not await queue.take(actor, task["id"], NOW)
            assert not await queue.finish(actor, task["id"], NOW)
            assert not await clients.retry(actor, task["id"], "unavailable", NOW)
            current = (await rows(engine, tasks))[0]
            assert current["state"] == "open" and current["body"] == PHONE
            lookup = AsyncMock()
            lookup.candidates.return_value = [Candidate(client_id=50, phone=SecretStr(PHONE))]
            clients.lookup = lookup
            assert await clients.retry(actor, task["id"], "verified", NOW)
            assert not await clients.retry(actor, task["id"], "another", NOW)
            assert not await queue.take(actor, task["id"], NOW)
            lookup.candidates.assert_awaited_once_with(SecretStr(PHONE))
            task = (await rows(engine, tasks))[0]
            assert task["state"] == "done" and task["body"] == ""
            assert task["taken_by"] is None
            assert [r for r in await rows(engine, users) if r["telegram_user_id"] == 2][0][
                "yclients_client_id"
            ] == 50
            for table in (users, kv, outbox, audit_log):
                assert PHONE not in repr(await rows(engine, table))
            assert PHONE not in caplog.text
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_two_admins_take_and_retry_r4_race(task_db):
    async def scenario():
        engines = [create_db(task_db), create_db(task_db)]
        try:
            clients, queue = await setup(engines[0])
            other_client = ClientService(engines[1], 1, clients.cfg)
            other_queue = TaskService(engines[1], 1, clients.cfg)
            lookup = AsyncMock()
            lookup.candidates.return_value = [Candidate(client_id=50, phone=SecretStr(PHONE))]
            clients.lookup = other_client.lookup = lookup
            result = await asyncio.gather(
                queue.take(1, 1, NOW),
                other_queue.take(3, 1, NOW),
                clients.retry(1, 1, "one", NOW),
                other_client.retry(3, 1, "two", NOW),
            )
            assert result[:2] == [False, False]
            assert sorted(result[2:]) == [False, True]
            assert lookup.candidates.await_count == 1
            assert len(await rows(engines[0], tasks)) == 1
            assert (await rows(engines[0], tasks))[0]["body"] == ""
            effects = await rows(engines[0], outbox)
            assert len({r["dedupe_key"] for r in effects}) == len(effects)
            assert (
                len(
                    [
                        r
                        for r in effects
                        if r["kind"] == "task.card" and r["dedupe_key"].endswith('"created"]')
                    ]
                )
                == 2
            )
            actions = [r["action"] for r in await rows(engines[0], audit_log)]
            assert actions.count("client.linked") == 1
            assert "tasks.taken" not in actions
            client = [r for r in await rows(engines[0], users) if r["telegram_user_id"] == 2][0]
            assert client["role"] == "client" and not client["is_owner"]
        finally:
            for engine in engines:
                await engine.dispose()

    asyncio.run(scenario())


def test_real_sender_combines_onboarding_cards_reminders_and_fresh_rights(task_db, monkeypatch):
    async def scenario():
        engine = create_db(task_db)
        bot = Bot("123:synthetic")
        sent = AsyncMock()
        monkeypatch.setattr(bot, "send_message", sent)
        try:
            clients, queue = await setup(engine)
            sender = TelegramSender(bot)
            instant = NOW

            async def drain():
                nonlocal instant
                while await send_one(engine, sender, clients.cfg, instant):
                    instant += timedelta(seconds=1)

            await drain()
            deliveries = sent.call_args_list
            markups = [c.kwargs.get("reply_markup") for c in deliveries]
            assert any(
                m and getattr(m, "keyboard", None) and m.keyboard[0][0].request_contact
                for m in markups
            )
            callbacks = [
                b.callback_data
                for m in markups
                if m and getattr(m, "inline_keyboard", None)
                for line in m.inline_keyboard
                for b in line
            ]
            assert "cl:consent:10" in callbacks and "cl:yes:10" in callbacks
            assert "cl:retry:1" in callbacks and "task:take:1" not in callbacks
            card = next(c.kwargs for c in deliveries if PHONE in c.kwargs["text"])
            assert ru.CLIENT_RETRY in card["text"]
            due = NOW + timedelta(minutes=clients.cfg.tasks.overdue_ping_minutes)
            await maintain(engine, clients.cfg, due)
            assert (await rows(engine, tasks))[0]["last_ping_at"] == due
            # Pending cards are cancelled if permissions change before rendering.
            await queue.revoke(1, 3, due)
            sent.reset_mock()
            instant = due
            await drain()
            assert all(c.kwargs["chat_id"] != 3 for c in sent.call_args_list)
            with pytest.raises(AccessDenied):
                await clients.retry(3, 1, "stale", due)
            manual = await queue.manual(1, "new", "synthetic", due)
            instant += timedelta(seconds=1)
            await drain()
            assert any(
                b.callback_data == f"task:take:{manual}"
                for c in sent.call_args_list
                if (m := c.kwargs.get("reply_markup")) and getattr(m, "inline_keyboard", None)
                for line in m.inline_keyboard
                for b in line
            )
            # Resolve before queued card delivery: sender must never reuse the phone.
            await queue.queue(1, "before-link", due)
            lookup = AsyncMock()
            lookup.candidates.return_value = [Candidate(client_id=50, phone=SecretStr(PHONE))]
            clients.lookup = lookup
            await clients.retry(1, 1, "verified", due)
            sent.reset_mock()
            instant += timedelta(seconds=1)
            await drain()
            assert all(PHONE not in c.kwargs["text"] for c in sent.call_args_list)
            assert all(
                "cl:retry:1" != b.callback_data
                for c in sent.call_args_list
                if (m := c.kwargs.get("reply_markup")) and getattr(m, "inline_keyboard", None)
                for line in m.inline_keyboard
                for b in line
            )
        finally:
            await bot.session.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_r4_notification_failure_rolls_back_shared_task_identity(task_db, monkeypatch):
    async def scenario():
        from app.services import tasks as task_module

        engine = create_db(task_db)
        try:
            clients = ClientService(engine, 1, load_studio())
            await clients.bootstrap(NOW)
            await ready(clients)
            before = [await rows(engine, t) for t in (users, kv, tasks, outbox, audit_log)]
            original = task_module.card

            async def fail(*args):
                await original(*args)
                raise RuntimeError("synthetic")

            with monkeypatch.context() as patch:
                patch.setattr(task_module, "card", fail)
                with pytest.raises(RuntimeError):
                    await clients.contact(2, 2, SecretStr(PHONE), 11, NOW)
            assert [await rows(engine, t) for t in (users, kv, tasks, outbox, audit_log)] == before
            assert await clients.contact(2, 2, SecretStr(PHONE), 11, NOW)
            assert len(await rows(engine, tasks)) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_all_routers_invites_and_callbacks_use_authenticated_actor(task_db):
    async def scenario():
        engine = create_db(task_db)
        bot = Bot("123:synthetic")
        try:
            clients, queue = await setup(engine)
            invites = InviteService(engine, 1, clients.cfg, InviteTokens(SecretStr("synthetic")))
            dispatcher = Dispatcher()
            dispatcher.include_router(access_router(invites, lambda: NOW))
            dispatcher.include_router(task_router(queue, lambda: NOW))
            dispatcher.include_router(client_router(clients, lambda: NOW))
            # Exercise router order with a real invite generated by the service.
            assert await invites.issue(1, "teacher", "invite", NOW)
            invite_row = next(r for r in await rows(engine, outbox) if r["kind"] == "rbac.invite")
            token = invites.tokens.derive(invite_row["payload_min"]["nonce"])
            text = f"/start invite_{token.get_secret_value()}"
            await dispatcher.feed_update(
                bot,
                Update(
                    update_id=1,
                    message=message(
                        actor=4,
                        chat=4,
                        text=text,
                        entities=[MessageEntity(type="bot_command", offset=0, length=6)],
                    ),
                ),
            )
            assert [r for r in await rows(engine, users) if r["telegram_user_id"] == 4][0][
                "role"
            ] == "teacher"
            await dispatcher.feed_update(
                bot,
                Update(
                    update_id=2,
                    message=message(
                        actor=5,
                        chat=5,
                        text="/start src_test",
                        entities=[MessageEntity(type="bot_command", offset=0, length=6)],
                    ),
                ),
            )
            assert [r for r in await rows(engine, users) if r["telegram_user_id"] == 5][0][
                "source"
            ] == "src_test"
            manual = await queue.manual(1, "manual", "synthetic", NOW)
            for data in (
                f"task:take:{manual}:1",
                "cl:retry:1:1",
                "cl:retry:-1",
                "cl:retry:１",
                f"task:take:{manual}",
                "cl:retry:1",
            ):
                await dispatcher.feed_update(
                    bot,
                    Update(
                        update_id=3,
                        callback_query=CallbackQuery(
                            id=data,
                            from_user=User(id=2, first_name="Synthetic", is_bot=False),
                            chat_instance="synthetic",
                            message=message(),
                            data=data,
                        ),
                    ),
                )
            assert all(r["state"] == "open" for r in await rows(engine, tasks))
            # A legitimate callback still reaches the queue with the real admin.
            await dispatcher.feed_update(
                bot,
                Update(
                    update_id=4,
                    callback_query=CallbackQuery(
                        id="take",
                        from_user=User(id=3, first_name="Synthetic", is_bot=False),
                        chat_instance="synthetic",
                        message=message(actor=3, chat=3),
                        data=f"task:take:{manual}",
                    ),
                ),
            )
            assert [r for r in await rows(engine, tasks) if r["id"] == manual][0]["taken_by"] == 3
        finally:
            await bot.session.close()
            await engine.dispose()

    asyncio.run(scenario())
