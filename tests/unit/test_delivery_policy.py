from datetime import UTC, datetime, timedelta

import pytest

from app.domain.delivery import delivery_time, retry_time
from app.settings import load_studio

CFG = load_studio()


@pytest.mark.parametrize(
    "hour,minute,day,result_hour",
    [
        (18, 59, 12, 18),
        (19, 0, 13, 4),
        (23, 0, 13, 4),
        (0, 0, 12, 4),
        (3, 59, 12, 4),
        (4, 0, 12, 4),
    ],
)
def test_quiet_boundaries(hour, minute, day, result_hour):
    now = datetime(2026, 11, 12, hour, minute, tzinfo=UTC)
    result = delivery_time(now, CFG)
    assert result.day == day and result.hour == result_hour
    assert result.minute == (minute if result == now else 0)


@pytest.mark.parametrize("attempt,seconds", [(1, 5), (2, 10), (3, 20), (20, 3600), (10000, 3600)])
def test_backoff_is_bounded(attempt, seconds):
    now = datetime(2026, 11, 12, tzinfo=UTC)
    assert retry_time(now, attempt, CFG) == now + timedelta(seconds=seconds)


def test_daytime_quiet_window_and_equal_boundaries():
    now = datetime(2026, 11, 12, 9, tzinfo=UTC)
    values = CFG.model_dump(by_alias=True)
    values["notifications"]["quiet_hours"] = {"from": "11:00", "to": "13:00"}
    cfg = type(CFG).model_validate(values)
    assert delivery_time(now, cfg) == now.replace(hour=10)
    values["notifications"]["quiet_hours"] = {"from": "11:00", "to": "11:00"}
    assert delivery_time(now, type(CFG).model_validate(values)) == now
    with pytest.raises(ValueError, match="aware"):
        delivery_time(now.replace(tzinfo=None), CFG)
