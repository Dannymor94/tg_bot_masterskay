import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, Contact, Message, Update, User

from app.bot.client import build_router
from app.jobs.outbox import TelegramSender
from app.services.client_onboarding import ClientService

NOW = datetime(2026, 1, 1, 9, tzinfo=UTC)


def message(chat=2, actor=2, kind="private", bot=False, **kwargs):
    return Message(
        message_id=10,
        date=NOW,
        chat=Chat(id=chat, type=kind),
        from_user=User(id=actor, first_name="Synthetic", is_bot=bot),
        **kwargs,
    )


def test_transport_identity_and_safe_errors(caplog):
    async def scenario():
        service = AsyncMock(spec=ClientService)
        dispatcher = Dispatcher()
        dispatcher.include_router(build_router(service, lambda: NOW))
        bot = Bot("123:synthetic")
        try:
            for msg in [
                message(chat=3, text="/start src_test"),
                message(chat=-10, kind="group", text="/start"),
                message(bot=True, text="/start"),
                message(text="/start invite_synthetic"),
            ]:
                await dispatcher.feed_update(bot, Update(update_id=1, message=msg))
            service.start.assert_not_called()
            await dispatcher.feed_update(
                bot, Update(update_id=2, message=message(text="/start src_test"))
            )
            service.start.assert_awaited_once_with(2, 10, "src_test", NOW)
            for msg in [message(chat=3), message(chat=-10, kind="group"), message(bot=True)]:
                await dispatcher.feed_update(
                    bot,
                    Update(
                        update_id=3,
                        message=msg.model_copy(
                            update={
                                "contact": Contact(
                                    phone_number="+79000000001", first_name="Synthetic", user_id=2
                                )
                            }
                        ),
                    ),
                )
            service.contact.assert_not_called()
            contact = message(
                contact=Contact(phone_number="+79000000001", first_name="Synthetic", user_id=3)
            )
            # The service receives the real contact owner, not a claimed command ID.
            service.contact.side_effect = RuntimeError("+79000000001")
            await dispatcher.feed_update(bot, Update(update_id=4, message=contact))
            assert service.contact.call_args.args[0:2] == (2, 3)
            for msg in [message(chat=3), message(chat=-10, kind="group")]:
                await dispatcher.feed_update(
                    bot,
                    Update(
                        update_id=5,
                        callback_query=CallbackQuery(
                            id="forged",
                            from_user=User(id=2, first_name="Synthetic", is_bot=False),
                            chat_instance="synthetic",
                            message=msg,
                            data="cl:retry:1",
                        ),
                    ),
                )
            service.retry.assert_not_called()
            for data in ["cl:retry:1", "cl:consent:10", "cl:no:10", "cl:yes:10"]:
                await dispatcher.feed_update(
                    bot,
                    Update(
                        update_id=6,
                        callback_query=CallbackQuery(
                            id=data,
                            from_user=User(id=2, first_name="Synthetic", is_bot=False),
                            chat_instance="synthetic",
                            message=message(),
                            data=data,
                        ),
                    ),
                )
            service.retry.assert_awaited_once_with(2, 1, "cl:retry:1", NOW)
            assert service.choice.await_count == 3
            assert "+79000000001" not in caplog.text
            assert "RuntimeError" not in caplog.text
        finally:
            await bot.session.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "markup",
    [
        {"inline_keyboard": [[{"text": "Synthetic", "callback_data": "cl:consent:10"}]]},
        {"keyboard": [[{"text": "Synthetic", "request_contact": True}]], "resize_keyboard": True},
        {"remove_keyboard": True},
    ],
)
def test_sender_delivers_keyboard_from_outbox(markup, monkeypatch):
    async def scenario():
        bot = Bot("123:synthetic")
        send = AsyncMock()
        monkeypatch.setattr(bot, "send_message", send)
        try:
            await TelegramSender(bot)(
                {
                    "telegram_user_id": 2,
                    "kind": "client.onboarding",
                    "payload_min": {"text": "Synthetic", "reply_markup": json.dumps(markup)},
                }
            )
            delivered = send.call_args.kwargs["reply_markup"].model_dump(exclude_none=True)
            for key, value in markup.items():
                assert delivered[key] == value
        finally:
            await bot.session.close()

    asyncio.run(scenario())
