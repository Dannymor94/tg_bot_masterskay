"""Short-lived, single-use Telegram ForceReply flows for Rich Message actions.

All identity comes from the private chat sender. Prompt provenance is checked
against a random per-user state marker, never client-declared action fields.
"""

import json
import secrets
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from app.db.models import kv, outbox
from app.domain.calendar import selectable
from app.domain.models import OutboxDraft
from app.services.access import AccessDenied, AccessService
from app.services.outbox import enqueue

STATE_PREFIX = "ui:compose:"
PROMPT_TTL = timedelta(minutes=30)
TITLES = {
    "task": "Новая задача",
    "question": "Вопрос администратору",
    "booking": "Запрос записи на занятие",
    "rent": "Запрос аренды зала",
    "announce": "Новая рассылка",
}


class RichComposeService(AccessService):
    async def feedback(self, actor_id: int, request_id: str, now: datetime) -> None:
        """Generic response, without echoing rejected text or leaking role details."""
        async with self.transaction(now) as db:
            await enqueue(
                db,
                OutboxDraft(
                    telegram_user_id=actor_id,
                    kind="ui.compose.feedback",
                    payload_min={
                        "text": (
                            "Запрос не принят. Проверьте доступ и длину текста; откройте меню "
                            "и нажмите кнопку ещё раз."
                        )
                    },
                    next_attempt_at=now,
                    dedupe_key=f"ui.compose.feedback:{actor_id}:{request_id}",
                ),
                self.cfg,
                now,
            )

    async def begin(
        self,
        actor_id: int,
        request_id: str,
        action: str,
        now: datetime,
        *,
        segment: str = "",
        selected_date: str = "",
        menu_message_id: int = 0,
    ) -> None:
        if action not in TITLES or not request_id or len(request_id) > 100 or menu_message_id <= 0:
            raise ValueError("invalid compose request")
        if action == "announce" and segment not in self.cfg.broadcasts.segments:
            raise ValueError("unknown announcement segment")
        parsed: date | None = None
        if selected_date:
            if action not in ("booking", "rent"):
                raise ValueError("date not supported for action")
            try:
                parsed = date.fromisoformat(selected_date)
            except ValueError as error:
                raise ValueError("invalid request date") from error
            today = now.astimezone(ZoneInfo(self.cfg.studio.timezone)).date()
            if parsed.isoformat() != selected_date or not selectable(parsed, today):
                raise ValueError("request date outside calendar horizon")
        async with self.transaction(now) as db:
            actor = await self.user(db, actor_id)
            if action == "task":
                await self.authorize(db, actor_id, "tasks.create")
            elif action == "announce":
                await self.authorize(db, actor_id, "broadcast.create")
            else:
                actor = await self.authorize(db, actor_id, "menu", actor)
                if action in ("question", "booking"):
                    if actor.role not in ("client", "teacher") or (
                        actor.role == "client" and actor.consent_at is None
                    ):
                        raise AccessDenied()
                elif action == "rent" and actor.role != "teacher":
                    raise AccessDenied()
            key = f"ui.compose.prompt:{actor_id}:{request_id}"
            if (
                await db.execute(select(outbox.c.id).where(outbox.c.dedupe_key == key))
            ).scalar_one_or_none() is not None:
                return
            marker = secrets.token_hex(5)
            context = f" на {parsed.strftime('%d.%m.%Y')}" if parsed is not None else ""
            label = f"{TITLES[action]}{context} · {marker}"
            state = {
                "action": action,
                "segment": segment,
                "selected_date": selected_date,
                "label": label,
                "expires": (now + PROMPT_TTL).astimezone(UTC).isoformat(),
                "menu_message_id": menu_message_id,
            }
            statement = insert(kv).values(
                key=STATE_PREFIX + str(actor_id),
                value=json.dumps(state, separators=(",", ":"), ensure_ascii=False),
            )
            await db.execute(
                statement.on_conflict_do_update(
                    index_elements=[kv.c.key], set_={"value": statement.excluded.value}
                )
            )
            await enqueue(
                db,
                OutboxDraft(
                    telegram_user_id=actor_id,
                    kind="ui.compose.prompt",
                    payload_min={
                        "text": label,
                        "reply_markup": json.dumps(
                            {
                                "force_reply": True,
                                "input_field_placeholder": "Введите текст и отправьте",
                            },
                            ensure_ascii=False,
                        ),
                    },
                    next_attempt_at=now,
                    dedupe_key=key,
                ),
                self.cfg,
                now,
            )

    async def claim(self, actor_id: int, reply_to_text: str, now: datetime) -> dict | None:
        """Atomically remove one matching prompt; replayed replies do nothing."""
        if not reply_to_text or actor_id <= 0:
            return None
        async with self.transaction(now) as db:
            key = STATE_PREFIX + str(actor_id)
            value = (
                await db.execute(select(kv.c.value).where(kv.c.key == key))
            ).scalar_one_or_none()
            if value is None:
                return None
            try:
                state = json.loads(value)
                expires = datetime.fromisoformat(state["expires"])
            except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                return None
            if state.get("label") != reply_to_text:
                return None
            if now > expires:
                await db.execute(kv.delete().where(kv.c.key == key))
                await enqueue(
                    db,
                    OutboxDraft(
                        telegram_user_id=actor_id,
                        kind="ui.compose.feedback",
                        payload_min={"text": "Время ответа истекло. Нажмите кнопку ещё раз."},
                        next_attempt_at=now,
                        dedupe_key=f"ui.compose.expired:{actor_id}:{state['label']}",
                    ),
                    self.cfg,
                    now,
                )
                return None
            await db.execute(kv.delete().where(kv.c.key == key))
            return state
