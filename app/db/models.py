"""Internal storage schema; payload_min must contain no YCLIENTS names or phones."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, Integer, MetaData, String, Table
from sqlalchemy import Column as C
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError("aware datetime required")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


metadata = MetaData()


def table(name: str, *columns: Any) -> Table:
    return Table(name, metadata, *columns)


users = table(
    "users",
    C("telegram_user_id", Integer, primary_key=True),
    C("tg_username", String),
    C("tg_first_name", String),
    C("role", String, nullable=False),
    C("is_owner", Boolean, nullable=False, server_default="0"),
    C("access_revoked", Boolean, nullable=False, server_default="0"),
    C("teacher_kind", String),
    C("yclients_client_id", Integer),
    C("yclients_staff_id", Integer),
    C("consent_at", UTCDateTime),
    C("news_opt_in", Boolean, nullable=False, server_default="0"),
    C("slot_digest_enabled", Boolean, nullable=False, server_default="0"),
    C("source", String),
    C("blocked_at", UTCDateTime),
    C("created_at", UTCDateTime, nullable=False),
    CheckConstraint("role IN ('client', 'teacher', 'admin')"),
    CheckConstraint("teacher_kind IS NULL OR teacher_kind IN ('staff', 'renter')"),
)
invites = table(
    "invites",
    C("token_hash", String, primary_key=True),
    C("role", String, nullable=False),
    C("created_by", Integer, nullable=False),
    C("expires_at", UTCDateTime, nullable=False),
    C("used_by", Integer),
    C("used_at", UTCDateTime),
)
record_snapshots = table(
    "record_snapshots",
    C("record_id", Integer, primary_key=True),
    C("staff_id", Integer),
    C("client_id", Integer),
    C("activity_id", Integer),
    C("service_id", Integer),
    C("hall_code", String),
    C("starts_at", UTCDateTime, nullable=False),
    C("ends_at", UTCDateTime, nullable=False),
    C("deleted", Boolean, nullable=False),
    C("content_hash", String, nullable=False),
    C("seen_at", UTCDateTime, nullable=False),
)
events = table(
    "events",
    C("id", Integer, primary_key=True),
    C("type", String, nullable=False),
    C("record_id", Integer, nullable=False),
    C("payload_min", JSON, nullable=False),
    C("created_at", UTCDateTime, nullable=False),
    C("dedupe_key", String, nullable=False, unique=True),
)
tasks = table(
    "tasks",
    C("id", Integer, primary_key=True),
    C("code", String, nullable=False),
    C("title", String, nullable=False),
    C("body", String, nullable=False),
    C("ref", String),
    C("requester_id", Integer),
    C("state", String, nullable=False, server_default="open"),
    C("taken_by", Integer),
    C("taken_at", UTCDateTime),
    C("done_at", UTCDateTime),
    C("due_at", UTCDateTime),
    C("last_ping_at", UTCDateTime),
    C("dedupe_key", String, nullable=False, unique=True),
    CheckConstraint("state IN ('open', 'taken', 'done')"),
)
outbox = table(
    "outbox",
    C("id", Integer, primary_key=True),
    C("telegram_user_id", Integer, nullable=False),
    C("kind", String, nullable=False),
    C("payload_min", JSON, nullable=False),
    C("state", String, nullable=False, server_default="pending"),
    C("attempts", Integer, nullable=False, server_default="0"),
    C("next_attempt_at", UTCDateTime, nullable=False),
    C("dedupe_key", String, nullable=False, unique=True),
)
broadcasts = table(
    "broadcasts",
    C("id", Integer, primary_key=True),
    C("kind", String, nullable=False),
    C("segment", String),
    C("record_ref", String),
    C("text", String, nullable=False),
    C("photo_file_id", String),
    C("created_by", Integer, nullable=False),
    C("state", String, nullable=False),
    *(
        C(name, Integer, nullable=False, server_default="0")
        for name in ("sent", "failed", "blocked", "unsubscribed")
    ),
)
answers = table(
    "answers",
    C("record_id", Integer, primary_key=True),
    C("telegram_user_id", Integer, primary_key=True),
    C("kind", String, primary_key=True),
    C("value", String, nullable=False),
    C("answered_at", UTCDateTime, nullable=False),
    CheckConstraint("kind IN ('rsvp', 'feedback')"),
)
kv = table("kv", C("key", String, primary_key=True), C("value", String, nullable=False))
audit_log = table(
    "audit_log",
    C("id", Integer, primary_key=True),
    C("actor", Integer, nullable=False),
    C("action", String, nullable=False),
    C("target", String, nullable=False),
    C("created_at", UTCDateTime, nullable=False),
)
