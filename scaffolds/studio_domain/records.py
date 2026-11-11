"""Deterministic record delta detection; never infer deletion from absence."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from collections.abc import Iterable

from .models import Record


@dataclass(frozen=True)
class Event:
    kind: str
    record_id: int
    dedupe_key: str


def _index(records: Iterable[Record]) -> dict[int, Record]:
    result: dict[int, Record] = {}
    for record in records:
        if record.record_id in result:
            raise ValueError(f"duplicate record_id {record.record_id}")
        result[record.record_id] = record
    return result


def _event(kind: str, before: Record | None, after: Record) -> Event:
    # Includes transition fields so a genuine later reschedule produces a new key.
    raw = "|".join(
        [
            kind,
            str(after.record_id),
            before.starts_at.isoformat() if before else "",
            before.ends_at.isoformat() if before else "",
            after.starts_at.isoformat(),
            after.ends_at.isoformat(),
        ]
    )
    return Event(kind, after.record_id, sha256(raw.encode()).hexdigest())


def diff(previous: Iterable[Record], current: Iterable[Record]) -> tuple[Event, ...]:
    """Compare complete *normalized* snapshots from an identical window.

    Missing record IDs deliberately produce no cancellation. The T-104 sync
    service must confirm deletion via verified record.get or fallback before
    constructing an explicit cancelled record.
    """
    before, after = _index(previous), _index(current)
    events: list[Event] = []
    for record_id in sorted(after):
        new = after[record_id]
        old = before.get(record_id)
        if old is None:
            if not new.cancelled:
                events.append(_event("record.created", None, new))
        elif not old.cancelled and new.cancelled:
            events.append(_event("record.cancelled", old, new))
        elif old.cancelled and not new.cancelled:
            events.append(_event("record.created", old, new))
        elif not new.cancelled and (
            old.starts_at != new.starts_at or old.ends_at != new.ends_at
        ):
            events.append(_event("record.rescheduled", old, new))
    return tuple(events)
