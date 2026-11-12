import asyncio
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update, User
from pydantic import SecretStr
from sqlalchemy import select

from app.bot.rbac import build_router
from app.db.engine import create_db
from app.db.models import audit_log, invites, kv, metadata, outbox, users
from app.domain.models import OutboxDraft
from app.jobs.outbox import send_one
from app.runtime import migrate
from app.services.access import AccessDenied
from app.services.invites import InviteService, InviteTokens
from app.services.outbox import enqueue
from app.settings import load_studio

NOW = datetime(2026, 1, 1, 9, tzinfo=UTC)


@pytest.fixture
def path(tmp_path):
    path = tmp_path / "security.db"
    migrate(path)
    return path


def service(engine):
    return InviteService(engine, 1, load_studio(), InviteTokens(SecretStr("synthetic-key")))


async def snapshot(engine):
    async with engine.connect() as db:
        return {
            table.name: (await db.execute(select(table))).all()
            for table in (users, kv, invites, audit_log, outbox)
        }


@pytest.mark.parametrize("operation", ["bootstrap", "issue", "redeem", "revoke"])
def test_audit_failure_rolls_back_every_effect(path, monkeypatch, operation):
    async def scenario():
        engine = create_db(path)
        access = service(engine)
        try:
            if operation != "bootstrap":
                await access.bootstrap(NOW)
            if operation == "redeem":
                await access.issue(1, "admin", "one", NOW)
            if operation == "revoke":
                async with engine.begin() as db:
                    await db.execute(
                        users.insert().values(telegram_user_id=2, role="admin", created_at=NOW)
                    )
            before = await snapshot(engine)
            monkeypatch.setattr(
                access, "audit", AsyncMock(side_effect=RuntimeError("synthetic failure"))
            )
            with pytest.raises(RuntimeError):
                if operation == "bootstrap":
                    await access.bootstrap(NOW)
                elif operation == "issue":
                    await access.issue(1, "admin", "one", NOW)
                elif operation == "revoke":
                    await access.revoke(1, 2, NOW)
                else:
                    nonce = before["outbox"][0]._mapping["payload_min"]["nonce"]
                    await access.redeem(2, access.tokens.derive(nonce), NOW)
            assert await snapshot(engine) == before
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_concurrent_issue_deduplicates_and_conflicting_replay_denied(path):
    async def scenario():
        engines = [create_db(path), create_db(path)]
        services = [service(engine) for engine in engines]
        try:
            await services[0].bootstrap(NOW)
            result = await asyncio.gather(
                *(access.issue(1, "admin", "same", NOW) for access in services)
            )
            assert sorted(result) == [False, True]
            before = await snapshot(engines[0])
            assert len(before["invites"]) == len(before["outbox"]) == 1
            assert len(before["audit_log"]) == 2
            with pytest.raises(AccessDenied):
                await services[0].issue(1, "teacher", "same", NOW)
            assert await snapshot(engines[0]) == before
        finally:
            for engine in engines:
                await engine.dispose()

    asyncio.run(scenario())


def test_revoke_races_with_stale_command_and_stops_future_delivery(path):
    async def scenario():
        engines = [create_db(path), create_db(path)]
        access = service(engines[0])
        cfg = load_studio()
        sent = []

        async def send(row):
            sent.append(row["telegram_user_id"])

        try:
            await access.bootstrap(NOW)
            async with engines[0].begin() as db:
                await db.execute(
                    users.insert().values(telegram_user_id=2, role="admin", created_at=NOW)
                )
            result = await asyncio.gather(
                access.revoke(1, 2, NOW),
                service(engines[1]).menu(2, "stale", NOW),
                return_exceptions=True,
            )
            assert result[0] is True
            assert result[1] == "admin" or isinstance(result[1], AccessDenied)
            async with engines[0].begin() as db:
                await enqueue(
                    db,
                    OutboxDraft(
                        telegram_user_id=2,
                        kind="text",
                        payload_min={"text": "synthetic"},
                        next_attempt_at=NOW,
                        dedupe_key="future",
                    ),
                    cfg,
                    NOW,
                )
                assert (
                    await db.execute(select(outbox.c.state).where(outbox.c.dedupe_key == "future"))
                ).scalar_one() == "blocked"
                # Even a producer bypassing enqueue cannot deliver to a revoked recipient.
                await db.execute(
                    outbox.insert().values(
                        telegram_user_id=2,
                        kind="text",
                        payload_min={"text": "synthetic"},
                        next_attempt_at=NOW,
                        dedupe_key="bypass",
                    )
                )
            for offset in range(3):
                await send_one(engines[0], send, cfg, NOW + timedelta(seconds=offset))
            assert 2 not in sent
            async with engines[0].connect() as db:
                assert not (
                    await db.execute(
                        select(outbox).where(
                            outbox.c.telegram_user_id == 2,
                            outbox.c.state.in_(["pending", "summary", "sent"]),
                        )
                    )
                ).all()
        finally:
            for engine in engines:
                await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("role", ["client", "teacher", "admin"])
