import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.methods import SendMessage
from sqlalchemy import select

from app.db.engine import create_db
from app.db.models import outbox, users
from app.domain.models import OutboxDraft
from app.jobs.outbox import TelegramSender, send_one
from app.runtime import migrate
from app.services.outbox import enqueue
from app.settings import load_studio

NOW = datetime(2026, 11, 12, 9, tzinfo=UTC)
CFG = load_studio()
METHOD = SendMessage(chat_id=1, text="synthetic")


@pytest.fixture
def path(tmp_path):
    path = tmp_path / "delivery.db"
    migrate(path)
    return path


def draft(key="one", user=1, kind="text"):
    return OutboxDraft(
        telegram_user_id=user,
        kind=kind,
        payload_min={"text": "synthetic"},
        next_attempt_at=NOW,
        dedupe_key=key,
    )


async def put(engine, item=None, now=NOW):
    async with engine.begin() as connection:
        return await enqueue(connection, item or draft(), CFG, now)


async def rows(engine):
    async with engine.connect() as connection:
        return (await connection.execute(select(outbox).order_by(outbox.c.id))).mappings().all()


def test_restart_and_concurrent_senders(path):
    calls = []

    async def send(row):
        calls.append(row["dedupe_key"])
        await asyncio.sleep(0)  # force the second sender to contend

    async def scenario():
        engine = create_db(path)
        await put(engine)
        await engine.dispose()
        engine, other = create_db(path), create_db(path)
        try:
            assert sorted(
                await asyncio.gather(
                    send_one(engine, send, CFG, NOW), send_one(other, send, CFG, NOW)
                )
            ) == [False, True]
            assert not await put(engine)
            await engine.dispose()
            engine = create_db(path)
            assert not await send_one(engine, send, CFG, NOW + timedelta(seconds=1))
            assert calls == ["one"]
            assert (await rows(engine))[0]["state"] == "sent"
        finally:
            await engine.dispose()
            await other.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "error,state,delay",
    [
        (TelegramRetryAfter(method=METHOD, message="synthetic", retry_after=30), "pending", 30),
        (TelegramNetworkError(method=METHOD, message="synthetic"), "pending", 5),
        (TelegramServerError(method=METHOD, message="synthetic"), "pending", 5),
        (TimeoutError(), "pending", 5),
        (TelegramBadRequest(method=METHOD, message="synthetic"), "failed", 0),
        (ValueError("synthetic invalid payload"), "failed", 0),
    ],
)
def test_errors_and_restart_retry(path, error, state, delay):
    async def scenario():
        engine = create_db(path)
        calls = []

        async def send(row):
            calls.append(row["dedupe_key"])
            if len(calls) == 1:
                raise error

        try:
            await put(engine)
            assert await send_one(engine, send, CFG, NOW)
            stored = (await rows(engine))[0]
            assert stored["state"] == state and stored["attempts"] == 1
            assert stored["next_attempt_at"] == NOW + timedelta(seconds=delay)
            await engine.dispose()
            engine = create_db(path)
            assert not await send_one(engine, send, CFG, NOW)
            assert await send_one(engine, send, CFG, NOW + timedelta(seconds=delay)) == (
                state == "pending"
            )
            assert (await rows(engine))[0]["state"] == ("sent" if state == "pending" else state)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_429_cooldown_applies_to_other_recipients_and_survives_restart(path):
    async def scenario():
        engine = create_db(path)
        calls = []

        async def send(row):
            calls.append(row["dedupe_key"])
            if len(calls) == 1:
                raise TelegramRetryAfter(method=METHOD, message="synthetic", retry_after=30)

        try:
            await put(engine)
            await put(engine, draft("two", user=2))
            await send_one(engine, send, CFG, NOW)
            await engine.dispose()
            engine = create_db(path)
            assert not await send_one(engine, send, CFG, NOW + timedelta(seconds=29))
            assert await send_one(engine, send, CFG, NOW + timedelta(seconds=30))
            assert calls == ["one", "two"]
            assert not await send_one(engine, send, CFG, NOW + timedelta(seconds=30))
            assert await send_one(engine, send, CFG, NOW + timedelta(seconds=31))
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_blocked_user_pending_summary_and_future_messages(path):
    async def scenario():
        engine = create_db(path)
        calls = []

        async def send(row):
            calls.append(row["id"])
            raise TelegramForbiddenError(method=METHOD, message="synthetic")

        try:
            async with engine.begin() as connection:
                await connection.execute(
                    users.insert().values(telegram_user_id=1, role="admin", created_at=NOW)
                )
            await put(engine)
            await put(engine, draft("two"))
            night = NOW.replace(hour=20)
            await put(engine, draft("night", kind="record.created"), night)
            assert (await rows(engine))[-1]["state"] == "summary"
            await send_one(engine, send, CFG, NOW)
            assert await put(engine, draft("future"))
            assert all(row["state"] == "blocked" for row in await rows(engine))
            async with engine.connect() as connection:
                assert (await connection.execute(select(users.c.blocked_at))).scalar_one() == NOW
            assert not await send_one(engine, send, CFG, NOW + timedelta(seconds=1))
            assert len(calls) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("accepted", [False, True])
def test_crash_rolls_back_and_exposes_remote_acceptance_window(path, accepted):
    async def scenario():
        engine = create_db(path)
        deliveries = []

        async def crash(row):
            if accepted:
                deliveries.append(row["id"])
            raise asyncio.CancelledError()

        async def send(row):
            deliveries.append(row["id"])

        try:
            await put(engine)
            with pytest.raises(asyncio.CancelledError):
                await send_one(engine, crash, CFG, NOW)
            assert (await rows(engine))[0]["attempts"] == 0
            await engine.dispose()
            engine = create_db(path)
            await send_one(engine, send, CFG, NOW)
            assert len(deliveries) == (2 if accepted else 1)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_telegram_sender_text_and_photo():
    class FakeBot:
        def __init__(self):
            self.calls = []

        async def send_message(self, **kwargs):
            self.calls.append(kwargs)

        async def send_photo(self, **kwargs):
            self.calls.append(kwargs)

    async def scenario():
        bot = FakeBot()
        sender = TelegramSender(bot)  # type: ignore[arg-type]
        await sender({"telegram_user_id": 1, "payload_min": {"text": "synthetic"}})
        await sender({"telegram_user_id": 2, "payload_min": {"photo_file_id": "fake"}})
        assert bot.calls == [
            {"chat_id": 1, "text": "synthetic"},
            {"chat_id": 2, "photo": "fake", "caption": None},
        ]

    asyncio.run(scenario())


def test_retry_after_counts_from_response_time(path):
    async def scenario():
        engine = create_db(path)

        async def send(row):
            raise TelegramRetryAfter(method=METHOD, message="synthetic", retry_after=1)

        try:
            await put(engine)
            response_at = NOW + timedelta(seconds=2)
            await send_one(engine, send, CFG, NOW, clock=lambda: response_at)
            # Short Retry-After still respects the larger configured backoff.
            assert (await rows(engine))[0]["next_attempt_at"] == response_at + timedelta(seconds=5)
        finally:
            await engine.dispose()

    asyncio.run(scenario())
