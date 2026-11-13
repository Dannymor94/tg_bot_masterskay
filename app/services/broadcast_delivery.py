"""Recheck news subscription at actual send time and finalize outcome counts."""

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import broadcasts, outbox, users
from app.domain.models import OutboxDraft, User
from app.domain.segments import segment
from app.services.outbox import enqueue
from app.settings import StudioConfig
from app.texts import ru


async def render_news(
    db: AsyncConnection, row: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, str]:
    """Return rendered row or terminal skip reason, never raw data to outbox."""
    identifier = row["payload_min"].get("broadcast_id")
    if type(identifier) is not int or identifier <= 0:
        return None, "failed"
    item = (
        (await db.execute(select(broadcasts).where(broadcasts.c.id == identifier)))
        .mappings()
        .first()
    )
    if item is None or item["state"] != "sending" or item["kind"] != "announcement":
        return None, "cancelled"
    person = (
        (
            await db.execute(
                select(users).where(users.c.telegram_user_id == row["telegram_user_id"])
            )
        )
        .mappings()
        .first()
    )
    if person is None:
        return None, "cancelled"
    actor = User.model_validate({name: person[name] for name in User.model_fields})
    if not actor.news_opt_in:
        return None, "unsubscribed"
    try:
        eligible = actor.telegram_user_id in segment([actor], item["segment"])
    except ValueError:
        # A manually corrupted/retired segment must never crash the sender.
        return None, "cancelled"
    if not eligible:
        # Role/segment changes are suppressed, but are not an explicit opt-out.
        return None, "cancelled"
    markup = {
        "inline_keyboard": [
            [
                {
                    "text": ru.BROADCAST_UNSUBSCRIBE,
                    "callback_data": f"bc:off:{identifier}",
                }
            ]
        ]
    }
    payload: dict[str, Any] = {
        "text": item["text"],
        "reply_markup": json.dumps(markup),
        "broadcast_id": identifier,
    }
    if item["photo_file_id"]:
        payload["photo_file_id"] = item["photo_file_id"]
    return {**row, "payload_min": payload}, "sent"


async def finish_broadcast(
    db: AsyncConnection, identifier: int, cfg: StudioConfig, now: datetime
) -> bool:
    """Update aggregate counters and enqueue one summary after all terminal rows."""
    item = (
        (await db.execute(select(broadcasts).where(broadcasts.c.id == identifier)))
        .mappings()
        .first()
    )
    if item is None or item["state"] != "sending":
        return False
    counts = {
        str(state): int(count)
        for state, count in (
            await db.execute(
                select(outbox.c.state, func.count())
                .where(
                    outbox.c.kind == "broadcast.news",
                    outbox.c.dedupe_key.like(f"{identifier}:%"),
                )
                .group_by(outbox.c.state)
            )
        ).all()
    }
    sent = counts.get("sent", 0)
    blocked = counts.get("blocked", 0)
    unsubscribed = counts.get("unsubscribed", 0)
    failed = counts.get("failed", 0) + counts.get("cancelled", 0)
    await db.execute(
        broadcasts.update()
        .where(broadcasts.c.id == identifier)
        .values(sent=sent, failed=failed, blocked=blocked, unsubscribed=unsubscribed)
    )
    if counts.get("pending", 0) or counts.get("summary", 0):
        return False
    await db.execute(
        broadcasts.update().where(broadcasts.c.id == identifier).values(state="done")
    )
    await enqueue(
        db,
        OutboxDraft(
            telegram_user_id=item["created_by"],
            kind="broadcast.summary",
            payload_min={
                "text": ru.BROADCAST_RESULT.format(
                    id=identifier,
                    sent=sent,
                    failed=failed,
                    blocked=blocked,
                    unsubscribed=unsubscribed,
                )
            },
            next_attempt_at=now,
            dedupe_key=f"broadcast:summary:{identifier}",
        ),
        cfg,
        now,
    )
    return True
