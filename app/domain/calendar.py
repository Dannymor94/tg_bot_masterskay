"""Pure bounded date selection; never equate a date with free CRM capacity."""

from calendar import monthcalendar
from datetime import date, timedelta

HORIZON_DAYS = 90
MONTH_NAMES = (
    "",
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)


def selectable(day: date, today: date) -> bool:
    return today <= day <= today + timedelta(days=HORIZON_DAYS)


def calendar_model(today: date, month: date, selected: date | None = None) -> dict:
    """Finite view model; ``available`` is always unknown until YCLIENTS is verified."""
    first = month.replace(day=1)
    last = today + timedelta(days=HORIZON_DAYS)
    if first > last.replace(day=1) or (first.year, first.month) < (today.year, today.month):
        raise ValueError("calendar month outside request horizon")
    if selected is not None and (selected.year, selected.month) != (first.year, first.month):
        raise ValueError("selected date outside displayed month")
    if selected is not None and not selectable(selected, today):
        raise ValueError("selected date outside request horizon")
    previous = (first - timedelta(days=1)).replace(day=1)
    next_month = (first + timedelta(days=32)).replace(day=1)
    return {
        "month": first.isoformat()[:7],
        "title": f"{MONTH_NAMES[first.month].capitalize()} {first.year}",
        "rows": [
            [
                {
                    "label": f"{number:02d}" if number else "·",
                    "date": date(first.year, first.month, number).isoformat() if number else None,
                    "enabled": bool(
                        number and selectable(date(first.year, first.month, number), today)
                    ),
                }
                for number in week
            ]
            for week in monthcalendar(first.year, first.month)
        ],
        "previous": previous.isoformat()[:7]
        if (previous.year, previous.month) >= (today.year, today.month)
        else None,
        "next": next_month.isoformat()[:7] if next_month <= last else None,
        "selected": selected.isoformat() if selected else None,
        "availability": "unverified",
    }
