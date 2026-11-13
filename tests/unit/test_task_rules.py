from datetime import UTC, datetime, timedelta

import pytest

from app.domain.models import User
from app.domain.rbac import TASK_ACTIONS, can
from app.domain.tasks import RULES, reminder_due, rule
from app.settings import load_studio

NOW = datetime(2026, 1, 1, 9, tzinfo=UTC)


@pytest.mark.parametrize("code", RULES)
def test_each_rule(code):
    cfg = load_studio()
    draft = rule(
        code,
        "event:1",
        cfg,
        NOW,
        body="synthetic",
        requester_id=10,
        starts_at=NOW + timedelta(days=1),
        score=3,
        rank=1,
    )
    assert draft is not None and draft.code == code
    assert draft == rule(
        code,
        "event:1",
        cfg,
        NOW,
        body="synthetic",
        requester_id=10,
        starts_at=NOW + timedelta(days=1),
        score=3,
        rank=1,
    )
    minutes = cfg.tasks.due_minutes[RULES[code]]
    expected = NOW + timedelta(minutes=minutes) if minutes else None
    assert draft.due_at == (NOW + timedelta(days=1) if code == "R2" else expected)


@pytest.mark.parametrize("code,extra", [("R5", {"score": 4}), ("R6", {"rank": 6})])
def test_filtered_rules(code, extra):
    assert (
        rule(code, "source", load_studio(), NOW, body="synthetic", requester_id=10, **extra) is None
    )


@pytest.mark.parametrize("code", ["R7", "R9", "R2", "R3", "R5", "R6"])
def test_invalid_sources(code):
    with pytest.raises(ValueError):
        rule(code, "source", load_studio(), NOW, body="synthetic")


@pytest.mark.parametrize("action", TASK_ACTIONS)
@pytest.mark.parametrize(
    "role,owner,revoked,allowed",
    [
        ("admin", False, False, True),
        ("admin", True, False, True),
        ("client", False, False, False),
        ("teacher", False, False, False),
        ("admin", False, True, False),
        ("client", True, False, False),
    ],
)
def test_queue_permissions(action, role, owner, revoked, allowed):
    user = User(telegram_user_id=1, role=role, is_owner=owner, access_revoked=revoked)
    assert can(user, action) is allowed
    assert not can(user, action, user)


@pytest.mark.parametrize("hours,expected", [(0, False), (1, True), (10, False), (18, False)])
def test_reminder_working_hours(hours, expected):
    assert reminder_due(NOW, NOW + timedelta(hours=hours), load_studio()) is expected
