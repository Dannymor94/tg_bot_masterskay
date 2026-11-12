import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update, User
from pydantic import SecretStr
from sqlalchemy import select

from app.bot.rbac import build_router
from app.db.engine import create_db
from app.db.models import invites, outbox, users
from app.jobs.outbox import TelegramSender
from app.runtime import migrate
from app.services.invites import InviteService, InviteTokens
from app.settings import load_studio

NOW = datetime(2026, 1, 1, 9, tzinfo=UTC)


def update(actor, text, message_id=1, chat_id=None):
    return Update(
        update_id=message_id,
        message=Message(
            message_id=message_id,
            date=NOW,
            chat=Chat(id=actor if chat_id is None else chat_id, type="private"),
            from_user=User(id=actor, is_bot=False, first_name="Synthetic"),
            text=text,
        ),
    )


def test_commands_identity_and_outbox_only(tmp_path):
    path = tmp_path / "bot.db"
    migrate(path)

    async def scenario():
        engine = create_db(path)
        tokens = InviteTokens(SecretStr("synthetic-key"))
        service = InviteService(engine, 1, load_studio(), tokens)
        bot = Bot("123:synthetic")
        dispatcher = Dispatcher()
        dispatcher.include_router(build_router(service, lambda: NOW))
        try:
            await service.bootstrap(NOW)
            # Forged chat identity, group and bot-origin requests have no effects.
            await dispatcher.feed_update(bot, update(1, "/invite admin", chat_id=2))
            original = update(1, "/invite admin")
            assert original.message is not None
            group = original.model_copy(
                update={
                    "message": original.message.model_copy(
                        update={"chat": Chat(id=-10, type="group")}
                    )
                }
            )
            await dispatcher.feed_update(bot, group)
            assert original.message.from_user is not None
            bot_origin = original.model_copy(
                update={
                    "message": original.message.model_copy(
                        update={
                            "from_user": original.message.from_user.model_copy(
                                update={"is_bot": True}
                            )
                        }
                    )
                }
            )
            await dispatcher.feed_update(bot, bot_origin)
            await dispatcher.feed_update(bot, update(2, "/invite admin"))
            async with engine.connect() as db:
                assert (await db.execute(select(invites))).all() == []
            await dispatcher.feed_update(bot, update(1, "/invite admin", 10))
            async with engine.connect() as db:
                row = (await db.execute(select(outbox))).mappings().one()
                token = tokens.derive(row["payload_min"]["nonce"]).get_secret_value()
            # Extra claimed ID is rejected; valid redemption binds to sender 2 only.
            await dispatcher.feed_update(bot, update(2, f"/accept {token} 3", 11))
            async with engine.connect() as db:
                assert (await db.execute(select(invites.c.used_at))).scalar_one() is None
            await dispatcher.feed_update(bot, update(2, f"/start invite_{token}", 12))
            await dispatcher.feed_update(bot, update(2, "/access", 13))
            await dispatcher.feed_update(bot, update(2, "/revoke 1", 14))
            await dispatcher.feed_update(bot, update(1, "/revoke 2", 15))
            await dispatcher.feed_update(bot, update(2, "/access", 16))
            async with engine.connect() as db:
                rows = (await db.execute(select(users))).mappings().all()
                assert [(r["telegram_user_id"], r["access_revoked"]) for r in rows] == [
                    (1, False),
                    (2, True),
                ]
                messages = (await db.execute(select(outbox))).mappings().all()
                assert all(token not in repr(row["payload_min"]) for row in messages)
                assert not any('"16"' in row["dedupe_key"] for row in messages)
        finally:
            await bot.session.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_sender_reconstructs_invite_only_in_memory(monkeypatch):
    bot = Bot("123:synthetic")
    send = AsyncMock()
    monkeypatch.setattr(
        bot, "me", AsyncMock(return_value=SimpleNamespace(username="synthetic_bot"))
    )
    monkeypatch.setattr(bot, "send_message", send)
    tokens = InviteTokens(SecretStr("synthetic-key"))
    sender = TelegramSender(bot, tokens=tokens)
    row = {
        "telegram_user_id": 1,
        "kind": "rbac.invite",
        "payload_min": {"nonce": "synthetic-nonce", "role": "admin"},
    }

    async def scenario():
        try:
            await sender(row)
        finally:
            await bot.session.close()

    asyncio.run(scenario())
    text = send.call_args.kwargs["text"]
    assert "https://t.me/synthetic_bot?start=invite_" in text
    assert tokens.derive("synthetic-nonce").get_secret_value() in text
    assert "text" not in row["payload_min"]


def test_polling_cancels_cleanly_without_network(tmp_path, monkeypatch):
    from app.bot.rbac import poll

    async def scenario():
        started, closed, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def fake_polling(self, bot, **kwargs):
            assert kwargs["handle_signals"] is False
            assert kwargs["handle_as_tasks"] is False
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        monkeypatch.setattr(Dispatcher, "start_polling", fake_polling)
        engine = create_db(tmp_path / "poll.db")
        service = InviteService(engine, 1, load_studio(), InviteTokens(SecretStr("synthetic")))
        bot = Bot("123:synthetic")
        try:
            running = asyncio.create_task(poll(bot, service, stopped))
            await asyncio.wait_for(started.wait(), timeout=1)
            stopped.set()
            await asyncio.wait_for(running, timeout=1)
            assert closed.is_set()
        finally:
            await bot.session.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_runtime_bootstraps_before_polling(tmp_path, monkeypatch):
    from app import runtime

    async def fake_poll(bot, service, stopped):
        async with service.engine.connect() as db:
            owner = (await db.execute(select(users))).mappings().one()
            assert owner["telegram_user_id"] == 1 and owner["is_owner"]
        stopped.set()

    monkeypatch.setattr(runtime, "poll", fake_poll)

    async def scenario():
        bot = Bot("123:synthetic")
        try:
            await runtime.serve(
                1,
                tmp_path / "runtime.db",
                bot=bot,
                cfg=load_studio(),
                owner_id=1,
                tokens=InviteTokens(SecretStr("synthetic")),
            )
        finally:
            await bot.session.close()

    asyncio.run(scenario())
