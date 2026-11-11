"""Pure occupancy/price/free-slot functions using internal normalized models."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from .models import Activity, Hall, Interval, Record, require_interval


def occupancy(
    records: Iterable[Record], activities: Iterable[Activity], halls: Iterable[Hall]
) -> tuple[Interval, ...]:
    """Hall occupied by booking on hall's staff column OR activity resource."""
    hall_list = list(halls)
    if len({h.code for h in hall_list}) != len(hall_list):
        raise ValueError("duplicate hall codes")
    intervals: set[Interval] = set()
    for record in records:
        if record.cancelled:
            continue
        for hall in hall_list:
            if hall.staff_id is not None and record.staff_id == hall.staff_id:
                intervals.add(Interval(hall.code, record.starts_at, record.ends_at))
    for activity in activities:
        if activity.cancelled:
            continue
        for hall in hall_list:
            if hall.resource_ids & activity.resource_ids:
                intervals.add(Interval(hall.code, activity.starts_at, activity.ends_at))
    return tuple(sorted(intervals, key=lambda x: (x.hall_code, x.starts_at, x.ends_at)))


def price(hall: Hall, when: datetime) -> int:
    """Per-hour rental rate at studio-local time (weekend = Sat/Sun)."""
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("price requires aware local datetime")
    if when.weekday() >= 5:
        return hall.weekend
    return hall.weekday_before_18 if when.hour < 18 else hall.weekday_after_18


def free_slots(
    occupied: Iterable[Interval], hall_code: str,
    start: datetime, end: datetime, step_minutes: int = 60,
) -> tuple[Interval, ...]:
    """Full-length free slots, no partial overlapping slots."""
    require_interval(start, end)
    if step_minutes <= 0:
        raise ValueError("step_minutes must be positive")
    busy = [entry for entry in occupied if entry.hall_code == hall_code]
    step = timedelta(minutes=step_minutes)
    cursor, free = start, []
    while cursor + step <= end:
        next_end = cursor + step
        if all(next_end <= item.starts_at or cursor >= item.ends_at for item in busy):
            free.append(Interval(hall_code, cursor, next_end))
        cursor = next_end
    return tuple(free)
