"""Deterministic delivery and local calendar policy."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.settings import StudioConfig


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


def retry_time(now: datetime, attempts: int, cfg: StudioConfig) -> datetime:
    policy = cfg.delivery
    exponent = min(max(attempts - 1, 0), policy.backoff_max_seconds.bit_length())
    seconds = min(policy.backoff_base_seconds * 2**exponent, policy.backoff_max_seconds)
    return now + timedelta(seconds=seconds)
