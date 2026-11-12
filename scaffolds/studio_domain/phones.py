"""Pure Russian phone matching; never log or persist input phone values."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    phone: str  # ephemeral, from verified API response only
    external_id: int
    kind: str  # client | staff | renter, renter status proven by service


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
    """Fail invalid *request*, skip malformed candidates; duplicates mean R4.

    For teachers, staff wins over renter per ADR-10; service must prove that
    a 'renter' has an actual rental within the last 180 days.
    """
    phone = _russian_phone(value)
    matches: list[Candidate] = []
    for candidate in candidates:
        try:
            candidate_phone = _russian_phone(candidate.phone)
        except (ValueError, TypeError):
            continue
        if candidate_phone == phone:
            matches.append(candidate)
    preferred = ("staff", "renter") if teacher else ("client",)
    for kind in preferred:
        ids = sorted({c.external_id for c in matches if c.kind == kind and c.external_id > 0})
        if len(ids) > 1:
            return Match("ambiguous")
        if len(ids) == 1:
            return Match("unique", kind, ids[0])
    return Match("none")
