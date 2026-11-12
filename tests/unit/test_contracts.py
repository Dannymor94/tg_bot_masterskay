from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.domain.models import Interval, OutboxDraft, RecordSnapshot


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
