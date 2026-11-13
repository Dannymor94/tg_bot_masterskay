"""T-305b: synthetic announcement transaction, unsubscribe and delivery tests."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import select

from app.bot.broadcasts import build_router
from app.db.engine import create_db
from app.db.models import audit_log, broadcasts, outbox, users
from app.jobs.outbox import TelegramSender, send_one
from app.services.access import AccessDenied
from app.services.broadcasts import BroadcastService
from app.settings import load_studio

NOW = datetime(2026, 1, 1, 9, tzinfo=UTC)


async def seeded(engine):
    service = BroadcastService(engine, 1, load_studio())
    await service.bootstrap(NOW)
    rows = [
        {"telegram_user_id": 2, "role": "admin", "created_at": NOW},
        {"telegram_user_id": 3, "role": "client", "news_opt_in": True, "created_at": NOW},
        {"telegram_user_id": 4, "role": "client", "news_opt_in": True, "created_at": NOW},
        {"telegram_user_id": 5, "role": "client", "news_opt_in": False, "created_at": NOW},
        {
            "telegram_user_id": 6,
            "role": "teacher",
            "teacher_kind": "renter",
            "news_opt_in": True,
            "created_at": NOW,
        },
        {
            "telegram_user_id": 7,
            "role": "client",
            "news_opt_in": True,
            "access_revoked": True,
            "created_at": NOW,
        },
        {
            "telegram_user_id": 8,
            "role": "client",
            "news_opt_in": True,
            "blocked_at": NOW,
            "created_at": NOW,
        },
    ]
    async with engine.begin() as db:
        for item in rows:
            await db.execute(users.insert().values(**item))
    return service


async def all_rows(engine, table):
    async with engine.connect() as db:
        return (await db.execute(select(table))).mappings().all()


def test_draft_preview_authorization_and_deduplication(tmp_path):
    async def scenario():
        engine = create_db(tmp_path / "announcements.db")
        try:
            service = await seeded(engine)
            with pytest.raises(AccessDenied):
                await service.create(3, "forged", "clients_all", "Bad", NOW)
            with pytest.raises(ValueError):
                await service.create(1, "bad", "unknown", "Bad", NOW)
            broadcast_id = await service.create(
                2, "msg10", "clients_all", "Synthetic text", NOW
            )
            assert await service.create(
                2, "msg10", "clients_all", "Synthetic text", NOW
            ) == broadcast_id
            with pytest.raises(ValueError):
                await service.create(2, "msg10", "clients_all", "changed", NOW)
            events = await all_rows(engine, outbox)
            assert len(events) == 1
            assert events[0]["kind"] == "broadcast.preview"
            assert "получателей: 2" in events[0]["payload_min"]["text"]
            assert len(await all_rows(engine, broadcasts)) == 1
            assert not any(row["kind"] == "broadcast.news" for row in events)
            with pytest.raises(AccessDenied):
                await service.confirm(1, broadcast_id, NOW)
            assert await service.confirm(2, broadcast_id, NOW) == 2
            assert await service.confirm(2, broadcast_id, NOW) == 0
            assert not await service.cancel(2, broadcast_id, NOW)
            sent = [r for r in await all_rows(engine, outbox) if r["kind"] == "broadcast.news"]
            assert {r["telegram_user_id"] for r in sent} == {3, 4}
            assert {r["dedupe_key"] for r in sent} == {
                f"{broadcast_id}:3", f"{broadcast_id}:4"
            }
            assert all(r["payload_min"] == {"broadcast_id": broadcast_id} for r in sent)
            assert "Synthetic text" not in repr(sent)
            audits = await all_rows(engine, audit_log)
            assert [a["action"] for a in audits].count("broadcast.created") == 1
            assert [a["action"] for a in audits].count("broadcast.confirmed") == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_cancel_and_empty_audience_summary(tmp_path):
    async def scenario():
        engine = create_db(tmp_path / "empty.db")
        try:
            service = await seeded(engine)
            id1 = await service.create(1, "cancel1", "clients_all", "Cancel", NOW)
            assert await service.cancel(1, id1, NOW)
            assert not await service.cancel(1, id1, NOW)
            with pytest.raises(ValueError):
                await service.confirm(1, id1, NOW)
            id2 = await service.create(1, "empty", "teachers_all", "None", NOW)
            # Renter is opted-in; disable their subscription before confirm.
            async with engine.begin() as db:
                await db.execute(
                    users.update().where(users.c.telegram_user_id == 6).values(news_opt_in=False)
                )
            assert await service.confirm(1, id2, NOW) == 0
            rows = await all_rows(engine, outbox)
            reports = [r for r in rows if r["kind"] == "broadcast.summary"]
            assert len(reports) == 1
            assert "доставлено 0" in reports[0]["payload_min"]["text"]
            items = await all_rows(engine, broadcasts)
            assert {r["id"]: r["state"] for r in items} == {
                id1: "cancelled", id2: "done"
            }
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_unsubscribe_is_own_only_and_cancels_unsent_news(tmp_path):
    async def scenario():
        engine = create_db(tmp_path / "optout.db")
        try:
            service = await seeded(engine)
            identifier = await service.create(1, "news", "clients_all", "Private", NOW)
            assert await service.confirm(1, identifier, NOW) == 2
            with pytest.raises(AccessDenied):
                await service.unsubscribe(6, NOW, broadcast_id=identifier)
            assert await service.unsubscribe(3, NOW, broadcast_id=identifier)
            assert not await service.unsubscribe(3, NOW, broadcast_id=identifier)
            async with engine.connect() as db:
                opt_in = (
                    await db.execute(
                        select(users.c.news_opt_in).where(users.c.telegram_user_id == 3)
                    )
                ).scalar_one()
                state = (
                    await db.execute(
                        select(outbox.c.state).where(
                            outbox.c.kind == "broadcast.news",
                            outbox.c.telegram_user_id == 3,
                        )
                    )
                ).scalar_one()
            assert opt_in is False and state == "unsubscribed"
            send = AsyncMock()
            for t in range(12):
                await send_one(engine, send, service.cfg, NOW + timedelta(seconds=t * 2))
            assert not any(
                call.args[0]["telegram_user_id"] == 3
                and call.args[0]["kind"] == "broadcast.news"
                for call in send.await_args_list
            )
            items = await all_rows(engine, broadcasts)
            assert items[0]["state"] == "done"
            assert items[0]["sent"] == 1 and items[0]["unsubscribed"] == 1
            rows = await all_rows(engine, outbox)
            assert len([r for r in rows if r["kind"] == "broadcast.summary"]) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_send_time_rechecks_late_optout_and_revocation(tmp_path):
    async def scenario():
        engine = create_db(tmp_path / "race.db")
        try:
            service = await seeded(engine)
            identifier = await service.create(1, "race", "clients_all", "Synthetic", NOW)
            assert await service.confirm(1, identifier, NOW) == 2
            async with engine.begin() as db:
                await db.execute(
                    users.update().where(users.c.telegram_user_id == 3).values(news_opt_in=False)
                )
                await db.execute(
                    users.update().where(users.c.telegram_user_id == 4).values(access_revoked=True)
                )
            sends = AsyncMock()
            for t in range(12):
                await send_one(engine, sends, service.cfg, NOW + timedelta(seconds=t * 2))
            assert not any(
                call.args[0]["kind"] == "broadcast.news"
                for call in sends.await_args_list
            )
            row = (await all_rows(engine, broadcasts))[0]
            assert row["state"] == "done"
            assert row["unsubscribed"] == 1 and row["blocked"] == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_photo_sender_attaches_unsubscribe_keyboard(monkeypatch):
    async def scenario():
        bot = Bot("123:synthetic")
        send_photo = AsyncMock()
        monkeypatch.setattr(bot, "send_photo", send_photo)
        try:
            await TelegramSender(bot)(
                {
                    "telegram_user_id": 3,
                    "kind": "broadcast.news",
                    "payload_min": {
                        "text": "Synthetic caption",
                        "photo_file_id": "synthetic-file-id",
                        "reply_markup": json.dumps(
                            {
                                "inline_keyboard": [[{
                                    "text": "Отписаться", "callback_data": "bc:off:10"
                                }]]
                            }
                        ),
                    },
                }
            )
            assert send_photo.await_count == 1
            kwargs = send_photo.call_args.kwargs
            assert kwargs["caption"] == "Synthetic caption"
            assert kwargs["photo"] == "synthetic-file-id"
            assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "bc:off:10"
        finally:
            await bot.session.close()

    asyncio.run(scenario())


def test_photo_preview_and_transactional_enqueue_rollback(tmp_path, monkeypatch):
    from app.services import broadcasts as module

    async def scenario():
        engine = create_db(tmp_path / "preview-photo.db")
        try:
            service = await seeded(engine)
            original = module.enqueue

            async def fail(*args, **kwargs):
                raise RuntimeError("synthetic preview failure")

            monkeypatch.setattr(module, "enqueue", fail)
            with pytest.raises(RuntimeError):
                await service.create(
                    1, "photo-1", "clients_all", "Synthetic", NOW,
                    photo_file_id="synthetic-photo-id",
                )
            assert await all_rows(engine, broadcasts) == []
            assert await all_rows(engine, outbox) == []
            monkeypatch.setattr(module, "enqueue", original)
            identifier = await service.create(
                1, "photo-1", "clients_all", "Synthetic", NOW,
                photo_file_id="synthetic-photo-id",
            )
            items = await all_rows(engine, outbox)
            assert [r["kind"] for r in items] == [
                "broadcast.preview.photo", "broadcast.preview"
            ]
            assert items[0]["payload_min"]["photo_file_id"] == "synthetic-photo-id"
            assert items[1]["payload_min"]["text"].startswith(f"Рассылка №{identifier}")
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_two_pending_broadcasts_reconciled_after_telegram_block(tmp_path):
    from aiogram.exceptions import TelegramForbiddenError
    from aiogram.methods import SendMessage

    async def scenario():
        engine = create_db(tmp_path / "telegram-block.db")
        try:
            service = await seeded(engine)
            first = await service.create(1, "b1", "clients_all", "One", NOW)
            second = await service.create(1, "b2", "clients_all", "Two", NOW)
            assert await service.confirm(1, first, NOW) == 2
            assert await service.confirm(1, second, NOW) == 2

            async def transport(row):
                if row["kind"] == "broadcast.news" and row["telegram_user_id"] == 3:
                    raise TelegramForbiddenError(
                        method=SendMessage(chat_id=3, text="Synthetic"),
                        message="Synthetic forbidden",
                    )

            for n in range(16):
                await send_one(
                    engine, transport, service.cfg, NOW + timedelta(seconds=n * 2)
                )
            items = {r["id"]: r for r in await all_rows(engine, broadcasts)}
            assert items[first]["state"] == "done"
            assert items[second]["state"] == "done"
            assert items[first]["blocked"] == items[second]["blocked"] == 1
            reports = [
                r for r in await all_rows(engine, outbox) if r["kind"] == "broadcast.summary"
            ]
            assert len(reports) == 2
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def make_update(actor, text=None, *, callback=None, chat=None, kind="private"):
    message = Message(
        message_id=10,
        date=NOW,
        chat=Chat(id=actor if chat is None else chat, type=kind),
        from_user=User(id=actor, is_bot=False, first_name="Synthetic"),
        text=text,
    )
    if callback:
        return Update(
            update_id=12,
            callback_query=CallbackQuery(
                id=callback,
                from_user=User(id=actor, is_bot=False, first_name="Synthetic"),
                chat_instance="synthetic",
                message=message,
                data=callback,
            ),
        )
    return Update(update_id=10, message=message)


def test_broadcast_transport_identity_and_callback_scope(caplog):
    async def scenario():
        service = AsyncMock(spec=BroadcastService)
        bot = Bot("123:synthetic")
        dispatcher = Dispatcher()
        dispatcher.include_router(build_router(service, lambda: NOW))
        try:
            for update in [
                make_update(2, "/announce clients_all forged", chat=3),
                make_update(2, "/announce clients_all group", chat=-10, kind="group"),
                make_update(2, "/announce clients_all"),
            ]:
                await dispatcher.feed_update(bot, update)
            service.create.assert_not_called()
            await dispatcher.feed_update(
                bot, make_update(2, "/announce clients_all Synthetic")
            )
            service.create.assert_awaited_once_with(
                2, "10", "clients_all", "Synthetic", NOW, photo_file_id=None
            )
            for action in ("send", "cancel", "off"):
                await dispatcher.feed_update(
                    bot, make_update(2, callback=f"bc:{action}:7", chat=-10, kind="group")
                )
            service.confirm.assert_not_called()
            service.cancel.assert_not_called()
            service.unsubscribe.assert_not_called()
            await dispatcher.feed_update(bot, make_update(2, callback="bc:send:7"))
            await dispatcher.feed_update(bot, make_update(2, callback="bc:off:7"))
            service.confirm.assert_awaited_once_with(2, 7, NOW)
            service.unsubscribe.assert_awaited_once_with(2, NOW, broadcast_id=7)
            service.create.side_effect = RuntimeError("PRIVATE_BROADCAST")
            await dispatcher.feed_update(
                bot, make_update(2, "/announce clients_all PRIVATE_BROADCAST")
            )
            assert "PRIVATE_BROADCAST" not in caplog.text
        finally:
            await bot.session.close()

    asyncio.run(scenario())
