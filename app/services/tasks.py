"""Queue writes share the caller's transaction with event/outbox/audit effects.

Actor IDs are trusted transport input, never command arguments. Business source
adapters may call create_task inside their existing BEGIN IMMEDIATE transaction.
"""

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import kv, tasks, users
from app.domain.models import OutboxDraft, TaskDraft
from app.domain.tasks import RULES, rule, task_key
from app.services.access import AccessDenied, AccessService
from app.services.outbox import enqueue
from app.settings import StudioConfig
from app.texts import ru


async def notify_admins(
    db: AsyncConnection, task_id: int, purpose: str, cfg: StudioConfig, now: datetime
) -> None:
    recipients = (
        (
            await db.execute(
                select(users.c.telegram_user_id).where(
                    users.c.role == "admin",
                    users.c.access_revoked.is_(False),
                    users.c.blocked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for recipient in recipients:
        await card(db, task_id, recipient, purpose, cfg, now)


async def card(
    db: AsyncConnection,
    task_id: int,
    recipient: int,
    purpose: str,
    cfg: StudioConfig,
    now: datetime,
) -> None:
    # Texts (especially the temporary R4 phone) are never copied to notifications.
    await enqueue(
        db,
        OutboxDraft(
            telegram_user_id=recipient,
            kind="task.card",
            payload_min={"task_id": task_id},
            next_attempt_at=now,
            dedupe_key=task_key(task_id, recipient, purpose),
        ),
        cfg,
        now,
    )


async def create_task(
    db: AsyncConnection, draft: TaskDraft, cfg: StudioConfig, now: datetime
) -> int:
    """Trusted internal source, no network; retain row/key as a TTL tombstone."""
    if now.utcoffset() is None or draft.code not in RULES:
        raise ValueError("invalid task")
    previous = (
        (await db.execute(select(tasks).where(tasks.c.dedupe_key == draft.dedupe_key)))
        .mappings()
        .first()
    )
    # Keep a non-reversible identity after TTL; never retain a phone-derived digest.
    normalized = draft.model_copy(
        update={"due_at": draft.due_at.astimezone(UTC) if draft.due_at else None}
    )
    identity = normalized.model_dump_json(exclude={"body"} if draft.code == "R4" else set())
    fingerprint = hashlib.sha256(identity.encode()).hexdigest()
    if previous is not None:
        saved = (
            await db.execute(
                select(kv.c.value).where(kv.c.key == f"task:identity:{previous['id']}")
            )
        ).scalar_one_or_none()
        if saved != fingerprint or (
            draft.code == "R4" and previous["state"] == "open" and previous["body"] != draft.body
        ):
            raise ValueError("task dedupe_key content conflict")
        return previous["id"]
    task_id = (
        await db.execute(
            insert(tasks).values(**draft.model_dump(), last_ping_at=now).returning(tasks.c.id)
        )
    ).scalar_one()
    await db.execute(kv.insert().values(key=f"task:identity:{task_id}", value=fingerprint))
    await notify_admins(db, task_id, "created", cfg, now)
    return task_id


class TaskService(AccessService):
    async def manual(self, actor_id: int, request_id: str, text: str, now: datetime) -> int:
        async with self.transaction(now) as db:
            await self.authorize(db, actor_id, "tasks.create")
            if not text.strip() or len(text) > self.cfg.broadcasts.max_text_length:
                raise ValueError("invalid task text")
            draft = rule(
                "R8",
                task_key(actor_id, request_id),
                self.cfg,
                now,
                body=text,
                requester_id=actor_id,
            )
            assert draft is not None
            previous = (
                await db.execute(select(tasks.c.id).where(tasks.c.dedupe_key == draft.dedupe_key))
            ).scalar_one_or_none()
            task_id = await create_task(db, draft, self.cfg, now)
            if previous is None:
                await self.audit(db, actor_id, "tasks.created", str(task_id), now)
            return task_id

    async def queue(self, actor_id: int, request_id: str, now: datetime) -> list[int]:
        async with self.transaction(now) as db:
            await self.authorize(db, actor_id, "tasks.list")
            ids = list(
                (
                    await db.execute(
                        select(tasks.c.id).where(tasks.c.state != "done").order_by(tasks.c.id)
                    )
                ).scalars()
            )
            for task_id in ids:
                await card(db, task_id, actor_id, task_key("list", request_id), self.cfg, now)
            return ids

    async def take(self, actor_id: int, task_id: int, now: datetime) -> bool:
        async with self.transaction(now) as db:
            await self.authorize(db, actor_id, "tasks.take")
            row = (
                await db.execute(
                    tasks.update()
                    .where(tasks.c.id == task_id, tasks.c.state == "open")
                    .values(state="taken", taken_by=actor_id, taken_at=now)
                    .returning(tasks.c.code)
                )
            ).first()
            if row is None:
                return False
            # R4 may retain a phone only in the open state.
            if row.code == "R4":
                await db.execute(tasks.update().where(tasks.c.id == task_id).values(body=""))
            await self.audit(db, actor_id, "tasks.taken", str(task_id), now)
            await card(db, task_id, actor_id, "taken", self.cfg, now)
            return True

    async def finish(
        self, actor_id: int, task_id: int, now: datetime, *, reply: str | None = None
    ) -> bool:
        async with self.transaction(now) as db:
            actor = await self.authorize(
                db, actor_id, "tasks.reply" if reply is not None else "tasks.done"
            )
            row = (await db.execute(select(tasks).where(tasks.c.id == task_id))).mappings().first()
            if row is None:
                return False
            if row["taken_by"] != actor_id and not actor.is_owner:
                raise AccessDenied()
            if row["state"] != "taken":
                return False
            if reply is not None:
                if (
                    row["code"] != "R3"
                    or not row["requester_id"]
                    or not reply.strip()
                    or len(reply) > self.cfg.broadcasts.max_text_length
                ):
                    raise ValueError("invalid reply")
                await enqueue(
                    db,
                    OutboxDraft(
                        telegram_user_id=row["requester_id"],
                        kind="task.reply",
                        payload_min={
                            "text": ru.TASK_REPLY_TEXT.format(text=reply),
                            "task_id": task_id,
                        },
                        next_attempt_at=now,
                        dedupe_key=task_key(task_id, "reply"),
                    ),
                    self.cfg,
                    now,
                )
            result = await db.execute(
                tasks.update()
                .where(tasks.c.id == task_id, tasks.c.state == "taken")
                .values(state="done", done_at=now, body="" if row["code"] == "R4" else row["body"])
                .returning(tasks.c.id)
            )
            if result.scalar_one_or_none() is None:
                return False
            await self.audit(
                db,
                actor_id,
                "tasks.replied" if reply is not None else "tasks.done",
                str(task_id),
                now,
            )
            await card(db, task_id, actor_id, "done", self.cfg, now)
            return True

    async def reply_prompt(
        self, actor_id: int, task_id: int, request_id: str, now: datetime
    ) -> None:
        async with self.transaction(now) as db:
            actor = await self.authorize(db, actor_id, "tasks.reply")
            row = (await db.execute(select(tasks).where(tasks.c.id == task_id))).mappings().first()
            if (
                row is None
                or row["code"] != "R3"
                or row["state"] != "taken"
                or (row["taken_by"] != actor_id and not actor.is_owner)
            ):
                raise AccessDenied()
            await enqueue(
                db,
                OutboxDraft(
                    telegram_user_id=actor_id,
                    kind="task.reply_help",
                    payload_min={"task_id": task_id, "text": ru.TASK_REPLY_HELP.format(id=task_id)},
                    next_attempt_at=now,
                    dedupe_key=task_key(task_id, actor_id, "prompt", request_id),
                ),
                self.cfg,
                now,
            )
