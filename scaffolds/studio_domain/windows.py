"""Eight-week historical empty-window detector (pure normalized contract)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from collections.abc import Sequence, Iterable

from .models import Hall, Interval
from .occupancy import price


@dataclass(frozen=True)
class HistoricalWeek:
    monday: datetime  # Monday at 00:00 in Europe/Moscow
    occupied: tuple[Interval, ...]


@dataclass(frozen=True)
class EmptyWindow:
    hall_code: str
    weekday: int  # 0=Monday
    start_hour: int
    end_hour: int
    potential_revenue: int


def _slot(monday: datetime, day: int, hour: int) -> tuple[datetime, datetime]:
    begin = datetime.combine(
        monday.date() + timedelta(days=day), time(hour), tzinfo=monday.tzinfo
    )
    return begin, begin + timedelta(hours=1)


def empty_windows(
    weeks: Sequence[HistoricalWeek], halls: Iterable[Hall], report_monday: datetime,
    *, open_hour: int = 7, close_hour: int = 22, max_occupied_weeks: int = 1,
) -> tuple[EmptyWindow, ...]:
    """Windows occupied in at most max_occupied_weeks of exactly 8 complete weeks."""
    if len(weeks) != 8:
        raise ValueError("exactly 8 historical weeks are required")
    if not (0 <= open_hour < close_hour <= 24):
        raise ValueError("invalid operating hours")
    if not 0 <= max_occupied_weeks <= 8:
        raise ValueError("invalid threshold")
    if report_monday.tzinfo is None or report_monday.utcoffset() is None:
        raise ValueError("report_monday must have timezone")
    if report_monday.weekday() != 0 or report_monday.hour != 0 or report_monday.minute != 0:
        raise ValueError("report_monday must start at local Monday midnight")
    for index, week in enumerate(weeks):
        m = week.monday
        if m.tzinfo is None or m.utcoffset() is None or m.weekday() != 0 or (m.hour, m.minute) != (0, 0):
            raise ValueError("week must start at local Monday midnight")
        if m.tzinfo != report_monday.tzinfo:
            raise ValueError("week timezone mismatch")
        if index and m.date() != weeks[index - 1].monday.date() + timedelta(days=7):
            raise ValueError("nonconsecutive weeks")
    if report_monday.date() != weeks[-1].monday.date() + timedelta(days=7):
        raise ValueError("report week must follow history")

    result: list[EmptyWindow] = []
    for hall in halls:
        for day in range(7):
            start_hour = None
            revenue = 0
            for hour in range(open_hour, close_hour + 1):
                eligible = False
                if hour < close_hour:
                    occupied_count = 0
                    for week in weeks:
                        start, end = _slot(week.monday, day, hour)
                        if any(
                            x.hall_code == hall.code
                            and start < x.ends_at and end > x.starts_at
                            for x in week.occupied
                        ):
                            occupied_count += 1
                    eligible = occupied_count <= max_occupied_weeks
                if eligible:
                    if start_hour is None:
                        start_hour = hour
                    report_start, _ = _slot(report_monday, day, hour)
                    revenue += price(hall, report_start)
                elif start_hour is not None:
                    result.append(EmptyWindow(hall.code, day, start_hour, hour, revenue))
                    start_hour, revenue = None, 0
    return tuple(sorted(result, key=lambda x: (-x.potential_revenue, x.hall_code, x.weekday, x.start_hour)))
