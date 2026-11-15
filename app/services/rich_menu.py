"""Authenticated menu navigation, committed atomically to the existing outbox."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import kv, users
from app.domain.models import OutboxDraft, User
from app.domain.rbac import can
from app.services.access import AccessDenied, AccessService
from app.services.outbox import enqueue
from app.ui.rich_menu import can_open


class RichMenuService(AccessService):
    async def show(
        self, actor_id: int, request_id: str, screen: str, now: datetime, *, message_id: int = 0
    ) -> None:
        if not request_id or len(request_id) > 100 or message_id < 0:
            raise ValueError("invalid UI request")
        async with self.transaction(now) as db:
            target = await self.user(db, actor_id)
            actor = await self.authorize(db, actor_id, "menu", target)
            if not can_open(actor, screen):
                raise AccessDenied()
            await enqueue(
                db,
                OutboxDraft(
                    telegram_user_id=actor_id,
                    kind="ui.rich.edit" if message_id else "ui.rich.new",
                    payload_min={"screen": screen, "message_id": message_id},
                    next_attempt_at=now,
                    dedupe_key=f"ui-rich:{actor_id}:{request_id}:{screen}",
                ),
                self.cfg,
                now,
            )


async def render_menu_delivery(
    db: AsyncConnection, row: dict[str, object]
) -> dict[str, object] | None:
    """Re-authorize at actual delivery time, not only at callback enqueue time."""
    actor_id = row["telegram_user_id"]
    if type(actor_id) is not int or actor_id <= 0:
        return None
    saved = (
        (await db.execute(select(users).where(users.c.telegram_user_id == actor_id)))
        .mappings()
        .first()
    )
    if saved is None:
        return None
    actor = User.model_validate({key: saved[key] for key in User.model_fields})
    if actor.is_owner:
        pinned = (
            await db.execute(select(kv.c.value).where(kv.c.key == "rbac:owner_id"))
        ).scalar_one_or_none()
        if pinned != str(actor_id):
            return None
    payload = row["payload_min"]
    if not isinstance(payload, dict):
        return None
    screen = payload.get("screen")
    if not can(actor, "menu", actor) or not isinstance(screen, str) or not can_open(actor, screen):
        return None
    message_id = payload.get("message_id")
    if (
        type(message_id) is not int
        or message_id < 0
        or (row["kind"] == "ui.rich.edit" and message_id == 0)
    ):
        return None
    # Rendering uses only role metadata from trusted user rows, never callback data.
    return {
        **row,
        "payload_min": {
            "screen": screen,
            "message_id": message_id,
            "role": actor.role,
            "is_owner": actor.is_owner,
            "news_opt_in": actor.news_opt_in,
        },
    }
