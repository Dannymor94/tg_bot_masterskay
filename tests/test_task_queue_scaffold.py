"""Reference scaffold tests; stdlib only, no network or database required."""

import json
import unittest
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from scaffolds.task_queue.decisions import (
    TASK_CODES,
    TaskView,
    can_reply,
    can_take,
    reminder_due,
    should_clear_closed_text,
    task_key,
)

MOSCOW = ZoneInfo("Europe/Moscow")
T0 = datetime(2026, 11, 13, 7, tzinfo=UTC)  # 10:00 Moscow
OPEN = time(7)
CLOSE = time(22)


class TaskGuardTests(unittest.TestCase):
    def test_all_rules_have_stable_keys(self):
        self.assertEqual(TASK_CODES, {"R1", "R2", "R3", "R4", "R5", "R6", "R8"})
        for rule in TASK_CODES:
            with self.subTest(rule=rule):
                k = task_key(rule, "event:123:transition:1")
                self.assertEqual(json.loads(k), ["task", rule, "event:123:transition:1"])
                self.assertEqual(k, task_key(rule, "event:123:transition:1"))

    def test_keys_cannot_collide_when_input_contains_separators(self):
        self.assertNotEqual(task_key("R1", "a:b:c"), task_key("R2", "a:b:c"))
        self.assertNotEqual(task_key("R1", "a:b"), task_key("R1", "a:b:c"))
        self.assertEqual(json.loads(task_key("R8", 'a"\\b'))[-1], 'a"\\b')

    def test_keys_reject_missing_identity_and_unknown_rules(self):
        for rule, source in [("R7", "x"), ("R9", "x"), ("R1", ""), ("R1", "  ")]:
            with self.subTest(rule=rule, source=source), self.assertRaises(ValueError):
                task_key(rule, source)

    def test_state_matrix(self):
        self.assertTrue(can_take("open"))
        self.assertFalse(can_take("taken"))
        self.assertFalse(can_take("done"))
        with self.assertRaises(ValueError):
            can_take("cancelled")  # type: ignore[arg-type]

    def test_only_r3_supports_reply(self):
        for code in TASK_CODES:
            self.assertEqual(can_reply(code), code == "R3")
        with self.assertRaises(ValueError):
            can_reply("R9")

    def due(self, task, now, *, after=60):
        return reminder_due(
            task,
            now,
            after_minutes=after,
            studio_timezone=MOSCOW,
            work_open=OPEN,
            work_close=CLOSE,
        )

    def test_reminder_due_at_boundary(self):
        t = TaskView("R3", "open", T0)
        self.assertFalse(self.due(t, T0 + timedelta(minutes=59)))
        self.assertTrue(self.due(t, T0 + timedelta(minutes=60)))

    def test_reminder_only_when_open_and_not_taken(self):
        t = TaskView("R1", "open", T0 - timedelta(hours=12))
        self.assertFalse(self.due(t, datetime(2026, 11, 13, 3, tzinfo=UTC)))  # 06:00
        self.assertTrue(self.due(t, datetime(2026, 11, 13, 4, tzinfo=UTC)))  # 07:00
        self.assertFalse(self.due(t, datetime(2026, 11, 13, 19, tzinfo=UTC)))  # 22:00
        self.assertFalse(self.due(TaskView("R1", "taken", t.created_at), T0))
        self.assertFalse(self.due(TaskView("R1", "done", t.created_at), T0))

    def test_reminder_uses_last_ping_for_rate_limit(self):
        t = TaskView("R3", "open", T0 - timedelta(hours=10), last_ping_at=T0)
        self.assertFalse(self.due(t, T0 + timedelta(minutes=59)))
        self.assertTrue(self.due(t, T0 + timedelta(minutes=60)))

    def test_reminder_rejects_invalid_settings_and_naive_time(self):
        t = TaskView("R3", "open", T0)
        with self.assertRaises(ValueError):
            self.due(t, T0, after=0)
        with self.assertRaises(ValueError):
            self.due(t, datetime(2026, 11, 13, 7))
        with self.assertRaises(ValueError):
            self.due(TaskView("R3", "open", datetime(2026, 11, 13)), T0)

    def test_ttl_boundary_and_non_done(self):
        done_at = T0
        t = TaskView("R3", "done", T0 - timedelta(days=10), done_at=done_at)
        self.assertFalse(should_clear_closed_text(t, T0 + timedelta(days=89), ttl_days=90))
        self.assertTrue(should_clear_closed_text(t, T0 + timedelta(days=90), ttl_days=90))
        self.assertFalse(
            should_clear_closed_text(
                TaskView("R3", "taken", T0, done_at=done_at),
                T0 + timedelta(days=100),
                ttl_days=90,
            )
        )
        self.assertFalse(
            should_clear_closed_text(
                TaskView("R3", "done", T0), T0 + timedelta(days=100), ttl_days=90
            )
        )

    def test_ttl_checks_aware_and_valid_settings(self):
        t = TaskView("R3", "done", T0, done_at=T0)
        with self.assertRaises(ValueError):
            should_clear_closed_text(t, datetime(2026, 11, 13, 7), ttl_days=90)
        with self.assertRaises(ValueError):
            should_clear_closed_text(t, T0, ttl_days=0)


if __name__ == "__main__":
    unittest.main()
