"""T-305a: pure audience and dedupe contract, using synthetic User records."""

from itertools import permutations

import pytest

from app.domain.models import User
from app.domain.segments import SEGMENT_NAMES, broadcast_key, segment
from app.settings import load_studio


def user(
    identity: int,
    role: str = "client",
    *,
    subscribed: bool = True,
    revoked: bool = False,
    blocked: bool = False,
    kind: str | None = None,
) -> User:
    # Test-only synthetic data. Production types reject invalid roles/kinds.
    return User.model_validate(
        {
            "telegram_user_id": identity,
            "role": role,
            "news_opt_in": subscribed,
            "access_revoked": revoked,
            "blocked_at": "2026-11-13T00:00:00+00:00" if blocked else None,
            "teacher_kind": kind,
        }
    )


@pytest.fixture
def sample() -> list[User]:
    return [
        user(7, "client"),
        user(2, "teacher", kind="renter"),
        user(9, "teacher", kind="staff"),
        user(6, "admin"),
        user(3, "client", subscribed=False),
        user(11, "client", revoked=True),
        user(4, "teacher", kind="renter", blocked=True),
    ]


def test_config_matches_approved_segment_names():
    assert set(load_studio().broadcasts.segments) == SEGMENT_NAMES


@pytest.mark.parametrize(
    "name,expected",
    [
        ("clients_all", [7]),
        ("teachers_all", [2, 9]),
        ("teachers_renters", [2]),
        ("everyone", [2, 6, 7, 9]),
    ],
)
def test_all_segments_require_opt_in_and_fresh_access(sample, name, expected):
    assert segment(sample, name) == expected


def test_order_permutations_do_not_change_recipients(sample):
    selected = sample[:4]
    expected = segment(selected, "everyone")
    for variant in permutations(selected):
        assert segment(variant, "everyone") == expected


def test_same_user_duplicate_is_deduplicated(sample):
    assert segment(sample + [sample[0]], "clients_all") == [7]


def test_conflicting_user_duplicate_fails_even_if_unsubscribed(sample):
    contradictory = user(7, "client", subscribed=False)
    with pytest.raises(ValueError):
        segment(sample + [contradictory], "everyone")


@pytest.mark.parametrize("name", ["", "clients", "teachers", "ALL", "other"])
def test_unknown_segments_are_not_silently_empty(sample, name):
    with pytest.raises(ValueError):
        segment(sample, name)


def test_bad_ids_are_rejected_even_for_opted_out_user():
    with pytest.raises(ValueError):
        segment([user(0, subscribed=False)], "clients_all")


def test_unsubscribing_is_immediate():
    current = user(5, subscribed=True)
    unsubscribed = current.model_copy(update={"news_opt_in": False})
    assert segment([current], "everyone") == [5]
    assert segment([unsubscribed], "everyone") == []


def test_broadcast_dedupe_is_stable_and_per_recipient():
    assert broadcast_key(17, 99) == "17:99"
    assert broadcast_key(17, 99) == broadcast_key(17, 99)
    assert len({broadcast_key(17, 99), broadcast_key(18, 99), broadcast_key(17, 100)}) == 3


@pytest.mark.parametrize(
    "values",
    [(0, 1), (1, 0), (-1, 1), (1, -2), (True, 1), (1, False)],
)
def test_invalid_broadcast_identity(values):
    with pytest.raises(ValueError):
        broadcast_key(*values)
