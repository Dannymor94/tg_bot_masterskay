"""Authorized, bounded SQLite snapshots for Telegram's native Rich UI.

Only call after RBAC, under the outbox sender's BEGIN IMMEDIATE lock. The view
model is transient: none of these task bodies or customer details are stored in
the queued UI outbox payload.
"""

from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import broadcasts, tasks, users
from app.domain.models import User

PAGE_SIZE = 4
QUESTION_LIMIT = 6
TEAM_LIMIT = 12
BROADCAST_LIMIT = 6
BOOKING_PREFIX = "Запрос записи на занятие: "


def screen_parts(screen: str) -> tuple[str, str, int]:
    """Return a validated base, filter and page; no arbitrary SQL identifiers."""
    parts = screen.split(":")
    if (
        len(parts) == 3
        and parts[0] == "tasks"
        and parts[1] in ("active", "open", "taken", "done")
        and parts[2].isascii()
        and parts[2].isdecimal()
        and len(parts[2]) <= 4
    ):
        page = int(parts[2])
        if page <= 500:
            return "tasks", parts[1], page
    if len(parts) == 3 and parts[0] in ("broadcasts", "team") and parts[1] == "detail":
        if (
            parts[2].isascii()
            and parts[2].isdecimal()
            and len(parts[2]) <= 19
            and 0 < int(parts[2]) <= 2**63 - 1
        ):
            return parts[0], "detail", int(parts[2])
    if len(parts) == 1:
        return screen, "", 0
    raise ValueError("invalid Rich UI screen")


def _task_summary(task: Mapping[Any, Any], actor: User) -> dict[str, Any]:
    state = task["state"]
    # R4 can hold a temporary phone number. Never put it in a Telegram UI.
    body = "Требуется повторная проверка привязки" if task["code"] == "R4" else task["body"]
    return {
        "id": int(task["id"]),
        "code": str(task["code"]),
        "state": str(state),
        "title": str(task["title"]),
        "body": str(body)[:160],
        "due": task["due_at"].isoformat()[:16] if task["due_at"] else "",
        "take": state == "open" and task["code"] != "R4",
        "finish": (
            state == "taken"
            and task["code"] != "R4"
            and (task["taken_by"] == actor.telegram_user_id or actor.is_owner)
        ),
        "reply": task["code"] == "R3",
    }


async def view_data(db: AsyncConnection, actor: User, screen: str) -> dict[str, Any]:
    """Compute the current display after authentication and current role check."""
    base, mode, page = screen_parts(screen)
    if base == "tasks" and actor.role == "admin":
        predicate = (
            tasks.c.state.in_(["open", "taken"])
            if mode in ("", "active")
            else tasks.c.state == mode
        )
        count = int(
            (
                await db.execute(select(func.count()).select_from(tasks).where(predicate))
            ).scalar_one()
        )
        found = (
            (
                await db.execute(
                    select(tasks)
                    .where(predicate)
                    .order_by(tasks.c.id.desc())
                    .offset(page * PAGE_SIZE)
                    .limit(PAGE_SIZE)
                )
            )
            .mappings()
            .all()
        )
        return {
            "tasks": [_task_summary(row, actor) for row in found],
            "count": count,
            "page": page,
            "pages": max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE),
            "filter": mode or "active",
        }
    if base == "question" and actor.role in ("client", "teacher"):
        found = (
            (
                await db.execute(
                    select(tasks.c.id, tasks.c.state, tasks.c.due_at)
                    .where(tasks.c.requester_id == actor.telegram_user_id, tasks.c.code == "R3")
                    .order_by(tasks.c.id.desc())
                    .limit(QUESTION_LIMIT)
                )
            )
            .mappings()
            .all()
        )
        return {"questions": [{"id": int(row["id"]), "state": str(row["state"])} for row in found]}
    if base == "bookings" and actor.role == "client":
        # Booking requests are R3 tasks, not confirmed YCLIENTS bookings.
        found = (
            (
                await db.execute(
                    select(tasks.c.id, tasks.c.state, tasks.c.body)
                    .where(
                        tasks.c.requester_id == actor.telegram_user_id,
                        tasks.c.code == "R3",
                        tasks.c.body.startswith(BOOKING_PREFIX),
                    )
                    .order_by(tasks.c.id.desc())
                    .limit(QUESTION_LIMIT)
                )
            )
            .mappings()
            .all()
        )
        return {
            "booking_requests": [
                {
                    "id": int(row["id"]),
                    "state": str(row["state"]),
                    "text": str(row["body"])[len(BOOKING_PREFIX) :][:160],
                }
                for row in found
            ]
        }
    if base == "broadcasts" and actor.role == "admin":
        if mode == "detail":
            row = (
                (
                    await db.execute(
                        select(broadcasts).where(
                            broadcasts.c.id == page,
                            broadcasts.c.created_by == actor.telegram_user_id,
                            broadcasts.c.kind == "announcement",
                        )
                    )
                )
                .mappings()
                .first()
            )
            return (
                {
                    "broadcast": {
                        "id": int(row["id"]),
                        "state": str(row["state"]),
                        "segment": str(row["segment"]),
                        "text": str(row["text"])[:1024],
                        "sent": int(row["sent"]),
                        "failed": int(row["failed"]),
                    }
                }
                if row
                else {"broadcast": None}
            )
        found = (
            (
                await db.execute(
                    select(broadcasts)
                    .where(
                        broadcasts.c.created_by == actor.telegram_user_id,
                        broadcasts.c.kind == "announcement",
                    )
                    .order_by(broadcasts.c.id.desc())
                    .limit(BROADCAST_LIMIT)
                )
            )
            .mappings()
            .all()
        )
        return {
            "broadcasts": [
                {
                    "id": int(row["id"]),
                    "state": str(row["state"]),
                    "segment": str(row["segment"]),
                    "sent": int(row["sent"]),
                }
                for row in found
            ]
        }
    if base == "team" and actor.role == "admin":
        if mode == "detail" and actor.is_owner:
            target = (
                (await db.execute(select(users).where(users.c.telegram_user_id == page)))
                .mappings()
                .first()
            )
            if target is None or target["is_owner"] or target["role"] not in ("admin", "teacher"):
                return {"member": None}
            return {
                "member": {
                    "id": int(target["telegram_user_id"]),
                    "role": str(target["role"]),
                    "revoked": bool(target["access_revoked"]),
                }
            }
        if actor.is_owner:
            found = (
                (
                    await db.execute(
                        select(users.c.telegram_user_id, users.c.role, users.c.access_revoked)
                        .where(users.c.role.in_(["admin", "teacher"]), users.c.is_owner.is_(False))
                        .order_by(users.c.telegram_user_id)
                        .limit(TEAM_LIMIT)
                    )
                )
                .mappings()
                .all()
            )
            return {
                "members": [
                    {
                        "id": int(row["telegram_user_id"]),
                        "role": str(row["role"]),
                        "revoked": bool(row["access_revoked"]),
                    }
                    for row in found
                ]
            }
        return {"members": []}
    if base in ("home", "reports") and actor.role == "admin":
        values = {}
        for state in ("open", "taken", "done"):
            values[state] = int(
                (
                    await db.execute(
                        select(func.count()).select_from(tasks).where(tasks.c.state == state)
                    )
                ).scalar_one()
            )
        if actor.is_owner:
            values["subscribers"] = int(
                (
                    await db.execute(
                        select(func.count())
                        .select_from(users)
                        .where(users.c.news_opt_in.is_(True), users.c.access_revoked.is_(False))
                    )
                ).scalar_one()
            )
        return {"counts": values}
    return {}
