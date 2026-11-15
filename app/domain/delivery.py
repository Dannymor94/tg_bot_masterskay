"""Deterministic delivery and local calendar policy."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.settings import StudioConfig

# Replies to explicit user actions are not scheduled notifications. Quiet hours
# apply to pushes, including task cards and broadcasts, not to conversation UI.
INTERACTIVE_REPLY_KINDS = frozenset(
    {
        "client.onboarding",
        "rbac.menu",
        "rbac.invite",
        "rbac.accepted",
        "rbac.revoked",
        "question.ack",
        "task.reply",
        "task.reply_help",
        "task.queue_empty",
        "task.card.query",
        "broadcast.preview",
        "broadcast.preview.photo",
        "broadcast.subscribed",
        "broadcast.unsubscribed",
        "ui.rich.new",
        "ui.rich.edit",
        "ui.compose.prompt",
        "ui.compose.feedback",
    }
)


def delivery_time(now: datetime, cfg: StudioConfig) -> datetime:
    if now.utcoffset() is None:
        raise ValueError("aware datetime required")
    local = now.astimezone(ZoneInfo(cfg.studio.timezone))
    start, end = cfg.notifications.quiet_hours.from_, cfg.notifications.quiet_hours.to
    clock = local.time()
    quiet = start <= clock < end if start < end else clock >= start or clock < end
    if start == end or not quiet:
        return now.astimezone(UTC)
    day = local.date() + timedelta(days=int(start > end and clock >= start))
    return datetime.combine(day, end, local.tzinfo).astimezone(UTC)


def due_time(kind: str, when: datetime, cfg: StudioConfig) -> datetime:
    if kind in INTERACTIVE_REPLY_KINDS:
        if when.utcoffset() is None:
            raise ValueError("aware datetime required")
        return when.astimezone(UTC)
    return delivery_time(when, cfg)


def retry_time(now: datetime, attempts: int, cfg: StudioConfig) -> datetime:
    policy = cfg.delivery
    exponent = min(max(attempts - 1, 0), policy.backoff_max_seconds.bit_length())
    seconds = min(policy.backoff_base_seconds * 2**exponent, policy.backoff_max_seconds)
    return now + timedelta(seconds=seconds)
