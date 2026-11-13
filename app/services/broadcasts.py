"""Transactional announcement drafts and confirmation; no Telegram network calls."""

import hashlib
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from app.db.models import broadcasts, kv, outbox, users
from app.domain.models import OutboxDraft, User
from app.domain.segments import SEGMENT_NAMES, broadcast_key, segment
from app.services.access import AccessDenied, AccessService
from app.services.broadcast_delivery import finish_broadcast
from app.services.outbox import enqueue
from app.texts import ru


def draft_key(actor_id: int, request_id: str) -> str:
    if not request_id or len(request_id) > 100:
        raise ValueError("invalid draft request")
    return json.dumps(["broadcast:draft", actor_id, request_id], separators=(",", ":"))


class BroadcastService(AccessService):
    async def members(self, db, segment_name: str) -> list[int]:
        data = (await db.execute(select(users))).mappings().all()
        return segment(
            [User.model_validate({k: r[k] for k in User.model_fields}) for r in data],
            segment_name,
        )

    async def create(
        self,
        actor_id: int,
        request_id: str,
        segment_name: str,
        text: str,
        now: datetime,
        *,
        photo_file_id: str | None = None,
    ) -> int:
        if (
            segment_name not in SEGMENT_NAMES
            or segment_name not in self.cfg.broadcasts.segments
            or not text.strip()
            or len(text) > self.cfg.broadcasts.max_text_length
            or (photo_file_id is not None and (not photo_file_id or len(photo_file_id) > 512))
        ):
            raise ValueError("invalid announcement draft")
        key = draft_key(actor_id, request_id)
        identity = json.dumps([segment_name, text, photo_file_id], ensure_ascii=True)
        digest = hashlib.sha256(identity.encode()).hexdigest()
        async with self.transaction(now) as db:
            await self.authorize(db, actor_id, "broadcast.create")
            old = (await db.execute(select(kv.c.value).where(kv.c.key == key))).scalar_one_or_none()
            if old is not None:
                saved_hash, saved_id = json.loads(old)
                if saved_hash != digest:
                    raise ValueError("conflicting draft replay")
                return int(saved_id)
            broadcast_id = (
                await db.execute(
                    insert(broadcasts)
                    .values(
                        kind="announcement",
                        segment=segment_name,
                        record_ref=None,
                        text=text,
                        photo_file_id=photo_file_id,
                        created_by=actor_id,
                        state="draft",
                    )
                    .returning(broadcasts.c.id)
                )
            ).scalar_one()
            await db.execute(
                kv.insert().values(
                    key=key,
                    value=json.dumps([digest, broadcast_id], separators=(",", ":")),
                )
            )
            count = len(await self.members(db, segment_name))
            preview = ru.BROADCAST_PREVIEW.format(
                id=broadcast_id,
                segment=segment_name,
                count=count,
                text=text,
            )
            markup = {
                "inline_keyboard": [
                    [
                        {
                            "text": ru.BROADCAST_CONFIRM,
                            "callback_data": f"bc:send:{broadcast_id}",
                        },
                        {
                            "text": ru.BROADCAST_CANCEL,
                            "callback_data": f"bc:cancel:{broadcast_id}",
                        },
                    ]
                ]
            }
            if photo_file_id is not None:
                # A second preview item makes the actual attachment visible
                # without overflowing Telegram's 1024-character photo caption.
                await enqueue(
                    db,
                    OutboxDraft(
                        telegram_user_id=actor_id,
                        kind="broadcast.preview.photo",
                        payload_min={"text": text, "photo_file_id": photo_file_id},
                        next_attempt_at=now,
                        dedupe_key=f"broadcast:preview:photo:{broadcast_id}",
                    ),
                    self.cfg,
                    now,
                )
            await enqueue(
                db,
                OutboxDraft(
                    telegram_user_id=actor_id,
                    kind="broadcast.preview",
                    payload_min={"text": preview, "reply_markup": json.dumps(markup)},
                    next_attempt_at=now,
                    dedupe_key=f"broadcast:preview:{broadcast_id}",
                ),
                self.cfg,
                now,
            )
            await self.audit(db, actor_id, "broadcast.created", str(broadcast_id), now)
            return broadcast_id

    async def confirm(self, actor_id: int, broadcast_id: int, now: datetime) -> int:
        if broadcast_id <= 0:
            raise ValueError("invalid broadcast id")
        async with self.transaction(now) as db:
            await self.authorize(db, actor_id, "broadcast.manage")
            item = (
                (await db.execute(select(broadcasts).where(broadcasts.c.id == broadcast_id)))
                .mappings()
                .first()
            )
            if item is None or item["created_by"] != actor_id:
                raise AccessDenied()
            if item["state"] in ("sending", "done"):
                return 0
            if item["state"] != "draft" or item["kind"] != "announcement":
                raise ValueError("announcement not sendable")
            targets = await self.members(db, item["segment"])
            await db.execute(
                broadcasts.update()
                .where(broadcasts.c.id == broadcast_id, broadcasts.c.state == "draft")
                .values(state="sending")
            )
            for recipient in targets:
                await enqueue(
                    db,
                    OutboxDraft(
                        telegram_user_id=recipient,
                        kind="broadcast.news",
                        payload_min={"broadcast_id": broadcast_id},
                        next_attempt_at=now,
                        dedupe_key=broadcast_key(broadcast_id, recipient),
                    ),
                    self.cfg,
                    now,
                )
            await self.audit(db, actor_id, "broadcast.confirmed", str(broadcast_id), now)
            # Handles the legitimate empty-audience case without a fake delivery.
            await finish_broadcast(db, broadcast_id, self.cfg, now)
            return len(targets)

    async def cancel(self, actor_id: int, broadcast_id: int, now: datetime) -> bool:
        if broadcast_id <= 0:
            raise ValueError("invalid broadcast id")
        async with self.transaction(now) as db:
            await self.authorize(db, actor_id, "broadcast.manage")
            item = (
                (await db.execute(select(broadcasts).where(broadcasts.c.id == broadcast_id)))
                .mappings()
                .first()
            )
            if item is None or item["created_by"] != actor_id:
                raise AccessDenied()
            if item["state"] != "draft" or item["kind"] != "announcement":
                return False
            await db.execute(
                broadcasts.update().where(broadcasts.c.id == broadcast_id).values(state="cancelled")
            )
            await self.audit(db, actor_id, "broadcast.cancelled", str(broadcast_id), now)
            return True

    async def subscribe(self, actor_id: int, request_id: str, now: datetime) -> bool:
        """Explicit re-subscription for consenting linked users and teachers."""
        if not request_id or len(request_id) > 100:
            raise ValueError("invalid subscribe request")
        async with self.transaction(now) as db:
            actor = await self.user(db, actor_id)
            await self.authorize(db, actor_id, "menu", actor)
            if actor is None or (actor.role == "client" and actor.consent_at is None):
                raise AccessDenied()
            if actor.news_opt_in:
                return False
            await db.execute(
                users.update()
                .where(users.c.telegram_user_id == actor_id)
                .values(news_opt_in=True)
            )
            await enqueue(
                db,
                OutboxDraft(
                    telegram_user_id=actor_id,
                    kind="broadcast.subscribed",
                    payload_min={"text": ru.BROADCAST_SUBSCRIBED},
                    next_attempt_at=now,
                    dedupe_key=f"broadcast:subscribe:{actor_id}:{request_id}",
                ),
                self.cfg,
                now,
            )
            await self.audit(db, actor_id, "broadcast.subscribed", str(actor_id), now)
            return True

    async def unsubscribe(
        self, actor_id: int, now: datetime, *, broadcast_id: int | None = None
    ) -> bool:
        """Only Telegram's authenticated sender may opt out their own user row."""
        if actor_id <= 0 or (broadcast_id is not None and broadcast_id <= 0):
            raise ValueError("invalid unsubscribe request")
        async with self.transaction(now) as db:
            actor = await self.user(db, actor_id)
            if actor is None:
                raise AccessDenied()
            if broadcast_id is not None:
                proof = (
                    await db.execute(
                        select(outbox.c.id).where(
                            outbox.c.kind == "broadcast.news",
                            outbox.c.dedupe_key == broadcast_key(broadcast_id, actor_id),
                            outbox.c.telegram_user_id == actor_id,
                        )
                    )
                ).scalar_one_or_none()
                if proof is None:
                    raise AccessDenied()
            changed = bool(actor.news_opt_in)
            if changed:
                await db.execute(
                    users.update()
                    .where(users.c.telegram_user_id == actor_id)
                    .values(news_opt_in=False)
                )
                await self.audit(db, actor_id, "broadcast.unsubscribed", str(actor_id), now)
            # No pending announcement can survive opt-out; batch state change is atomic.
            pending = (
                (await db.execute(
                    select(outbox.c.id, outbox.c.payload_min).where(
                        outbox.c.telegram_user_id == actor_id,
                        outbox.c.kind == "broadcast.news",
                        outbox.c.state == "pending",
                    )
                )).all()
            )
            affected: set[int] = set()
            for outbox_id, payload in pending:
                identifier = payload.get("broadcast_id")
                if type(identifier) is not int:
                    raise ValueError("invalid queued broadcast reference")
                affected.add(identifier)
                await db.execute(
                    outbox.update().where(outbox.c.id == outbox_id).values(state="unsubscribed")
                )
            for identifier in sorted(affected):
                await finish_broadcast(db, identifier, self.cfg, now)
            if changed:
                await enqueue(
                    db,
                    OutboxDraft(
                        telegram_user_id=actor_id,
                        kind="broadcast.unsubscribed",
                        payload_min={"text": ru.BROADCAST_UNSUBSCRIBED},
                        next_attempt_at=now,
                        dedupe_key=f"broadcast:off:{actor_id}",
                    ),
                    self.cfg,
                    now,
                )
            return changed
