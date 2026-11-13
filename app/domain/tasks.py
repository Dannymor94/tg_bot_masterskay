"""Internal task rules; future business sources supply normalized, synthetic-safe data."""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.domain.delivery import delivery_time
from app.domain.models import TaskDraft
from app.settings import StudioConfig
from app.texts import ru

RULES = {
    "R1": "R1_rent_request",
    "R2": "R2_cannot_come",
    "R3": "R3_question",
    "R4": "R4_link_check",
    "R5": "R5_low_feedback",
    "R6": "R6_offer_empty_slot",
    "R8": "R8_manual",
}


def task_key(*parts: str | int) -> str:
    return json.dumps(["task", *parts], ensure_ascii=True, separators=(",", ":"))


def rule(
    code: str,
    source_key: str,
    cfg: StudioConfig,
    now: datetime,
    *,
    body: str,
    requester_id: int | None = None,
    ref: str | None = None,
    starts_at: datetime | None = None,
    score: int | None = None,
    rank: int | None = None,
) -> TaskDraft | None:
    if now.utcoffset() is None or code not in RULES or not source_key or not body.strip():
        raise ValueError("invalid task source")
    if code in ("R1", "R2", "R3", "R4", "R5") and (requester_id or 0) <= 0:
        raise ValueError("requester required")
    if code == "R2" and (starts_at is None or starts_at.utcoffset() is None):
        raise ValueError("class start required")
    if code == "R5":
        if score is None or score <= 0:
            raise ValueError("score required")
        if score > cfg.feedback.low_score_threshold:
            return None
    if code == "R6":
        if rank is None or rank <= 0:
            raise ValueError("window rank required")
        if rank > cfg.empty_slots.top_windows_in_report:
            return None
    minutes = cfg.tasks.due_minutes[RULES[code]]
    due = now + timedelta(minutes=minutes) if minutes is not None else None
    return TaskDraft(
        code=code,
        title=ru.TASK_TITLES[code],
        body=body,
        requester_id=requester_id,
        ref=ref,
        due_at=starts_at if code == "R2" else due,
        dedupe_key=task_key(code, source_key),
    )


def working(now: datetime, cfg: StudioConfig) -> bool:
    local = now.astimezone(ZoneInfo(cfg.studio.timezone))
    return (
        cfg.studio.open_hour <= local.hour < cfg.studio.close_hour
        and delivery_time(now, cfg) <= now
    )


def reminder_due(last_ping: datetime, now: datetime, cfg: StudioConfig) -> bool:
    return working(now, cfg) and now >= last_ping + timedelta(
        minutes=cfg.tasks.overdue_ping_minutes
    )
