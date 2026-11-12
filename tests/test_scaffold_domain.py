"""Offline unit and regression tests for reference-only studio code."""

import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from scaffolds.studio_domain.access import Actor, Target, can
from scaffolds.studio_domain.models import Activity, Hall, Interval, Record
from scaffolds.studio_domain.occupancy import free_slots, occupancy, price
from scaffolds.studio_domain.records import diff

TZ = ZoneInfo("Europe/Moscow")
START = datetime(2026, 11, 16, 10, tzinfo=TZ)


def record(record_id=1, start=START, cancelled=False, staff_id=101, hall_code="big", kind="rental"):
    return Record(
        record_id, start, start + timedelta(hours=1), staff_id=staff_id,
        hall_code=hall_code, kind=kind, cancelled=cancelled,
    )


class DiffTests(unittest.TestCase):
    def test_created_once(self):
        events = diff([], [record()], occurrence=1)
        self.assertEqual([x.kind for x in events], ["record.created"])
        self.assertEqual(diff([record()], [record()], occurrence=2), ())
        self.assertEqual(events, diff([], [record()], occurrence=1))

    def test_reschedule_cancel_and_missing(self):
        moved = record(start=START + timedelta(hours=1))
        self.assertEqual(diff([record()], [moved], occurrence=2)[0].kind, "record.rescheduled")
        self.assertEqual(diff([record()], [record(cancelled=True)], occurrence=2)[0].kind, "record.cancelled")
        self.assertEqual(diff([record()], [], occurrence=2), ())

    def test_hall_change_same_time(self):
        changed = record(staff_id=102, hall_code="small")
        self.assertEqual(diff([record()], [changed], occurrence=4)[0].kind, "record.rescheduled")

    def test_service_change_same_time(self):
        old = record()
        changed = Record(1, START, START + timedelta(hours=1), staff_id=101,
                         hall_code="big", kind="rental", service_id=99)
        self.assertEqual(diff([old], [changed], occurrence=4)[0].kind, "record.rescheduled")

    def test_distinct_repeated_transition_occurrences(self):
        a, b = record(), record(start=START + timedelta(hours=2))
        first = diff([a], [b], occurrence=2)[0]
        reverse = diff([b], [a], occurrence=3)[0]
        repeat = diff([a], [b], occurrence=4)[0]
        self.assertNotEqual(first.dedupe_key, repeat.dedupe_key)
        self.assertNotEqual(first.dedupe_key, reverse.dedupe_key)
        self.assertEqual(first, diff([a], [b], occurrence=2)[0])

    def test_invalid_occurrence(self):
        for value in (0, -1, True, "2", 1.3):
            with self.subTest(value=value), self.assertRaises(ValueError):
                diff([], [record()], occurrence=value)

    def test_order_duplicates_and_timezone(self):
        self.assertEqual([e.record_id for e in diff([], [record(3), record(1)], occurrence=2)], [1, 3])
        with self.assertRaises(ValueError):
            diff([record(), record()], [], occurrence=2)
        shifted = Record(1, START.astimezone(timezone.utc),
                         (START + timedelta(hours=1)).astimezone(timezone.utc),
                         staff_id=101, hall_code="big", kind="rental")
        self.assertEqual(diff([record()], [shifted], occurrence=3), ())

    def test_invalid_timestamp(self):
        with self.assertRaises(ValueError):
            Record(1, datetime(2026, 1, 1), START)


class OccupancyTests(unittest.TestCase):
    def setUp(self):
        self.hall = Hall("big", 101, frozenset({201}), 900, 1000, 1000, mapping_verified=True)

    def busy(self, records=(), activities=()):
        return occupancy(records, activities, [self.hall],
                         records_complete=True, activities_complete=True)

    def test_rent_and_activity(self):
        activity = Activity(START + timedelta(hours=2), START + timedelta(hours=3), frozenset({201}))
        busy = self.busy([record()], [activity])
        self.assertEqual(len(busy.intervals), 2)
        free = free_slots(busy, "big", START, START + timedelta(hours=4))
        self.assertEqual([x.starts_at.hour for x in free], [11, 13])

    def test_cancelled_not_occupied(self):
        self.assertEqual(self.busy([record(cancelled=True)]).intervals, ())

    def test_price(self):
        self.assertEqual(price(self.hall, START, studio_timezone=TZ), 900)
        self.assertEqual(price(self.hall, START.replace(hour=19), studio_timezone=TZ), 1000)
        self.assertEqual(price(self.hall, START + timedelta(days=5), studio_timezone=TZ), 1000)

    def test_utc_instant_price(self):
        utc = datetime(2026, 11, 16, 15, 30, tzinfo=timezone.utc)
        self.assertEqual(price(self.hall, utc, studio_timezone=TZ), 1000)

    def test_overlap(self):
        free = free_slots(self.busy([record()]), "big", START + timedelta(minutes=30),
                          START + timedelta(hours=2, minutes=30))
        self.assertEqual([x.starts_at.hour for x in free], [11])

    def test_reject_missing_coverage(self):
        with self.assertRaises(ValueError):
            occupancy([record()], [], [self.hall], records_complete=True)
        with self.assertRaises(ValueError):
            occupancy([], [], [self.hall], activities_complete=True)
        with self.assertRaises(ValueError):
            occupancy([], [], [Hall("big", None, frozenset(), 900, 1000, 1000)],
                      records_complete=True, activities_complete=True)
        with self.assertRaises(ValueError):
            free_slots(self.busy(), "small", START, START + timedelta(hours=1))

    def test_reject_rental_unmapped(self):
        with self.assertRaises(ValueError):
            self.busy([record(staff_id=999, hall_code="unknown")])


class AccessTests(unittest.TestCase):
    def test_owner_admin(self):
        self.assertTrue(can(Actor(1, "admin", is_owner=True), "admin.revoke"))
        self.assertFalse(can(Actor(2, "admin"), "admin.revoke"))
        self.assertTrue(can(Actor(2, "admin"), "task.take"))

    def test_client_cannot_see_other(self):
        client = Actor(1, "client", yclients_client_id=10)
        self.assertTrue(can(client, "record.view", Target(client_id=10)))
        self.assertFalse(can(client, "record.view", Target(client_id=11)))
        self.assertFalse(can(client, "record.view"))

    def test_staff_scope(self):
        staff = Actor(1, "teacher", teacher_kind="staff", yclients_staff_id=20)
        self.assertTrue(can(staff, "record.view", Target(staff_id=20, record_kind="class")))
        self.assertFalse(can(staff, "record.view", Target(staff_id=21, record_kind="class")))
        self.assertFalse(can(staff, "record.view", Target(staff_id=20, record_kind="rental")))

    def test_renter_only_own_rental(self):
        renter = Actor(2, "teacher", teacher_kind="renter", yclients_client_id=30)
        self.assertTrue(can(renter, "record.view", Target(client_id=30, record_kind="rental")))
        self.assertFalse(can(renter, "record.view", Target(client_id=30, record_kind="class")))
        self.assertFalse(can(renter, "record.view", Target(client_id=30, record_kind="unknown")))
        self.assertFalse(can(renter, "record.view", Target(client_id=31, record_kind="rental")))

    def test_default_deny(self):
        self.assertFalse(can(Actor(3, "guest"), "schedule.view"))
        self.assertFalse(can(Actor(3, "client"), "secret.admin"))


if __name__ == "__main__":
    unittest.main()
