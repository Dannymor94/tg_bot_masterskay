"""Normalized internal DTOs, independent of the YCLIENTS wire format."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

Role = Literal["client", "teacher", "admin"]
EventType = Literal["record.created", "record.cancelled", "record.rescheduled"]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Interval(DTO):
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    hall_code: str | None

    @model_validator(mode="after")
    def validate_interval(self) -> "Interval":
        if self.ends_at.astimezone(UTC) <= self.starts_at.astimezone(UTC):
            raise ValueError("ends_at <= starts_at")
        return self


class RecordSnapshot(Interval):
    record_id: int
    staff_id: int | None
    client_id: int | None
    activity_id: int | None
    service_id: int | None
    deleted: bool
    content_hash: str
    seen_at: AwareDatetime


class DomainEvent(DTO):
    type: EventType
    record_id: int
    payload_min: dict[str, str | int | bool | None]
    created_at: AwareDatetime
    dedupe_key: str


class TaskDraft(DTO):
    code: str
    title: str
    body: str
    ref: str | None = None
    requester_id: int | None = None
    due_at: AwareDatetime | None = None
    dedupe_key: str


class OutboxDraft(DTO):
    telegram_user_id: int
    kind: str
    payload_min: dict[str, str | int | bool | None]
    next_attempt_at: AwareDatetime
    dedupe_key: str


class User(DTO):
    telegram_user_id: int
    role: Role
    is_owner: bool = False
    teacher_kind: Literal["staff", "renter"] | None = None
    yclients_client_id: int | None = None
    yclients_staff_id: int | None = None
    news_opt_in: bool = False
    slot_digest_enabled: bool = False
    blocked_at: datetime | None = None
