"""Telegram startup must not inherit the short SQLite outbox send deadline."""

from app.__main__ import MIN_TELEGRAM_SESSION_TIMEOUT_SECONDS, build_telegram_session
from app.settings import Settings, load_studio


def test_bootstrap_session_allows_slow_get_me_without_expanding_outbox_deadline():
    settings = Settings.model_validate(
        {
            "yclients_api_host": "https://example.invalid",
            "yclients_partner_token": "dummy-partner",
            "yclients_user_token": "dummy-user",
            "yclients_company_id": 1,
            "telegram_bot_token": "dummy-telegram",
            "owner_telegram_id": 2,
        }
    )
    studio = load_studio()
    assert studio.delivery.request_timeout_seconds == 3

    session = build_telegram_session(settings, studio)
    assert session.timeout == MIN_TELEGRAM_SESSION_TIMEOUT_SECONDS
    assert studio.delivery.request_timeout_seconds == 3


def test_session_timeout_preserves_larger_configured_delivery_deadline():
    settings = Settings.model_validate(
        {
            "yclients_api_host": "https://example.invalid",
            "yclients_partner_token": "dummy-partner",
            "yclients_user_token": "dummy-user",
            "yclients_company_id": 1,
            "telegram_bot_token": "dummy-telegram",
            "owner_telegram_id": 2,
        }
    )
    studio = load_studio()
    studio = studio.model_copy(
        update={
            "delivery": studio.delivery.model_copy(
                update={"request_timeout_seconds": MIN_TELEGRAM_SESSION_TIMEOUT_SECONDS + 5}
            )
        }
    )

    session = build_telegram_session(settings, studio)
    assert session.timeout == MIN_TELEGRAM_SESSION_TIMEOUT_SECONDS + 5
