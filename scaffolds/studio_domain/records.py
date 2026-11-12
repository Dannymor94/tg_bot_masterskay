"""Pure record transitions; occurrence numbers are supplied by durable sync state."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timezone
from hashlib import sha256

from .models import Record


@dataclass(frozen=True)
class Event:
    kind: str
    record_id: int
    occurrence: int
    dedupe_key: str


def _index(records: Iterable[Record]) -> dict[int, Record]:
    indexed: dict[int, Record] = {}
    for record in records:
        if record.record_id in indexed:
            raise ValueError(f"duplicate record_id {record.record_id}")
        indexed[record.record_id] = record
    return indexed


def _fingerprint(record: Record) -> tuple[object, ...]:
    """Normalized business identity; stable across UTC/local representations."""
    return (
        record.starts_at.astimezone(timezone.utc).isoformat(),
        record.ends_at.astimezone(timezone.utc).isoformat(),
        record.client_id, record.staff_id, record.activity_id,
        record.service_id, record.hall_code, record.kind, record.cancelled,
    )


def _event(kind: str, record: Record, occurrence: int) -> Event:
    raw = f"{occurrence}|{record.record_id}|{kind}"
    return Event(kind, record.record_id, occurrence, sha256(raw.encode()).hexdigest())


def diff(
    previous: Iterable[Record], current: Iterable[Record], *, occurrence: int
) -> tuple[Event, ...]:
    """Compare two complete normalized snapshots of the same sync window.

    occurrence is a positive, durable transition sequence provided by T-104.
    Persist the sequence, event(s), snapshots and outbox atomically; reuse the
    same sequence for retries of that transaction. Increment for the next
    committed set of changes. This is NOT an API/YCLIENTS revision field.
    Missing rows never imply cancellation: verify via record.get/fallback.
    """
    if isinstance(occurrence, bool) or not isinstance(occurrence, int) or occurrence <= 0:
        raise ValueError("occurrence must be a positive durable integer")
    before, after = _index(previous), _index(current)
    events: list[Event] = []
    for record_id in sorted(after):
        new, old = after[record_id], before.get(record_id)
        if old is None:
            if not new.cancelled:
                events.append(_event("record.created", new, occurrence))
        elif not old.cancelled and new.cancelled:
            events.append(_event("record.cancelled", new, occurrence))
        elif old.cancelled and not new.cancelled:
            events.append(_event("record.created", new, occurrence))
        elif not new.cancelled and _fingerprint(old) != _fingerprint(new):
            # Matches product notification codes; includes move between halls.
            events.append(_event("record.rescheduled", new, occurrence))
    return tuple(events)
