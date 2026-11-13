"""R4 boundary for T-203: TaskDraft schema, same transaction, no PII notifications.

R4 body holds a request_contact number only while open. Generic Take/Done
are forbidden; only verified ClientService binding may resolve it.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import tasks
from app.domain.tasks import rule
from app.services.tasks import create_task

if TYPE_CHECKING:
    from app.services.client_onboarding import ClientService


class ClientTasks:
    def __init__(self, service: "ClientService"):
        self.service = service

    async def get(self, db: AsyncConnection, task_id: int) -> dict[str, Any] | None:
        row = (
            (await db.execute(select(tasks).where(tasks.c.id == task_id, tasks.c.code == "R4")))
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def open(
        self, db: AsyncConnection, user_id: int, phone: SecretStr, now: datetime
    ) -> None:
        draft = rule(
            "R4",
            f"client:{user_id}",
            self.service.cfg,
            now,
            body=phone.get_secret_value(),
            requester_id=user_id,
            ref=str(user_id),
        )
        assert draft is not None
        await create_task(db, draft, self.service.cfg, now)

    async def resolve(self, db: AsyncConnection, user_id: int, now: datetime) -> None:
        await db.execute(
            tasks.update()
            .where(
                tasks.c.code == "R4",
                tasks.c.requester_id == user_id,
            )
            .values(body="", state="done", done_at=now)
        )
