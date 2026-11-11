"""Normalized INTERNAL models: these are not YCLIENTS API response fields."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


def require_interval(start: datetime, end: datetime) -> None:
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start must be timezone-aware")
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("end must be timezone-aware")
    if end <= start:
        raise ValueError("end must be after start")


@dataclass(frozen=True)
class Record:
    record_id: int
    starts_at: datetime
    ends_at: datetime
    client_id: int | None = None
    staff_id: int | None = None
    activity_id: int | None = None
    hall_code: str | None = None
    cancelled: bool = False

    def __post_init__(self) -> None:
        if self.record_id <= 0:
            raise ValueError("record_id must be positive")
        require_interval(self.starts_at, self.ends_at)


@dataclass(frozen=True)
class Hall:
    code: str
    staff_id: int | None
    resource_ids: frozenset[int]
    weekday_before_18: int
    weekday_after_18: int
    weekend: int

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("hall code is required")
        if min(self.weekday_before_18, self.weekday_after_18, self.weekend) < 0:
            raise ValueError("prices cannot be negative")


@dataclass(frozen=True)
class Activity:
    starts_at: datetime
    ends_at: datetime
    resource_ids: frozenset[int]
    cancelled: bool = False

    def __post_init__(self) -> None:
        require_interval(self.starts_at, self.ends_at)


@dataclass(frozen=True)
class Interval:
    hall_code: str
    starts_at: datetime
    ends_at: datetime

    def __post_init__(self) -> None:
        require_interval(self.starts_at, self.ends_at)
