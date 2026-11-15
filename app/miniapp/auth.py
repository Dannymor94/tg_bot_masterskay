"""Validate signed Telegram Mini App initData (never trust initDataUnsafe)."""

import hashlib
import hmac
import json
from datetime import datetime
from urllib.parse import parse_qsl

MAX_AGE_SECONDS = 3600
MAX_DATA_LENGTH = 16384


class InvalidInitData(ValueError):
    """Missing, malformed, tampered-with or expired Telegram authorization."""


def telegram_user_id(raw: str, bot_token: str, now: datetime) -> int:
    if not raw or len(raw) > MAX_DATA_LENGTH or not bot_token or now.utcoffset() is None:
        raise InvalidInitData()
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
        fields = dict(pairs)
        if len(fields) != len(pairs) or not {"hash", "auth_date", "user"} <= fields.keys():
            raise InvalidInitData()
        received_hash = fields["hash"]
        if len(received_hash) != 64 or any(c not in "0123456789abcdef" for c in received_hash):
            raise InvalidInitData()
        check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(fields.items())
            if key not in ("hash", "signature")
        )
        secret_key = hmac.digest(b"WebAppData", bot_token.encode(), "sha256")
        digest = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(digest, received_hash):
            raise InvalidInitData()
        auth_date = fields["auth_date"]
        if not auth_date.isascii() or not auth_date.isdecimal():
            raise InvalidInitData()
        age = now.timestamp() - int(auth_date)
        if not 0 <= age <= MAX_AGE_SECONDS:
            raise InvalidInitData()
        user = json.loads(fields["user"])
        if not isinstance(user, dict) or user.get("is_bot") is True:
            raise InvalidInitData()
        identifier = user.get("id")
        if type(identifier) is not int or not 0 < identifier < 2**53:
            raise InvalidInitData()
        return identifier
    except (TypeError, ValueError, UnicodeError, OverflowError) as error:
        raise InvalidInitData() from error
