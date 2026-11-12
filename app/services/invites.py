"""One-use bearer invitations; plaintext tokens never enter durable storage."""

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import invites, outbox, users
from app.domain.models import OutboxDraft, User
from app.services.access import AccessDenied, AccessService, effect_key
from app.services.outbox import enqueue
from app.settings import StudioConfig
from app.texts import ru


class InviteTokens:
    def __init__(self, key: SecretStr):
        if not key.get_secret_value():
            raise AccessDenied()
        self._key = key

    def derive(self, nonce: str) -> SecretStr:
        # Domain-separated HMAC: nonce alone in outbox cannot reconstruct a token.
        digest = hmac.digest(
            self._key.get_secret_value().encode(),
            b"studio-bot:invite:v1:" + nonce.encode(),
            "sha256",
        )
        return SecretStr(base64.urlsafe_b64encode(digest).decode().rstrip("="))

    @staticmethod
    def digest(token: SecretStr) -> str:
        return hashlib.sha256(token.get_secret_value().encode()).hexdigest()


class InviteService(AccessService):
    def __init__(self, engine: AsyncEngine, owner_id: int, cfg: StudioConfig, tokens: InviteTokens):
        super().__init__(engine, owner_id, cfg)
        self.tokens = tokens

    async def issue(self, actor_id: int, role: str, request_id: str, now: datetime) -> bool:
        if role not in ("admin", "teacher"):
            raise AccessDenied()
        async with self.transaction(now) as db:
            await self.authorize(db, actor_id, f"invite.{role}")
            key = effect_key(actor_id, request_id, "invite")
            previous = (
                await db.execute(select(outbox.c.payload_min).where(outbox.c.dedupe_key == key))
            ).scalar_one_or_none()
            if previous is not None:
                if previous.get("role") != role:
                    raise AccessDenied()
                return False
            nonce = secrets.token_urlsafe(self.cfg.access.token_bytes)
            token_hash = self.tokens.digest(self.tokens.derive(nonce))
            hours = (
                self.cfg.access.admin_invite_hours
                if role == "admin"
                else self.cfg.access.teacher_invite_hours
            )
            await db.execute(
                invites.insert().values(
                    token_hash=token_hash,
                    role=role,
                    created_by=actor_id,
                    expires_at=now + timedelta(hours=hours),
                )
            )
            await self.audit(db, actor_id, "invite.issued", token_hash, now)
            await enqueue(
                db,
                OutboxDraft(
                    telegram_user_id=actor_id,
                    kind="rbac.invite",
                    payload_min={"nonce": nonce, "role": role},
                    next_attempt_at=now,
                    dedupe_key=key,
                ),
                self.cfg,
                now,
            )
            return True

    async def redeem(self, actor_id: int, token: SecretStr, now: datetime) -> User:
        async with self.transaction(now) as db:
            target = await self.user(db, actor_id) or User(telegram_user_id=actor_id, role="client")
            await self.authorize(db, actor_id, "invite.redeem", target)
            token_hash = self.tokens.digest(token)
            invite = (
                (await db.execute(select(invites).where(invites.c.token_hash == token_hash)))
                .mappings()
                .first()
            )
            if (
                invite is None
                or invite["used_at"] is not None
                or invite["used_by"] is not None
                or invite["expires_at"] <= now
                or invite["created_by"] == actor_id
                or invite["role"] not in ("admin", "teacher")
            ):
                raise AccessDenied()
            await self.authorize(db, invite["created_by"], f"invite.{invite['role']}")
            if await self.user(db, actor_id) is None:
                await db.execute(
                    users.insert().values(telegram_user_id=actor_id, role="client", created_at=now)
                )
            values = {"role": invite["role"]}
            if invite["role"] == "teacher":
                values.update(teacher_kind=None, yclients_staff_id=None)
            await db.execute(
                users.update().where(users.c.telegram_user_id == actor_id).values(**values)
            )
            await db.execute(
                invites.update()
                .where(invites.c.token_hash == token_hash)
                .values(used_by=actor_id, used_at=now)
            )
            await self.audit(db, actor_id, "role.changed", str(actor_id), now)
            await self.audit(db, actor_id, "invite.used", token_hash, now)
            await enqueue(
                db,
                OutboxDraft(
                    telegram_user_id=actor_id,
                    kind="rbac.accepted",
                    payload_min={"text": ru.INVITE_ACCEPTED[invite["role"]]},
                    next_attempt_at=now,
                    dedupe_key=f"rbac:accepted:{token_hash}",
                ),
                self.cfg,
                now,
            )
            result = await self.user(db, actor_id)
            assert result is not None
            return result
