"""No fake availability: test date geometry and native button restrictions."""

from datetime import date, timedelta

import pytest

from app.domain.calendar import HORIZON_DAYS, calendar_model, selectable
from app.services.rich_views import screen_parts
from app.ui.rich_calendar import calendar_view


def test_grid_has_weekday_columns_and_bounded_navigation():
    today = date(2026, 11, 16)
    model = calendar_model(today, date(2026, 11, 1))
    assert all(len(week) == 7 for week in model["rows"])
    assert model["previous"] is None
    assert model["next"] == "2026-12"
    assert not selectable(today - timedelta(days=1), today)
    assert selectable(today + timedelta(days=HORIZON_DAYS), today)
    assert not selectable(today + timedelta(days=HORIZON_DAYS + 1), today)
    html = calendar_view({"calendar": model}, rental=False)
    assert 'data="ui:schedule:day:2026-11-16"' in html
    assert 'data="ui:schedule:day:2026-11-15"' not in html
    assert "не свободный слот" in html
    assert "не бронь" in html
    assert html.count("<tg-button-row>") >= 5


def test_selected_date_uses_request_not_claimed_slot():
    today = date(2026, 11, 16)
    selected = date(2026, 11, 19)
    model = calendar_model(today, selected.replace(day=1), selected)
    rental = calendar_view({"calendar": model}, rental=True)
    booking = calendar_view({"calendar": model}, rental=False)
    assert 'data="ui:compose:rent:2026-11-19"' in rental
    assert 'data="ui:compose:booking:2026-11-19"' in booking
    assert "Свободных мест: 3" not in booking
    with pytest.raises(ValueError):
        calendar_model(today, date(2027, 5, 1))
    with pytest.raises(ValueError):
        calendar_model(today, date(2026, 11, 1), date(2026, 11, 14))


@pytest.mark.parametrize(
    "invalid",
    [
        "schedule:2026-13",
        "schedule:2026-1",
        "schedule:day:2026-02-30",
        "rent:day:2026-11-09extra",
        "schedule:day:9999-11-22",
        "reports:money",
    ],
)
def test_calendar_and_report_callback_parsing_fails_closed(invalid):
    with pytest.raises(ValueError):
        screen_parts(invalid)


def test_report_and_calendar_screens_have_normalized_nav_bases():
    assert screen_parts("schedule:2026-11") == ("schedule", "month", 202611)
    assert screen_parts("rent:day:2026-11-19") == ("rent", "day", 20261119)
    assert screen_parts("reports:news") == ("reports", "news", 0)
