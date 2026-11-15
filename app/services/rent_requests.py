"""Teacher-initiated R1 task: request only, never creates a YCLIENTS booking."""

from datetime import datetime

from app.domain.tasks import rule, task_key
from app.services.access import AccessDenied, AccessService
from app.services.tasks import create_task


class RentRequestService(AccessService):
    async def request(self, actor_id: int, request_id: str, text: str, now: datetime) -> int:
        if not text.strip() or len(text) > self.cfg.broadcasts.max_text_length:
            raise ValueError("invalid rent request")
        async with self.transaction(now) as db:
            target = await self.user(db, actor_id)
            actor = await self.authorize(db, actor_id, "menu", target)
            if actor.role != "teacher":
                raise AccessDenied()
            draft = rule(
                "R1",
                task_key("teacher-rent", actor_id, request_id),
                self.cfg,
                now,
                body=text,
                requester_id=actor_id,
            )
            assert draft is not None
            task_id = await create_task(db, draft, self.cfg, now)
            await self.audit(db, actor_id, "rent.requested", str(task_id), now)
            return task_id
