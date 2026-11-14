"""Default-deny starter RBAC; the service must load a trusted target."""

from __future__ import annotations

from dataclasses import dataclass
from .models import RecordKind


@dataclass(frozen=True)
class Actor:
    telegram_user_id: int
    role: str  # client | teacher | admin
    is_owner: bool = False
    teacher_kind: str | None = None  # staff | renter
    yclients_client_id: int | None = None
    yclients_staff_id: int | None = None


@dataclass(frozen=True)
class Target:
    telegram_user_id: int | None = None
    client_id: int | None = None
    staff_id: int | None = None
    record_kind: RecordKind = "unknown"  # trusted classification, never from callback


def can(actor: Actor, action: str, target: Target | None = None) -> bool:
    """Minimal action matrix; T-201 completes it and checks it in services."""
    if actor.telegram_user_id <= 0 or actor.role not in {"client", "teacher", "admin"}:
        return False
    if actor.is_owner and actor.role != "admin":
        return False
    if action == "schedule.view":
        return True
    if action in {"task.take", "task.done", "task.reply", "broadcast.send", "invite.teacher"}:
        return actor.role == "admin"
    if action in {"invite.admin", "admin.revoke"}:
        return actor.role == "admin" and actor.is_owner
    if action == "settings.update":
        return target is not None and target.telegram_user_id == actor.telegram_user_id
    if action == "record.view":
        if target is None:
            return False
        if actor.role == "admin":
            return True
        if actor.role == "client":
            return actor.yclients_client_id is not None and target.client_id == actor.yclients_client_id
        if actor.teacher_kind == "staff":
            return (
                target.record_kind == "class"
                and actor.yclients_staff_id is not None
                and target.staff_id == actor.yclients_staff_id
            )
        if actor.teacher_kind == "renter":
            return (
                target.record_kind == "rental"
                and actor.yclients_client_id is not None
                and target.client_id == actor.yclients_client_id
            )
    return False
