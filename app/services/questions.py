"""Client/teacher questions reuse the atomic R3 queue and durable outbox."""

from datetime import datetime

from sqlalchemy import select

from app.db.models import tasks
from app.domain.models import OutboxDraft
from app.domain.tasks import rule, task_key
from app.services.access import AccessService
from app.services.outbox import enqueue
from app.services.tasks import create_task
from app.texts import ru


class QuestionService(AccessService):
    async def ask(self, actor_id: int, request_id: str, text: str, now: datetime) -> int:
        """Return a stable R3 task ID for a single private Telegram message."""
        body = text.strip()
        if (
            not body
            or len(body) > self.cfg.broadcasts.max_text_length
            or not request_id
            or len(request_id) > 100
        ):
            raise ValueError("invalid question")
        source = task_key("question", actor_id, request_id)
        key = task_key("R3", source)
        async with self.transaction(now) as db:
            actor = await self.user(db, actor_id)
            await self.authorize(db, actor_id, "questions.ask", actor)
            existing = (
                (await db.execute(select(tasks).where(tasks.c.dedupe_key == key)))
                .mappings()
                .first()
            )
            if existing is not None:
                if (
                    existing["code"] != "R3"
                    or existing["requester_id"] != actor_id
                    or (existing["body"] and existing["body"] != body)
                ):
                    raise ValueError("question replay content conflict")
                task_id = int(existing["id"])
            else:
                draft = rule("R3", source, self.cfg, now, body=body, requester_id=actor_id)
                assert draft is not None
                task_id = await create_task(db, draft, self.cfg, now)
                await self.audit(db, actor_id, "questions.created", str(task_id), now)
            await enqueue(
                db,
                OutboxDraft(
                    telegram_user_id=actor_id,
                    kind="question.ack",
                    payload_min={"text": ru.QUESTION_RECEIVED.format(id=task_id)},
                    next_attempt_at=now,
                    dedupe_key=task_key("question", "ack", actor_id, request_id),
                ),
                self.cfg,
                now,
            )
            return task_id
