"""Eight-week empty-window reporting from complete, verified historical coverage."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import Hall, Interval
from .occupancy import price


@dataclass(frozen=True)
class HistoricalWeek:
    monday: datetime  # midnight in configured studio timezone
    occupied: tuple[Interval, ...]
    complete: bool = False  # set True only after T-103/T-104 verifies full API coverage


@dataclass(frozen=True)
class EmptyWindow:
    hall_code: str
    weekday: int
    start_hour: int
    end_hour: int
    potential_revenue: int


def _slot(monday: datetime, day: int, hour: int) -> tuple[datetime, datetime]:
    start = datetime.combine(monday.date() + timedelta(days=day), time(hour), tzinfo=monday.tzinfo)
    return start, start + timedelta(hours=1)


def _midnight(monday: datetime, zone: ZoneInfo) -> bool:
    return (
        monday.tzinfo is not None and monday.utcoffset() is not None
        and monday.tzinfo == zone and monday.weekday() == 0
        and (monday.hour, monday.minute, monday.second, monday.microsecond) == (0, 0, 0, 0)
    )


def empty_windows(
    weeks: Sequence[HistoricalWeek], halls: Iterable[Hall], report_monday: datetime,
    *, studio_timezone: ZoneInfo, open_hour: int, close_hour: int,
    max_occupied_weeks: int,
) -> tuple[EmptyWindow, ...]:
    """Aggregate full 8-week history; never interpret missing API data as empty.

    Callers must attest completeness per week *and* verified hall mapping,
    independently of this pure algorithm. Pass rules from studio.yaml.
    """
    if len(weeks) != 8 or any(w.complete is not True for w in weeks):
        raise ValueError("eight complete historical weeks are required")
    if not 0 <= max_occupied_weeks <= 8:
        raise ValueError("invalid occupied threshold")
    if not 0 <= open_hour < close_hour <= 24:
        raise ValueError("invalid operating hours")
    if not _midnight(report_monday, studio_timezone):
        raise ValueError("report_monday must start at studio-local Monday midnight")
    halls_list = list(halls)
    if not halls_list or len({h.code for h in halls_list}) != len(halls_list):
        raise ValueError("halls must be nonempty and unique")
    if any(not h.mapping_verified for h in halls_list):
        raise ValueError("unverified hall mapping")
    for index, week in enumerate(weeks):
        m = week.monday
        if not _midnight(m, studio_timezone):
            raise ValueError("historical week must start at studio-local Monday midnight")
        if index and m.date() != weeks[index - 1].monday.date() + timedelta(days=7):
            raise ValueError("nonconsecutive historical weeks")
        if any(interval.hall_code not in {h.code for h in halls_list} for interval in week.occupied):
            raise ValueError("historical interval contains unknown hall")
    if report_monday.date() != weeks[-1].monday.date() + timedelta(days=7):
        raise ValueError("report week must follow history")

    result: list[EmptyWindow] = []
    for hall in halls_list:
        for day in range(7):
            start_hour: int | None = None
            revenue = 0
            for hour in range(open_hour, close_hour + 1):
                occupied_count = 0
                if hour < close_hour:
                    for week in weeks:
                        start, end = _slot(week.monday, day, hour)
                        if any(
                            item.hall_code == hall.code
                            and start < item.ends_at and end > item.starts_at
                            for item in week.occupied
                        ):
                            occupied_count += 1
                eligible = hour < close_hour and occupied_count <= max_occupied_weeks
                if eligible:
                    if start_hour is None:
                        start_hour = hour
                    start, _ = _slot(report_monday, day, hour)
                    revenue += price(hall, start, studio_timezone=studio_timezone)
                elif start_hour is not None:
                    result.append(EmptyWindow(hall.code, day, start_hour, hour, revenue))
                    start_hour, revenue = None, 0
    return tuple(sorted(result, key=lambda w: (-w.potential_revenue, w.hall_code, w.weekday, w.start_hour)))
