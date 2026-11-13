"""Internal client lookup DTOs; these do not describe any YCLIENTS response."""

import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Candidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    client_id: int = Field(gt=0)
    phone: SecretStr = Field(repr=False)


def normalize_phone(phone: str) -> str | None:
    if re.fullmatch(r"[+0-9 ()-]+", phone) is None:
        return None
    digits = re.sub(r"[^0-9]", "", phone)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        return "+" + digits
    return None


def match_phone(phone: str, candidates: Sequence[Candidate]) -> int | None:
    normalized = normalize_phone(phone)
    if normalized is None:
        return None
    matches = {
        candidate.client_id
        for candidate in candidates
        if normalize_phone(candidate.phone.get_secret_value()) == normalized
    }
    return next(iter(matches)) if len(matches) == 1 else None
