"""Pure occupancy, Moscow-local price and free-slot calculations."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .models import Activity, Hall, Interval, OccupancyResult, Record, require_interval


def occupancy(
    records: Iterable[Record], activities: Iterable[Activity], halls: Iterable[Hall],
    *, records_complete: bool = False, activities_complete: bool = False,
) -> OccupancyResult:
    """Only process independently confirmed, complete normalized data.

    The T-103/T-104 integration must prove the activities source or its
    verified record-based fallback is complete before setting the flags.
    A missing resource mapping must never silently create free slots.
    """
    if records_complete is not True or activities_complete is not True:
        raise ValueError("incomplete records/activities coverage")
    hall_list = list(halls)
    if not hall_list or len({hall.code for hall in hall_list}) != len(hall_list):
        raise ValueError("halls must be nonempty and unique")
    if not all(hall.mapping_verified for hall in hall_list):
        raise ValueError("unverified hall mapping")
    by_code = {hall.code: hall for hall in hall_list}
    staff_codes = [hall.staff_id for hall in hall_list if hall.staff_id is not None]
    if len(set(staff_codes)) != len(staff_codes):
        raise ValueError("hall staff columns must be unique")
    intervals: set[Interval] = set()
    for record in records:
        if record.cancelled:
            continue
        matched = {hall.code for hall in hall_list if hall.staff_id is not None and record.staff_id == hall.staff_id}
        if record.hall_code is not None:
            if record.hall_code not in by_code:
                raise ValueError("record references an unverified hall")
            if matched and record.hall_code not in matched:
                raise ValueError("record hall does not match hall column")
            matched.add(record.hall_code)
        if record.kind == "rental" and not matched:
            raise ValueError("rental record without verified hall mapping")
        if record.kind == "unknown" and matched:
            raise ValueError("occupancy record has unverified kind")
        for code in matched:
            intervals.add(Interval(code, record.starts_at, record.ends_at))
    for activity in activities:
        if activity.cancelled:
            continue
        matched = {hall.code for hall in hall_list if hall.resource_ids & activity.resource_ids}
        if activity.hall_code is not None:
            if activity.hall_code not in by_code:
                raise ValueError("activity references an unverified hall")
            matched.add(activity.hall_code)
        if not matched:
            raise ValueError("group event lacks a verified hall mapping")
        for code in matched:
            intervals.add(Interval(code, activity.starts_at, activity.ends_at))
    return OccupancyResult(
        tuple(sorted(intervals, key=lambda x: (x.hall_code, x.starts_at, x.ends_at))),
        frozenset(hall.code for hall in hall_list),
    )


def price(hall: Hall, when: datetime, *, studio_timezone: ZoneInfo) -> int:
    """Tariff for a studio-local instant; caller supplies studio.yaml timezone."""
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("price requires an aware datetime")
    local = when.astimezone(studio_timezone)
    if local.weekday() >= 5:
        return hall.weekend
    return hall.weekday_before_18 if local.hour < 18 else hall.weekday_after_18


def free_slots(
    occupied: OccupancyResult, hall_code: str,
    start: datetime, end: datetime, step_minutes: int = 60,
) -> tuple[Interval, ...]:
    """Reject unknown hall or unverified occupancy instead of showing free time."""
    require_interval(start, end)
    if not isinstance(occupied, OccupancyResult) or hall_code not in occupied.hall_codes:
        raise ValueError("missing verified occupancy coverage for hall")
    if step_minutes <= 0:
        raise ValueError("step_minutes must be positive")
    busy = [entry for entry in occupied.intervals if entry.hall_code == hall_code]
    step = timedelta(minutes=step_minutes)
    cursor, free = start, []
    while cursor + step <= end:
        next_end = cursor + step
        if all(next_end <= item.starts_at or cursor >= item.ends_at for item in busy):
            free.append(Interval(hall_code, cursor, next_end))
        cursor = next_end
    return tuple(free)
