"""Pure reference pricing rule for future T-104 integration (not runtime-wired).

Uses only already approved studio.yaml tariff bands. It does not depend on
YCLIENTS records, availability, live requests, or unverified resource IDs.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from app.settings import Hall

_STUDIO_TIMEZONE = ZoneInfo("Europe/Moscow")
_WEEKEND_START = 5
_EVENING_START_HOUR = 18


def price(hall: Hall, dt: datetime) -> int:
    """Return one hour's configured tariff for the studio-local start time.

    Saturdays and Sundays use the weekend rate. On weekdays, the evening rate
    begins at 18:00 Moscow time. A caller must separately validate that a slot
    is open/available; this function does not infer occupancy or permissions.
    """
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("price requires a timezone-aware datetime")

    studio_time = dt.astimezone(_STUDIO_TIMEZONE)
    tariffs = hall.price_per_hour
    if studio_time.weekday() >= _WEEKEND_START:
        return tariffs.weekend
    if studio_time.hour >= _EVENING_START_HOUR:
        return tariffs.weekday_after_18
    return tariffs.weekday_before_18
