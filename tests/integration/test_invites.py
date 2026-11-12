import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.db.engine import create_db
from app.db.models import audit_log, invites, outbox, users
from app.runtime import migrate
from app.services.access import AccessDenied
from app.services.invites import InviteService, InviteTokens
from app.settings import load_studio

NOW = datetime(2026, 1, 1, 9, tzinfo=UTC)


@pytest.fixture
def invitation_db(tmp_path):
    path = tmp_path / "invites.db"
    migrate(path)
    return path


async def issued_token(db, tokens):
    nonce = (
        await db.execute(select(outbox.c.payload_min).where(outbox.c.kind == "rbac.invite"))
    ).scalar_one()["nonce"]
    return tokens.derive(nonce)


@pytest.mark.parametrize("role", ["admin", "teacher"])
def test_issue_redeem_repeat_and_no_plaintext(invitation_db, caplog, role):
    async def scenario():
        engine = create_db(invitation_db)
        tokens = InviteTokens(SecretStr("synthetic-bot-secret"))
        service = InviteService(engine, 1, load_studio(), tokens)
        try:
            await service.bootstrap(NOW)
            assert await service.issue(1, role, "message-1", NOW)
            assert not await service.issue(1, role, "message-1", NOW)
            async with engine.connect() as db:
                token = await issued_token(db, tokens)
                rows = repr((await db.execute(select(outbox))).all()) + repr(
                    (await db.execute(select(invites))).all()
                )
                assert token.get_secret_value() not in rows
            assert (await service.redeem(2, token, NOW)).role == role
            with pytest.raises(AccessDenied):
                await service.redeem(2, token, NOW)
            if role == "teacher":
                with pytest.raises(AccessDenied):
                    await service.menu(2, "pending-teacher", NOW)
            async with engine.connect() as db:
                row = (await db.execute(select(invites))).mappings().one()
                assert row["used_by"] == 2 and row["used_at"] == NOW
                assert row["expires_at"] == NOW + timedelta(hours=24)
                actions = (await db.execute(select(audit_log.c.action))).scalars().all()
                assert actions == [
                    "owner.bootstrap",
                    "invite.issued",
                    "role.changed",
                    "invite.used",
                ]
            assert token.get_secret_value() not in caplog.text
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_two_simultaneous_redeems(invitation_db):
    async def scenario():
        engines = [create_db(invitation_db), create_db(invitation_db)]
        tokens = InviteTokens(SecretStr("synthetic-bot-secret"))
        services = [InviteService(engine, 1, load_studio(), tokens) for engine in engines]
        try:
            await services[0].bootstrap(NOW)
            await services[0].issue(1, "admin", "one", NOW)
            async with engines[0].connect() as db:
                token = await issued_token(db, tokens)
            result = await asyncio.gather(
                services[0].redeem(2, token, NOW),
                services[1].redeem(3, token, NOW),
                return_exceptions=True,
            )
            assert sum(isinstance(item, AccessDenied) for item in result) == 1
            async with engines[0].connect() as db:
                assert len((await db.execute(select(users))).all()) == 2
                assert len((await db.execute(select(audit_log))).all()) == 4
        finally:
            for engine in engines:
                await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure", ["expired", "tampered", "self", "revoked_issuer", "revoked_user"]
)
def test_invalid_invites_have_no_effect(invitation_db, failure):
    async def scenario():
        engine = create_db(invitation_db)
        tokens = InviteTokens(SecretStr("synthetic-bot-secret"))
        service = InviteService(engine, 1, load_studio(), tokens)
        try:
            await service.bootstrap(NOW)
            async with engine.begin() as db:
                await db.execute(
                    users.insert().values(telegram_user_id=2, role="admin", created_at=NOW)
                )
            await service.issue(2, "teacher", "one", NOW)
            async with engine.connect() as db:
                token = await issued_token(db, tokens)
            actor, now = 3, NOW
            if failure == "expired":
                now += timedelta(hours=24)
            if failure == "tampered":
                token = SecretStr(token.get_secret_value() + "x")
            if failure == "self":
                actor = 2
            if failure == "revoked_issuer":
                await service.revoke(1, 2, NOW)
            if failure == "revoked_user":
                async with engine.begin() as db:
                    await db.execute(
                        users.insert().values(
                            telegram_user_id=3, role="teacher", access_revoked=True, created_at=NOW
                        )
                    )
            with pytest.raises(AccessDenied):
                await service.redeem(actor, token, now)
            async with engine.connect() as db:
                assert (await db.execute(select(invites.c.used_at))).scalar_one() is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
