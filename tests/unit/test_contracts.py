from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.domain.models import Interval, OutboxDraft, RecordSnapshot, User


@pytest.mark.parametrize("kind", [Interval, RecordSnapshot])
def test_interval_contract(kind):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    data = {"starts_at": now, "ends_at": now + timedelta(hours=1), "hall_code": "big"}
    if kind is RecordSnapshot:
        data.update(
            record_id=1,
            staff_id=None,
            client_id=None,
            activity_id=None,
            service_id=None,
            deleted=False,
            content_hash="hash",
            seen_at=now,
        )
    model = kind.model_validate(data)
    assert model.starts_at == now
    for invalid in (now, now - timedelta(hours=1), now.replace(tzinfo=None)):
        with pytest.raises(ValidationError):
            kind.model_validate({**data, "ends_at": invalid})
    with pytest.raises(ValidationError):
        kind.model_validate({**data, "phone": "not-allowed"})


def test_outbox_requires_dedupe_key():
    with pytest.raises(ValidationError):
        OutboxDraft.model_validate(
            {
                "telegram_user_id": 1,
                "kind": "text",
                "payload_min": {},
                "next_attempt_at": datetime(2026, 1, 1, tzinfo=UTC),
            }
        )


@pytest.mark.parametrize("blocked_at", [datetime(2026, 1, 1), "2026-01-01T00:00:00"])
def test_user_rejects_naive_blocked_at(blocked_at):
    with pytest.raises(ValidationError) as error:
        User(telegram_user_id=1, role="client", blocked_at=blocked_at)
    assert any(
        item["loc"] == ("blocked_at",) and item["type"] == "timezone_aware"
        for item in error.value.errors()
    )


@pytest.mark.parametrize(
    "blocked_at", [None, datetime(2026, 1, 1, tzinfo=UTC), "2026-01-01T03:00:00+03:00"]
)
def test_user_accepts_aware_or_absent_blocked_at(blocked_at):
    user = User.model_validate({"telegram_user_id": 1, "role": "client", "blocked_at": blocked_at})
    if blocked_at is None:
        assert user.blocked_at is None
    else:
        assert user.blocked_at == datetime(2026, 1, 1, tzinfo=UTC)
