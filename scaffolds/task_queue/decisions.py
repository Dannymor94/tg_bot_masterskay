"""T-203 pure task decisions, independent of storage and Telegram transport.

REFERENCE CODE ONLY: see scaffolds/task_queue/README.md before integration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

TaskState = Literal["open", "taken", "done"]
TASK_CODES = frozenset({"R1", "R2", "R3", "R4", "R5", "R6", "R8"})


@dataclass(frozen=True)
class TaskView:
    """Minimal trusted DB snapshot, not a client-provided callback value."""

    code: str
    state: TaskState
    created_at: datetime
    last_ping_at: datetime | None = None
    done_at: datetime | None = None


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")


def task_key(rule: str, source_key: str) -> str:
    """Deterministic key; source_key must be a stable, NON-PII event reference.

    Identity, including future event occurrence, is resolved by the caller.
    """
    if rule not in TASK_CODES or not source_key or not source_key.strip():
        raise ValueError("invalid task rule or stable source key")
    return json.dumps(["task", rule, source_key], ensure_ascii=True, separators=(",", ":"))


def can_take(state: TaskState) -> bool:
    """Advisory only; storage MUST still use conditional UPDATE WHERE state='open'."""
    if state not in ("open", "taken", "done"):
        raise ValueError("unknown task state")
    return state == "open"


def can_reply(code: str) -> bool:
    """Rule R3 alone exposes Reply; service MUST additionally authorize actor."""
    if code not in TASK_CODES:
        raise ValueError("unknown task rule")
    return code == "R3"


def reminder_due(
    task: TaskView,
    now: datetime,
    *,
    after_minutes: int,
    studio_timezone: ZoneInfo,
    work_open: time,
    work_close: time,
) -> bool:
    """A pending task may be reminded only during studio working hours.

    Elapsed-time interpretation: reminder after 60 wall-clock minutes, delivered
    at the next allowed working-hour instant if due overnight. The ADR does not
    specify whether overnight minutes count, so the integrator must confirm this
    interpretation. Does not enqueue or advance last_ping_at.
    """
    _aware(task.created_at)
    _aware(now)
    if task.last_ping_at is not None:
        _aware(task.last_ping_at)
    if after_minutes <= 0 or work_open >= work_close:
        raise ValueError("invalid reminder settings")
    if task.state != "open":
        return False
    local_now = now.astimezone(studio_timezone).timetz().replace(tzinfo=None)
    if not (work_open <= local_now < work_close):
        return False
    anchor = task.last_ping_at or task.created_at
    return now >= anchor + timedelta(minutes=after_minutes)


def should_clear_closed_text(task: TaskView, now: datetime, *, ttl_days: int) -> bool:
    """Only redact body/title of completed tasks; DO NOT delete audit or task row."""
    _aware(now)
    if task.done_at is not None:
        _aware(task.done_at)
    if ttl_days <= 0:
        raise ValueError("invalid retention TTL")
    return task.state == "done" and task.done_at is not None and (
        now >= task.done_at + timedelta(days=ttl_days)
    )
