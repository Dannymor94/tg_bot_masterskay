"""Default-deny permissions declared by T-201 and client onboarding T-301."""

from app.domain.models import RecordSnapshot, User

TASK_ACTIONS = ("tasks.list", "tasks.create", "tasks.take", "tasks.done", "tasks.reply")

ACTIONS = (
    "menu",
    "invite.admin",
    "invite.teacher",
    "invite.redeem",
    "access.revoke",
    "client.onboard",
    "client.retry",
    "broadcast.create",
    "broadcast.manage",
)


def can(user: User, action: str, target: RecordSnapshot | User | None = None) -> bool:
    if user.telegram_user_id <= 0 or user.access_revoked or user.blocked_at is not None:
        return False
    if user.is_owner and user.role != "admin":
        return False
    own = isinstance(target, User) and target.telegram_user_id == user.telegram_user_id
    if action in TASK_ACTIONS:
        return target is None and user.role == "admin"
    if action == "client.onboard":
        return own and user.role == "client" and not user.is_owner
    if action == "client.retry":
        return user.role == "admin" and isinstance(target, User) and target.role == "client"
    if action in ("broadcast.create", "broadcast.manage"):
        return target is None and user.role == "admin"
    if action == "invite.redeem":
        return own and user.role in ("client", "teacher") and not user.is_owner
    if action == "menu":
        linked = (
            user.role == "admin"
            or (user.role == "client" and (user.yclients_client_id or 0) > 0)
            or (
                user.role == "teacher"
                and (
                    (user.teacher_kind == "staff" and (user.yclients_staff_id or 0) > 0)
                    or (user.teacher_kind == "renter" and (user.yclients_client_id or 0) > 0)
                )
            )
        )
        return own and linked
    if action == "invite.admin":
        return target is None and user.role == "admin" and user.is_owner
    if action == "invite.teacher":
        return target is None and user.role == "admin"
    if action == "access.revoke":
        return (
            user.role == "admin"
            and user.is_owner
            and isinstance(target, User)
            and not own
            and not target.is_owner
            and target.role in ("admin", "teacher")
        )
    return False
