"""Render current authorized task cards only inside the sender's DB lock."""

from collections.abc import Mapping
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import tasks, users
from app.domain.models import User
from app.domain.rbac import can
from app.settings import StudioConfig
from app.texts import ru


async def render_card(
    db: AsyncConnection, row: Mapping[str, Any], cfg: StudioConfig
) -> dict[str, Any] | None:
    actor_row = (
        (await db.execute(select(users).where(users.c.telegram_user_id == row["telegram_user_id"])))
        .mappings()
        .first()
    )
    if actor_row is None:
        return None
    actor = User.model_validate({key: actor_row[key] for key in User.model_fields})
    if not can(actor, "tasks.list"):
        return None
    task = (
        (await db.execute(select(tasks).where(tasks.c.id == row["payload_min"]["task_id"])))
        .mappings()
        .first()
    )
    if task is None or not task["title"]:
        return None
    due = task["due_at"]
    payload = {
        "text": ru.TASK_CARD.format(
            id=task["id"],
            code=task["code"],
            state=task["state"],
            title=task["title"],
            body=task["body"],
            due=due.astimezone(ZoneInfo(cfg.studio.timezone)).isoformat()
            if due
            else ru.TASK_NO_DUE,
        ),
        "task_id": task["id"],
        "take": task["state"] == "open",
        "finish": task["state"] == "taken"
        and (task["taken_by"] == actor.telegram_user_id or actor.is_owner),
        "reply": task["code"] == "R3",
    }
    return {**row, "payload_min": payload}
