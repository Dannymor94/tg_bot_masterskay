"""Authorized, bounded SQLite snapshots for Telegram's native Rich UI.

Only call after RBAC, under the outbox sender's BEGIN IMMEDIATE lock. The view
model is transient: none of these task bodies or customer details are stored in
the queued UI outbox payload.
"""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import broadcasts, outbox, tasks, users
from app.domain.calendar import calendar_model
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
        len(parts) == 2
        and parts[0] == "reports"
        and parts[1]
        in (
            "tasks",
            "news",
            "people",
            "operations",
        )
    ):
        return "reports", parts[1], 0
    if len(parts) in (2, 3) and parts[0] in ("schedule", "rent"):
        if len(parts) == 2 and len(parts[1]) == 7 and parts[1][4] == "-":
            try:
                month = date.fromisoformat(parts[1] + "-01")
            except ValueError:
                pass
            else:
                if 2020 <= month.year <= 2100:
                    return parts[0], "month", month.year * 100 + month.month
        if len(parts) == 3 and parts[1] == "day":
            try:
                selected = date.fromisoformat(parts[2])
            except ValueError:
                pass
            else:
                if 2020 <= selected.year <= 2100 and selected.isoformat() == parts[2]:
                    return parts[0], "day", int(parts[2].replace("-", ""))
        raise ValueError("invalid calendar screen")
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


async def view_data(
    db: AsyncConnection,
    actor: User,
    screen: str,
    *,
    today: date | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Compute the current display after authentication and current role check."""
    base, mode, page = screen_parts(screen)
    if base in ("schedule", "rent") and (
        (base == "schedule" and actor.role == "client")
        or (base == "rent" and actor.role == "teacher")
    ):
        day = today or datetime.now(UTC).date()
        if mode == "month":
            month = date(page // 100, page % 100, 1)
            selected = None
        elif mode == "day":
            selected = date(page // 10000, page // 100 % 100, page % 100)
            month = selected.replace(day=1)
        else:
            month = day.replace(day=1)
            selected = None
        try:
            model = calendar_model(day, month, selected)
        except ValueError:
            model = calendar_model(day, day.replace(day=1))
            model["notice"] = "Дата вне доступного периода выбора."
        return {"calendar": model}
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
    if base == "reports" and actor.is_owner and actor.role == "admin":
        result: dict[str, Any] = {"report": {"mode": mode or "summary"}}
        report = result["report"]
        if mode in ("", "tasks"):
            rows = (
                await db.execute(
                    select(tasks.c.code, tasks.c.state, func.count().label("n")).group_by(
                        tasks.c.code, tasks.c.state
                    )
                )
            ).all()
            by_state = {"open": 0, "taken": 0, "done": 0}
            by_code: dict[str, dict[str, int]] = {}
            for code, state, count in rows:
                by_state[str(state)] = by_state.get(str(state), 0) + int(count)
                item = by_code.setdefault(str(code), {"open": 0, "taken": 0, "done": 0})
                item[str(state)] = int(count)
            report.update(states=by_state, codes=by_code)
        if mode in ("", "news"):
            rows = (
                await db.execute(
                    select(
                        broadcasts.c.state,
                        func.count().label("n"),
                        func.sum(broadcasts.c.sent).label("sent"),
                        func.sum(broadcasts.c.failed).label("failed"),
                    )
                    .where(broadcasts.c.kind == "announcement")
                    .group_by(broadcasts.c.state)
                )
            ).all()
            report.update(
                news={str(state): int(n) for state, n, _, _ in rows},
                delivered=sum(int(sent or 0) for _, _, sent, _ in rows),
                failed=sum(int(failed or 0) for _, _, _, failed in rows),
            )
        if mode in ("", "people"):
            rows = (
                await db.execute(
                    select(users.c.role, func.count().label("n"))
                    .where(users.c.access_revoked.is_(False), users.c.blocked_at.is_(None))
                    .group_by(users.c.role)
                )
            ).all()
            report["roles"] = {str(role): int(count) for role, count in rows}
            report["subscribers"] = int(
                (
                    await db.execute(
                        select(func.count())
                        .select_from(users)
                        .where(
                            users.c.news_opt_in.is_(True),
                            users.c.access_revoked.is_(False),
                            users.c.blocked_at.is_(None),
                        )
                    )
                ).scalar_one()
            )
        if mode in ("", "operations"):
            instant = as_of or datetime.now(UTC)
            if instant.utcoffset() is None:
                raise ValueError("aware report time required")
            waiting = (
                await db.execute(
                    select(outbox.c.state, func.count().label("n")).group_by(outbox.c.state)
                )
            ).all()
            states = {str(state): int(count) for state, count in waiting}
            report["delivery"] = {
                "pending": states.get("pending", 0) + states.get("summary", 0),
                "failed": states.get("failed", 0),
                "blocked": states.get("blocked", 0),
                "sent": states.get("sent", 0),
                "cancelled": states.get("cancelled", 0),
                "unsubscribed": states.get("unsubscribed", 0),
            }
            overdue = (
                await db.execute(
                    select(tasks.c.code, func.count().label("n"))
                    .where(
                        tasks.c.state.in_(("open", "taken")),
                        tasks.c.due_at.is_not(None),
                        tasks.c.due_at < instant,
                    )
                    .group_by(tasks.c.code)
                )
            ).all()
            report["overdue"] = {str(code): int(count) for code, count in overdue}
        return result
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
                        .where(
                            users.c.news_opt_in.is_(True),
                            users.c.access_revoked.is_(False),
                            users.c.blocked_at.is_(None),
                        )
                    )
                ).scalar_one()
            )
        return {"counts": values}
    return {}