def test_invitation_privileges_and_revoked_actors_are_checked_in_service(path, role):
    async def scenario():
        engine = create_db(path)
        access = service(engine)
        try:
            await access.bootstrap(NOW)
            async with engine.begin() as db:
                await db.execute(
                    users.insert().values(telegram_user_id=2, role=role, created_at=NOW)
                )
            for invalid_role in ["admin", "owner", "client", "invalid"]:
                with pytest.raises(AccessDenied):
                    await access.issue(2, invalid_role, "forged", NOW)
            if role == "admin":
                assert await access.issue(2, "teacher", "valid", NOW)
            else:
                with pytest.raises(AccessDenied):
                    await access.issue(2, "teacher", "forged", NOW)
            async with engine.begin() as db:
                await db.execute(
                    users.update().where(users.c.telegram_user_id == 2).values(access_revoked=True)
                )
            with pytest.raises(AccessDenied):
                await access.issue(2, "teacher", "valid", NOW)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_no_plaintext_in_any_table_or_error_log(path, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)

    async def scenario():
        engine = create_db(path)
        access = service(engine)
        bot = Bot("123:synthetic")
        try:
            await access.bootstrap(NOW)
            await access.issue(1, "teacher", "one", NOW)
            async with engine.connect() as db:
                payload = (await db.execute(select(outbox.c.payload_min))).scalar_one()
                token = access.tokens.derive(payload["nonce"])
            await access.redeem(2, token, NOW)
            async with engine.connect() as db:
                for table in metadata.tables.values():
                    assert token.get_secret_value() not in repr(
                        (await db.execute(select(table))).all()
                    )
            assert token.get_secret_value().encode() not in path.read_bytes()
            # Unexpected errors containing the raw token must not reach framework logs.
            monkeypatch.setattr(
                access, "redeem", AsyncMock(side_effect=RuntimeError(token.get_secret_value()))
            )
            dispatcher = Dispatcher()
            dispatcher.include_router(build_router(access, lambda: NOW))
            update = Update(
                update_id=1,
                message=Message(
                    message_id=1,
                    date=NOW,
                    chat=Chat(id=2, type="private"),
                    from_user=User(id=2, first_name="Synthetic", is_bot=False),
                    text=f"/accept {token.get_secret_value()}",
                ),
            )
            await dispatcher.feed_update(bot, update)
            assert token.get_secret_value() not in caplog.text
            assert "RBAC command failed actor=2" in caplog.text
        finally:
            await bot.session.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_tokens_survive_restart_and_require_external_key():
    first = InviteTokens(SecretStr("synthetic-key"))
    restarted = InviteTokens(SecretStr("synthetic-key"))
    different = InviteTokens(SecretStr("another-synthetic-key"))
    token = first.derive("synthetic-nonce")
    assert token == restarted.derive("synthetic-nonce")
    assert token != different.derive("synthetic-nonce")
    assert len("invite_" + token.get_secret_value()) <= 64
    assert token.get_secret_value() not in repr(token)
    assert first.digest(token) != token.get_secret_value()
