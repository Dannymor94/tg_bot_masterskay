"""Offline reference tests, synthetic IDs only. Run: python -m unittest discover -s tests."""

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from scaffolds.studio_domain.access import Actor, Target, can
from scaffolds.studio_domain.models import Activity, Hall, Record
from scaffolds.studio_domain.occupancy import free_slots, occupancy, price
from scaffolds.studio_domain.records import diff

TZ = ZoneInfo("Europe/Moscow")
START = datetime(2026, 11, 16, 10, tzinfo=TZ)


def record(record_id=1, start=START, cancelled=False, staff_id=101):
    return Record(record_id, start, start + timedelta(hours=1), staff_id=staff_id, cancelled=cancelled)


class DiffTests(unittest.TestCase):
    def test_created_once(self):
        events = diff([], [record()])
        self.assertEqual([x.kind for x in events], ["record.created"])
        self.assertEqual(diff([record()], [record()]), ())
        self.assertEqual(events, diff([], [record()]))

    def test_reschedule_and_cancel(self):
        moved = record(start=START + timedelta(hours=1))
        self.assertEqual(diff([record()], [moved])[0].kind, "record.rescheduled")
        self.assertEqual(diff([record()], [record(cancelled=True)])[0].kind, "record.cancelled")
        self.assertEqual(diff([record()], []), ())  # no fabricated cancellation

    def test_deterministic_order_and_duplicates(self):
        self.assertEqual([e.record_id for e in diff([], [record(3), record(1)])], [1, 3])
        with self.assertRaises(ValueError):
            diff([record(), record()], [])

    def test_invalid_timestamp(self):
        with self.assertRaises(ValueError):
            Record(1, datetime(2026, 1, 1), START)


class OccupancyTests(unittest.TestCase):
    def setUp(self):
        self.hall = Hall("big", 101, frozenset({201}), 900, 1000, 1000)

    def test_rent_and_activity(self):
        activity = Activity(START + timedelta(hours=2), START + timedelta(hours=3), frozenset({201}))
        busy = occupancy([record()], [activity], [self.hall])
        self.assertEqual(len(busy), 2)
        free = free_slots(busy, "big", START, START + timedelta(hours=4))
        self.assertEqual([x.starts_at.hour for x in free], [11, 13])

    def test_cancelled_not_occupied(self):
        self.assertEqual(occupancy([record(cancelled=True)], [], [self.hall]), ())

    def test_price(self):
        self.assertEqual(price(self.hall, START), 900)  # Mon 10
        self.assertEqual(price(self.hall, START.replace(hour=19)), 1000)
        self.assertEqual(price(self.hall, START + timedelta(days=5)), 1000)

    def test_overlap(self):
        busy = occupancy([record()], [], [self.hall])
        free = free_slots(busy, "big", START + timedelta(minutes=30), START + timedelta(hours=2, minutes=30))
        self.assertEqual([x.starts_at.hour for x in free], [11])


class AccessTests(unittest.TestCase):
    def test_owner_and_admin(self):
        owner = Actor(1, "admin", is_owner=True)
        admin = Actor(2, "admin")
        self.assertTrue(can(owner, "admin.revoke"))
        self.assertFalse(can(admin, "admin.revoke"))
        self.assertTrue(can(admin, "task.take"))

    def test_client_cannot_see_other(self):
        client = Actor(1, "client", yclients_client_id=10)
        self.assertTrue(can(client, "record.view", Target(client_id=10)))
        self.assertFalse(can(client, "record.view", Target(client_id=11)))
        self.assertFalse(can(client, "record.view"))

    def test_teacher_scope(self):
        staff = Actor(1, "teacher", teacher_kind="staff", yclients_staff_id=20)
        renter = Actor(2, "teacher", teacher_kind="renter", yclients_client_id=30)
        self.assertTrue(can(staff, "record.view", Target(staff_id=20)))
        self.assertFalse(can(staff, "record.view", Target(staff_id=21)))
        self.assertTrue(can(renter, "record.view", Target(client_id=30)))
        self.assertFalse(can(renter, "record.view", Target(client_id=31)))

    def test_default_deny(self):
        guest = Actor(3, "guest")
        self.assertFalse(can(guest, "schedule.view"))
        self.assertFalse(can(Actor(3, "client"), "secret.admin"))


if __name__ == "__main__":
    unittest.main()
