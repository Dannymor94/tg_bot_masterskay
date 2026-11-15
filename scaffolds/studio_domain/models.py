"""Verified INTERNAL normalized values, never raw YCLIENTS response fields."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

RecordKind = Literal["rental", "class", "unknown"]


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
    service_id: int | None = None
    kind: RecordKind = "unknown"

    def __post_init__(self) -> None:
        if isinstance(self.record_id, bool) or self.record_id <= 0:
            raise ValueError("record_id must be positive")
        if self.kind not in ("rental", "class", "unknown"):
            raise ValueError("invalid normalized record kind")
        require_interval(self.starts_at, self.ends_at)


@dataclass(frozen=True)
class Hall:
    code: str
    staff_id: int | None
    resource_ids: frozenset[int]
    weekday_before_18: int
    weekday_after_18: int
    weekend: int
    mapping_verified: bool = False

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("hall code is required")
        if min(self.weekday_before_18, self.weekday_after_18, self.weekend) < 0:
            raise ValueError("prices cannot be negative")
        if self.mapping_verified and self.staff_id is None and not self.resource_ids:
            raise ValueError("verified hall must have a staff/resource mapping")


@dataclass(frozen=True)
class Activity:
    starts_at: datetime
    ends_at: datetime
    resource_ids: frozenset[int]
    cancelled: bool = False
    hall_code: str | None = None  # trusted internal mapping via verified fallback

    def __post_init__(self) -> None:
        require_interval(self.starts_at, self.ends_at)


@dataclass(frozen=True)
class Interval:
    hall_code: str
    starts_at: datetime
    ends_at: datetime

    def __post_init__(self) -> None:
        if not self.hall_code:
            raise ValueError("hall code is required")
        require_interval(self.starts_at, self.ends_at)


@dataclass(frozen=True)
class OccupancyResult:
    """Only built by occupancy() once verified API coverage has been asserted."""

    intervals: tuple[Interval, ...]
    hall_codes: frozenset[str]
