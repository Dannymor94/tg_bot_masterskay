"""Authorization and effects share a SQLite write transaction."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db.models import audit_log, kv, outbox, users
from app.domain.models import OutboxDraft, Role, User
from app.domain.rbac import can
from app.services.outbox import enqueue
from app.services.telegram_recipients import is_blocked
from app.settings import StudioConfig
from app.texts import ru


class AccessDenied(Exception):
    def __init__(self):
        super().__init__(ru.ACCESS_DENIED)


def effect_key(actor: int, request: str, action: str) -> str:
    return json.dumps(["rbac", actor, request, action], separators=(",", ":"))


class AccessService:
    def __init__(self, engine: AsyncEngine, owner_id: int, cfg: StudioConfig):
        if owner_id <= 0:
            raise AccessDenied()
        self.engine, self.owner_id, self.cfg = engine, owner_id, cfg

    @asynccontextmanager
    async def transaction(self, now: datetime) -> AsyncIterator[AsyncConnection]:
        if now.utcoffset() is None:
            raise ValueError("aware datetime required")
        async with self.engine.connect() as db:
            await db.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def user(self, db: AsyncConnection, user_id: int) -> User | None:
        row = (
            (await db.execute(select(users).where(users.c.telegram_user_id == user_id)))
            .mappings()
            .first()
        )
        if row is None:
            return None
        return User.model_validate({key: row[key] for key in User.model_fields})

    async def authorize(
        self, db: AsyncConnection, actor_id: int, action: str, target: User | None = None
    ) -> User:
        actor = await self.user(db, actor_id)
        if actor is None and action == "invite.redeem":
            actor = User(telegram_user_id=actor_id, role="client")
        if (
            actor is None
            or (actor.is_owner and actor_id != self.owner_id)
            or await is_blocked(db, actor_id)
            or not can(actor, action, target)
        ):
            raise AccessDenied()
        return actor

    async def audit(
        self, db: AsyncConnection, actor: int, action: str, target: str, now: datetime
    ) -> None:
        await db.execute(
            audit_log.insert().values(actor=actor, action=action, target=target, created_at=now)
        )

    async def bootstrap(self, now: datetime) -> None:
        async with self.transaction(now) as db:
            pinned = (
                await db.execute(select(kv.c.value).where(kv.c.key == "rbac:owner_id"))
            ).scalar_one_or_none()
            owners = (
                (
                    await db.execute(
                        select(users.c.telegram_user_id).where(users.c.is_owner.is_(True))
                    )
                )
                .scalars()
                .all()
            )
            if (pinned is not None and pinned != str(self.owner_id)) or any(
                i != self.owner_id for i in owners
            ):
                raise AccessDenied()
            current = await self.user(db, self.owner_id)
            if current and current.access_revoked:
                raise AccessDenied()
            if pinned is None:
                await db.execute(kv.insert().values(key="rbac:owner_id", value=str(self.owner_id)))
            if current and current.is_owner and current.role == "admin":
                return
            if current is None:
                await db.execute(
                    users.insert().values(
                        telegram_user_id=self.owner_id, role="admin", is_owner=True, created_at=now
                    )
                )
            else:
                await db.execute(
                    users.update()
                    .where(users.c.telegram_user_id == self.owner_id)
                    .values(role="admin", is_owner=True)
                )
            await self.audit(db, self.owner_id, "owner.bootstrap", str(self.owner_id), now)

    async def menu(self, actor_id: int, request_id: str, now: datetime) -> Role:
        async with self.transaction(now) as db:
            target = await self.user(db, actor_id)
            actor = await self.authorize(db, actor_id, "menu", target)
            await enqueue(
                db,
                OutboxDraft(
                    telegram_user_id=actor_id,
                    kind="rbac.menu",
                    payload_min={"text": ru.ROLE_MENU[actor.role]},
                    next_attempt_at=now,
                    dedupe_key=effect_key(actor_id, request_id, "menu"),
                ),
                self.cfg,
                now,
            )
            return actor.role

    async def revoke(self, actor_id: int, target_id: int, now: datetime) -> bool:
        async with self.transaction(now) as db:
            target = await self.user(db, target_id)
            await self.authorize(db, actor_id, "access.revoke", target)
            if target is None:
                raise AccessDenied()
            if target.access_revoked:
                return False
            await db.execute(
                users.update()
                .where(users.c.telegram_user_id == target_id)
                .values(access_revoked=True)
            )
            await db.execute(
                outbox.update()
                .where(
                    outbox.c.telegram_user_id == target_id,
                    outbox.c.state.in_(["pending", "summary"]),
                )
                .values(state="cancelled")
            )
            await self.audit(db, actor_id, "access.revoked", str(target_id), now)
            return True
