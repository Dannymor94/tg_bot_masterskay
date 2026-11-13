"""R4 boundary for T-203: TaskDraft schema, same transaction, no PII notifications.

R4 body is exclusively a request_contact number while open/taken. Generic task
closing must scrub body; only ClientService.retry may perform a binding.
"""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import tasks, users
from app.domain.models import User
from app.domain.rbac import can
from app.texts import ru

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
        minutes = self.service.cfg.tasks.due_minutes["R4_link_check"]
        result = await db.execute(
            insert(tasks)
            .values(
                code="R4",
                title=ru.CLIENT_R4_TITLE,
                body=phone.get_secret_value(),
                requester_id=user_id,
                ref=str(user_id),
                state="open",
                due_at=now + timedelta(minutes=minutes) if minutes else None,
                dedupe_key=f"client:r4:{user_id}",
            )
            .on_conflict_do_nothing(index_elements=[tasks.c.dedupe_key])
            .returning(tasks.c.id)
        )
        task_id = result.scalar_one_or_none()
        if task_id is None:
            return
        target = await self.service.user(db, user_id)
        admins = (await db.execute(select(users).where(users.c.role == "admin"))).mappings()
        for row in admins:
            admin = User.model_validate({key: row[key] for key in User.model_fields})
            if can(admin, "client.retry", target):
                await self.service.reply(
                    db,
                    admin.telegram_user_id,
                    f"r4:{task_id}",
                    ru.CLIENT_R4_NOTICE.format(task_id=task_id),
                    now,
                    {
                        "inline_keyboard": [
                            [
                                {
                                    "text": ru.CLIENT_RETRY,
                                    "callback_data": f"cl:retry:{task_id}",
                                }
                            ]
                        ]
                    },
                )

    async def resolve(self, db: AsyncConnection, user_id: int, now: datetime) -> None:
        await db.execute(
            tasks.update()
            .where(
                tasks.c.code == "R4",
                tasks.c.requester_id == user_id,
            )
            .values(body="", state="done", done_at=now)
        )
