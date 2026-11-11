"""Pure Russian phone matching, with no logging or persistence of raw numbers."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable


@dataclass(frozen=True)
class Candidate:
    phone: str  # ephemeral, never log/store in bot DB
    external_id: int
    kind: str  # client | staff | renter


@dataclass(frozen=True)
class Match:
    status: str  # none | unique | ambiguous
    kind: str | None = None
    external_id: int | None = None


def _russian_phone(value: str) -> str:
    digits = "".join(ch for ch in value if "0" <= ch <= "9")
    if len(digits) == 10 and digits[0] == "9":
        digits = "7" + digits
    elif len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    if len(digits) != 11 or digits[0] != "7":
        raise ValueError("invalid Russian phone")
    return digits


def match_phone(value: str, candidates: Iterable[Candidate], *, teacher: bool = False) -> Match:
    """Teacher match prioritizes staff, then renter. Client path matches clients only.

    Different matching IDs at the same priority are ambiguous: send to R4.
    """
    phone = _russian_phone(value)
    matches = [c for c in candidates if _russian_phone(c.phone) == phone]
    preferred_kinds = ("staff", "renter") if teacher else ("client",)
    for kind in preferred_kinds:
        ids = sorted({c.external_id for c in matches if c.kind == kind and c.external_id > 0})
        if len(ids) == 1:
            return Match("unique", kind, ids[0])
        if len(ids) > 1:
            return Match("ambiguous")
    return Match("none")
