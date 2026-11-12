import asyncio
import sys

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from app.jobs.outbox import TelegramSender
from app.runtime import serve
from app.settings import Settings, StudioConfig, load_settings, load_studio


async def run(settings: Settings, studio: StudioConfig) -> None:
    session = AiohttpSession(
        proxy=str(settings.telegram_proxy_url) if settings.telegram_proxy_url else None,
        timeout=studio.delivery.request_timeout_seconds,
    )
    async with Bot(token=settings.telegram_bot_token.get_secret_value(), session=session) as bot:
        await serve(studio.runtime.heartbeat_interval_seconds, send=TelegramSender(bot), cfg=studio)


def main() -> int:
    try:
        settings = load_settings()
        studio = load_studio()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    asyncio.run(run(settings, studio))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
