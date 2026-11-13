"""Fail-closed port. A real adapter requires verified, anonymized T-103 fixtures."""

from collections.abc import Sequence
from typing import Protocol

from pydantic import SecretStr

from app.domain.client_matching import Candidate


class LookupUnavailable(Exception):
    pass


class ClientLookup(Protocol):
    async def candidates(self, phone: SecretStr) -> Sequence[Candidate]: ...


class UnavailableClientLookup:
    async def candidates(self, phone: SecretStr) -> Sequence[Candidate]:
        raise LookupUnavailable()
