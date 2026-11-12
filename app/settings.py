from datetime import time
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    ValidationError,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.texts import ru

Positive = Annotated[int, Field(gt=0)]
NonNegative = Annotated[int, Field(ge=0)]
Hour = Annotated[int, Field(ge=0, le=23)]
STUDIO_PATH = Path(__file__).parent / "config" / "studio.yaml"
SECRETS_PATH = Path.home() / ".config" / "studio-bot" / "secrets.env"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Studio(Model):
    name: str
    timezone: Literal["Europe/Moscow"]
    open_hour: Hour
    close_hour: Hour
    slot_minutes: Positive
    privacy_policy_url: HttpUrl
    booking_widget_url: HttpUrl | None
    rent_page_url: HttpUrl

    @model_validator(mode="after")
    def validate_hours(self) -> "Studio":
        if self.close_hour <= self.open_hour:
            raise ValueError("close_hour <= open_hour")
        return self


class Prices(Model):
    weekday_before_18: Positive
    weekday_after_18: Positive
    weekend: Positive


class Hall(Model):
    code: str
    title: str
    area_m2: Positive
    capacity_mats: Positive | None
    yclients_staff_id: Positive | None
    yclients_resource_ids: list[Positive]
    price_per_hour: Prices


class Rent(Model):
    service_title_prefix: str
    renter_lookback_days: Positive


class Sync(Model):
    interval_minutes: Positive
    window_days_back: NonNegative
    window_days_forward: Positive


class QuietHours(Model):
    from_: time = Field(alias="from")
    to: time


class Notifications(Model):
    quiet_hours: QuietHours
    admin_instant_events: list[str]
    admin_morning_summary_at: time
    teacher_instant_events: list[str]


class Reminders(Model):
    client_class_day_before_at: time
    teacher_rent_day_before_at: time
    skip_if_created_after_reminder_time: bool


class Feedback(Model):
    after_class_end_minutes: Positive
    low_score_threshold: Positive


class Weekly(Model):
    weekday: Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    at: time


class EmptySlots(Model):
    lookback_weeks: Positive
    max_occupied_weeks: NonNegative
    top_windows_in_report: Positive
    owner_report: Weekly
    teacher_digest: Weekly

    @model_validator(mode="after")
    def validate_window(self) -> "EmptySlots":
        if self.max_occupied_weeks > self.lookback_weeks:
            raise ValueError("max_occupied_weeks > lookback_weeks")
        return self


class Tasks(Model):
    overdue_ping_minutes: Positive
    done_ttl_days: Positive
    due_minutes: dict[str, Positive | None]


class PilotTargets(Model):
    weeks: Positive
    linked_clients: NonNegative
    linked_teachers: NonNegative
    median_take_minutes: Positive
    rsvp_answer_rate: Annotated[float, Field(ge=0, le=1)]
    empty_slot_bookings_via_bot: NonNegative


class Broadcasts(Model):
    messages_per_second: Positive
    max_text_length: Positive
    ask_news_opt_in_on_start: bool
    segments: list[str]


class StudioConfig(Model):
    version: Positive
    studio: Studio
    halls: list[Hall] = Field(min_length=1)
    rent: Rent
    sync: Sync
    notifications: Notifications
    reminders: Reminders
    feedback: Feedback
    empty_slots: EmptySlots
    tasks: Tasks
    pilot_targets: PilotTargets
    broadcasts: Broadcasts

    @model_validator(mode="after")
    def unique_halls(self) -> "StudioConfig":
        if len({hall.code for hall in self.halls}) != len(self.halls):
            raise ValueError("duplicate hall code")
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid", hide_input_in_errors=True)
    yclients_api_host: HttpUrl
    yclients_partner_token: SecretStr = Field(min_length=1)
    yclients_user_token: SecretStr = Field(min_length=1)
    yclients_company_id: Positive
    telegram_bot_token: SecretStr = Field(min_length=1)
    owner_telegram_id: Positive
    telegram_proxy_url: HttpUrl | None = None


def load_settings(secrets_path: Path = SECRETS_PATH) -> Settings:
    try:
        return Settings(_env_file=secrets_path, _env_file_encoding="utf-8")  # type: ignore[call-arg]
    except ValidationError as error:
        errors = error.errors(include_input=False, include_context=False, include_url=False)
        missing = next((item for item in errors if item["type"] == "missing"), None)
        if missing:
            raise ValueError(
                ru.MISSING_VARIABLE.format(name=str(missing["loc"][0]).upper())
            ) from None
        fields = ", ".join(str(item["loc"][0]).upper() for item in errors)
        raise ValueError(ru.INVALID_SETTINGS.format(fields=fields)) from None


def load_studio(path: Path = STUDIO_PATH) -> StudioConfig:
    try:
        return StudioConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (ValidationError, yaml.YAMLError, OSError):
        raise ValueError(ru.INVALID_STUDIO) from None
