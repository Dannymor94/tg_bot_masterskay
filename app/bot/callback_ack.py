"""Acknowledge Telegram inline-button presses without sending chat messages.

Unlike durable user-visible messages, this transient Bot API protocol response
cannot be queued in the outbox: Telegram expects it immediately.
"""

import logging

from aiogram import Bot
from aiogram.types import CallbackQuery

logger = logging.getLogger(__name__)


async def acknowledge(query: CallbackQuery, bot: Bot) -> None:
    """Stop Telegram's loading spinner even if a later service action fails."""
    try:
        await bot.answer_callback_query(callback_query_id=query.id)
    except Exception:
        # The service can still commit its durable outbox work if the ACK fails.
        # Never log the callback payload or the raw Telegram exception.
        logger.warning("Telegram callback acknowledgment failed")
