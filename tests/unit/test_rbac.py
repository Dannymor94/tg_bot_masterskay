from datetime import UTC, datetime
from itertools import product

import pytest

from app.domain.models import RecordSnapshot, User
from app.domain.rbac import ACTIONS, can

NOW = datetime(2026, 1, 1, tzinfo=UTC)
ACTORS = {
    "unknown": User(telegram_user_id=10, role="client"),
    "client": User(telegram_user_id=10, role="client", yclients_client_id=50),
    "client_consent": User(
        telegram_user_id=10, role="client", yclients_client_id=50, consent_at=NOW
    ),
    "client_blocked": User(
        telegram_user_id=10,
        role="client",
        yclients_client_id=50,
        consent_at=NOW,
        blocked_at=NOW,
    ),
    "teacher_revoked": User(
        telegram_user_id=10,
        role="teacher",
        teacher_kind="staff",
        yclients_staff_id=60,
        access_revoked=True,
    ),
    "teacher_pending": User(telegram_user_id=10, role="teacher"),
    "teacher": User(
        telegram_user_id=10, role="teacher", teacher_kind="staff", yclients_staff_id=60
    ),
    "renter": User(
        telegram_user_id=10, role="teacher", teacher_kind="renter", yclients_client_id=50
    ),
    "admin": User(telegram_user_id=10, role="admin"),
    "owner": User(telegram_user_id=10, role="admin", is_owner=True),
    "invalid_owner": User(telegram_user_id=10, role="client", is_owner=True),
    "revoked": User(telegram_user_id=10, role="admin", access_revoked=True),
    "blocked": User(telegram_user_id=10, role="admin", blocked_at=NOW),
}
RECORD = RecordSnapshot(
    record_id=1,
    staff_id=60,
    client_id=50,
    activity_id=None,
    service_id=None,
    hall_code=None,
    starts_at=NOW,
    ends_at=NOW.replace(hour=1),
    deleted=False,
    content_hash="x",
    seen_at=NOW,
)
TARGETS = {
    "none": None,
    "self": ACTORS["client"],
    "other_client": User(telegram_user_id=11, role="client", yclients_client_id=51),
    "other_admin": User(telegram_user_id=11, role="admin"),
    "other_teacher": User(telegram_user_id=11, role="teacher"),
    "other_owner": User(telegram_user_id=11, role="admin", is_owner=True),
    "record": RECORD,
}
# Independent explicit allow-list. Everything outside these cells must be denied.
ALLOWED = {
    "client.onboard": {(actor, "self") for actor in ("unknown", "client", "client_consent")},
    "client.retry": {
        (actor, target) for actor in ("admin", "owner") for target in ("self", "other_client")
    },
    "menu": {
        (actor, "self")
        for actor in ("client", "client_consent", "teacher", "renter", "admin", "owner")
    },
    "questions.ask": {(actor, "self") for actor in ("client_consent", "teacher", "renter")},
    "invite.admin": {("owner", "none")},
    "invite.teacher": {("owner", "none"), ("admin", "none")},
    "access.revoke": {("owner", "other_admin"), ("owner", "other_teacher")},
    "invite.redeem": {
        (actor, "self")
        for actor in (
            "unknown",
            "client",
            "client_consent",
            "teacher_pending",
            "teacher",
            "renter",
        )
    },
}


@pytest.mark.parametrize("action,actor,target", list(product(ALLOWED, ACTORS, TARGETS)))
def test_complete_action_matrix(action, actor, target):
    assert set(ACTIONS) == set(ALLOWED)
    assert can(ACTORS[actor], action, TARGETS[target]) is ((actor, target) in ALLOWED[action])


@pytest.mark.parametrize("actor", ACTORS.values())
def test_unknown_action_denied(actor):
    assert not can(actor, "future.action", actor)


def test_revoked_linked_user_denied():
    for actor in ACTORS.values():
        revoked = actor.model_copy(update={"access_revoked": True})
        assert all(not can(revoked, action, revoked) for action in ACTIONS)
