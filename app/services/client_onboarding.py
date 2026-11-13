"""Serialized consent, news and binding. No live YCLIENTS schema is assumed."""

import asyncio
import json
import re
from datetime import datetime
from typing import Any

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db.models import kv, users
from app.domain.client_matching import match_phone, normalize_phone
from app.domain.models import OutboxDraft, User
from app.services.access import AccessDenied, AccessService
from app.services.client_lookup import ClientLookup, UnavailableClientLookup
from app.services.client_tasks import ClientTasks
from app.services.outbox import enqueue
from app.settings import StudioConfig
from app.texts import ru


def button(text: str, data: str) -> dict[str, str]:
    return {"text": text, "callback_data": data}


class ClientService(AccessService):
    def __init__(
        self,
        engine: AsyncEngine,
        owner_id: int,
        cfg: StudioConfig,
        lookup: ClientLookup | None = None,
    ):
        super().__init__(engine, owner_id, cfg)
        self.lookup = lookup if lookup is not None else UnavailableClientLookup()
        self.tasks = ClientTasks(self)

    async def state(self, db: AsyncConnection, actor: int) -> dict[str, Any]:
        value = (
            await db.execute(select(kv.c.value).where(kv.c.key == f"client:state:{actor}"))
        ).scalar_one_or_none()
        return json.loads(value) if value else {}

    async def save(self, db: AsyncConnection, actor: int, state: dict[str, Any]) -> None:
        from sqlalchemy.dialects.sqlite import insert

        statement = insert(kv).values(key=f"client:state:{actor}", value=json.dumps(state))
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[kv.c.key], set_={"value": json.dumps(state)}
            )
        )

    async def fresh(self, db: AsyncConnection, actor: int, request: str) -> bool:
        from sqlalchemy.dialects.sqlite import insert

        result = await db.execute(
            insert(kv)
            .values(key=json.dumps(["client:request", actor, request]), value="done")
            .on_conflict_do_nothing()
            .returning(kv.c.key)
        )
        return result.scalar_one_or_none() is not None

    async def reply(
        self,
        db: AsyncConnection,
        actor: int,
        key: str,
        text: str,
        now: datetime,
        markup: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"text": text}
        if markup is not None:
            payload["reply_markup"] = json.dumps(markup)
        await enqueue(
            db,
            OutboxDraft(
                telegram_user_id=actor,
                kind="client.onboarding",
                payload_min=payload,
                next_attempt_at=now,
                dedupe_key=json.dumps(["client", actor, key]),
            ),
            self.cfg,
            now,
        )

    async def prompt(
        self, db: AsyncConnection, actor: int, state: dict[str, Any], key: str, now: datetime
    ) -> None:
        phase, epoch = state["phase"], state["epoch"]
        if phase == "consent":
            await self.reply(
                db,
                actor,
                key,
                ru.CLIENT_CONSENT.format(url=self.cfg.studio.privacy_policy_url),
                now,
                {"inline_keyboard": [[button(ru.CLIENT_AGREE, f"cl:consent:{epoch}")]]},
            )
        elif phase == "news":
            await self.reply(
                db,
                actor,
                key,
                ru.CLIENT_NEWS,
                now,
                {
                    "inline_keyboard": [
                        [
                            button(ru.CLIENT_YES, f"cl:yes:{epoch}"),
                            button(ru.CLIENT_NO, f"cl:no:{epoch}"),
                        ]
                    ]
                },
            )
        elif phase == "contact":
            await self.reply(
                db,
                actor,
                key,
                ru.CLIENT_CONTACT,
                now,
                {
                    "keyboard": [[{"text": ru.CLIENT_SHARE, "request_contact": True}]],
                    "resize_keyboard": True,
                    "one_time_keyboard": True,
                },
            )
        else:
            await self.reply(db, actor, key, ru.CLIENT_WAIT, now, {"remove_keyboard": True})

    async def start(self, actor_id: int, message_id: int, source: str, now: datetime) -> None:
        async with self.transaction(now) as db:
            actor = await self.user(db, actor_id)
            if actor is None:
                # Creation still uses can() via authorize before any persistent effect.
                actor = User(telegram_user_id=actor_id, role="client")
                from app.domain.rbac import can

                if not can(actor, "client.onboard", actor):
                    raise AccessDenied()
                await db.execute(
                    users.insert().values(
                        telegram_user_id=actor_id,
                        role="client",
                        created_at=now,
                        source=source if re.fullmatch(r"src_[A-Za-z0-9_-]{1,60}", source) else None,
                    )
                )
            if actor.role != "client":
                await self.authorize(db, actor_id, "menu", actor)
                if await self.fresh(db, actor_id, f"start:{message_id}"):
                    await self.reply(
                        db, actor_id, f"start:{message_id}", ru.ROLE_MENU[actor.role], now
                    )
                return
            await self.authorize(db, actor_id, "client.onboard", actor)
            if not await self.fresh(db, actor_id, f"start:{message_id}"):
                return
            state = await self.state(db, actor_id)
            if actor.yclients_client_id:
                await self.reply(db, actor_id, f"start:{message_id}", ru.CLIENT_LINKED, now)
                return
            if not state:
                state = {"phase": "consent", "epoch": message_id}
            elif message_id <= state["epoch"]:
                return
            elif state["phase"] in ("consent", "news", "contact"):
                state["epoch"] = message_id
            await self.save(db, actor_id, state)
            await self.prompt(db, actor_id, state, f"start:{message_id}", now)

    async def choice(self, actor_id: int, action: str, epoch: int, now: datetime) -> bool:
        async with self.transaction(now) as db:
            actor = await self.user(db, actor_id)
            await self.authorize(db, actor_id, "client.onboard", actor)
            state = await self.state(db, actor_id)
            if state.get("epoch") != epoch:
                return False
            if action == "consent" and state["phase"] == "consent":
                await db.execute(
                    users.update()
                    .where(users.c.telegram_user_id == actor_id)
                    .values(consent_at=actor.consent_at or now if actor else now)
                )
                state["phase"] = "news"
            elif action in ("yes", "no") and state["phase"] == "news":
                await db.execute(
                    users.update()
                    .where(users.c.telegram_user_id == actor_id)
                    .values(news_opt_in=action == "yes")
                )
                state["phase"] = "contact"
            else:
                return False
            await self.save(db, actor_id, state)
            await self.prompt(db, actor_id, state, f"choice:{epoch}:{action}", now)
            return True

    async def lookup_id(self, phone: SecretStr) -> int | None:
        try:
            async with asyncio.timeout(self.cfg.yclients_http.timeout_seconds):
                return match_phone(phone.get_secret_value(), await self.lookup.candidates(phone))
        except Exception:
            # Lookup exceptions can contain PII. Never log or persist them.
            return None

    async def contact(
        self,
        actor_id: int,
        contact_user_id: int | None,
        phone: SecretStr,
        message_id: int,
        now: datetime,
    ) -> bool:
        async with self.transaction(now) as db:
            actor = await self.user(db, actor_id)
            await self.authorize(db, actor_id, "client.onboard", actor)
            state = await self.state(db, actor_id)
            if (
                contact_user_id != actor_id
                or state.get("phase") != "contact"
                or message_id <= state["epoch"]
                or actor is None
                or actor.consent_at is None
            ):
                return False
            normalized = normalize_phone(phone.get_secret_value())
            if normalized is None or not await self.fresh(db, actor_id, f"contact:{message_id}"):
                return False
            client_id = await self.lookup_id(SecretStr(normalized))
            if client_id is None:
                await self.tasks.open(db, actor_id, SecretStr(normalized), now)
                state["phase"] = "waiting"
                await self.reply(
                    db,
                    actor_id,
                    f"contact:{message_id}",
                    ru.CLIENT_WAIT,
                    now,
                    {"remove_keyboard": True},
                )
            else:
                await self.bind(db, actor_id, client_id, f"contact:{message_id}", now)
                state["phase"] = "linked"
            await self.save(db, actor_id, state)
            return True

    async def bind(
        self, db: AsyncConnection, user_id: int, client_id: int, key: str, now: datetime
    ) -> None:
        await db.execute(
            users.update()
            .where(users.c.telegram_user_id == user_id)
            .values(yclients_client_id=client_id)
        )
        await self.tasks.resolve(db, user_id, now)
        await self.audit(db, user_id, "client.linked", str(client_id), now)
        await self.reply(db, user_id, key, ru.CLIENT_LINKED, now, {"remove_keyboard": True})

    async def retry(self, actor_id: int, task_id: int, request: str, now: datetime) -> bool:
        async with self.transaction(now) as db:
            task = await self.tasks.get(db, task_id)
            if task is None:
                raise AccessDenied()
            target = await self.user(db, task["requester_id"])
            await self.authorize(db, actor_id, "client.retry", target)
            if target is None:
                raise AccessDenied()
            await self.authorize(db, target.telegram_user_id, "client.onboard", target)
            if (
                task["state"] != "open"
                or target.consent_at is None
                or not await self.fresh(db, actor_id, f"retry:{request}")
            ):
                return False
            client_id = await self.lookup_id(SecretStr(task["body"]))
            success = client_id is not None
            if client_id is not None:
                await self.bind(db, target.telegram_user_id, client_id, f"r4:{task_id}", now)
                state = await self.state(db, target.telegram_user_id)
                state["phase"] = "linked"
                await self.save(db, target.telegram_user_id, state)
            await self.reply(
                db,
                actor_id,
                f"retry:{request}",
                ru.CLIENT_RETRY_OK if success else ru.CLIENT_RETRY_FAILED,
                now,
            )
            return success
