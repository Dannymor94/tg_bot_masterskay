"""Pure opt-in segmentation for promotional announcements only."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Subscriber:
    telegram_user_id: int
    role: str  # client | teacher | admin
    news_opt_in: bool
    teacher_kind: str | None = None
    blocked_at: datetime | None = None


def segment(users: Iterable[Subscriber], name: str) -> tuple[int, ...]:
    if name not in {"clients_all", "teachers_all", "teachers_renters", "everyone"}:
        raise ValueError("unknown segment")
    ids: set[int] = set()
    for user in users:
        if user.telegram_user_id <= 0 or not user.news_opt_in or user.blocked_at is not None:
            continue
        if name == "clients_all" and user.role != "client":
            continue
        if name == "teachers_all" and user.role != "teacher":
            continue
        if name == "teachers_renters" and (user.role != "teacher" or user.teacher_kind != "renter"):
            continue
        if name == "everyone" and user.role not in {"client", "teacher", "admin"}:
            continue
        ids.add(user.telegram_user_id)
    return tuple(sorted(ids))
