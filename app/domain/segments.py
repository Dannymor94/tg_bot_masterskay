"""Pure, fail-closed announcement audience selection (ADR-18 / T-305a).

No database, Telegram, YCLIENTS, clock, or external side effects.
"""

from collections.abc import Sequence

from app.domain.models import User

SEGMENT_NAMES = frozenset(
    {"clients_all", "teachers_all", "teachers_renters", "everyone"}
)


def segment(users: Sequence[User], name: str) -> list[int]:
    """Return sorted Telegram IDs of current explicit news opt-ins.

    The full candidate set is validated before filtering. Contradictory
    duplicates fail closed rather than selecting a potentially stale record.
    """
    if name not in SEGMENT_NAMES:
        raise ValueError("unknown broadcast segment")
    unique: dict[int, User] = {}
    for user in users:
        identity = user.telegram_user_id
        if identity <= 0 or user.role not in ("client", "teacher", "admin"):
            raise ValueError("invalid segment identity")
        previous = unique.get(identity)
        if previous is not None and previous != user:
            raise ValueError("conflicting duplicate user")
        unique[identity] = user

    def eligible(user: User) -> bool:
        if not user.news_opt_in or user.access_revoked or user.blocked_at is not None:
            return False
        if name == "clients_all":
            return user.role == "client"
        if name == "teachers_all":
            return user.role == "teacher"
        if name == "teachers_renters":
            return user.role == "teacher" and user.teacher_kind == "renter"
        return True

    return sorted(identity for identity, user in unique.items() if eligible(user))


def broadcast_key(broadcast_id: int, user_id: int) -> str:
    """Stable outbox dedupe key: one logical broadcast delivery per recipient."""
    if (
        type(broadcast_id) is not int
        or type(user_id) is not int
        or broadcast_id <= 0
        or user_id <= 0
    ):
        raise ValueError("invalid broadcast identity")
    return f"{broadcast_id}:{user_id}"
