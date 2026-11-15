"""Offline tariff-band tests; no YCLIENTS or Telegram access."""

from datetime import datetime, timezone

import pytest

from app.settings import Hall, load_studio
from scaffolds.pricing import price


@pytest.fixture(scope="module")
def halls() -> dict[str, Hall]:
    return {hall.code: hall for hall in load_studio().halls}


@pytest.mark.parametrize(
    ("hall_code", "local_start", "expected"),
    [
        ("big", "2026-11-13T17:59:00+03:00", 900),
        ("big", "2026-11-13T18:00:00+03:00", 1000),
        ("big", "2026-11-14T12:00:00+03:00", 1000),
        ("small", "2026-11-13T17:59:00+03:00", 600),
        ("small", "2026-11-13T18:00:00+03:00", 700),
        ("small", "2026-11-15T12:00:00+03:00", 700),
        ("tea", "2026-11-13T17:59:00+03:00", 500),
        ("tea", "2026-11-13T18:00:00+03:00", 600),
        ("tea", "2026-11-14T12:00:00+03:00", 600),
    ],
)
def test_tariffs_follow_approved_studio_config(
    halls: dict[str, Hall], hall_code: str, local_start: str, expected: int
) -> None:
    assert price(halls[hall_code], datetime.fromisoformat(local_start)) == expected


def test_utc_instants_are_converted_to_moscow_calendar(halls: dict[str, Hall]) -> None:
    big = halls["big"]
    assert price(big, datetime(2026, 11, 13, 14, 59, tzinfo=timezone.utc)) == 900
    assert price(big, datetime(2026, 11, 13, 15, 0, tzinfo=timezone.utc)) == 1000
    # Friday 21:00 UTC is Saturday 00:00 in Moscow: weekend tariff.
    assert price(big, datetime(2026, 11, 13, 21, 0, tzinfo=timezone.utc)) == 1000


def test_naive_datetime_is_rejected(halls: dict[str, Hall]) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        price(halls["big"], datetime(2026, 11, 13, 12, 0))


def test_pricing_does_not_mutate_configuration(halls: dict[str, Hall]) -> None:
    hall = halls["small"]
    before = hall.model_dump()
    assert price(hall, datetime.fromisoformat("2026-11-15T11:00:00+03:00")) == 700
    assert hall.model_dump() == before
