import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from scaffolds.studio_domain.marketing import Subscriber, segment
from scaffolds.studio_domain.phones import Candidate, match_phone
from scaffolds.studio_domain.models import Hall, Interval
from scaffolds.studio_domain.windows import HistoricalWeek, empty_windows

TZ = ZoneInfo("Europe/Moscow")
MONDAY = datetime(2026, 11, 16, tzinfo=TZ)


class MarketingTests(unittest.TestCase):
    def test_news_opt_in(self):
        users = [Subscriber(2, "teacher", True, "renter"),
                 Subscriber(1, "client", False), Subscriber(3, "client", True)]
        self.assertEqual(segment(users, "everyone"), (2, 3))
        self.assertEqual(segment(users, "teachers_renters"), (2,))
        self.assertEqual(segment(users, "clients_all"), (3,))

    def test_blocked_excluded(self):
        users = [Subscriber(1, "client", True, blocked_at=datetime(2026, 11, 1, tzinfo=TZ)),
                 Subscriber(2, "client", True)]
        self.assertEqual(segment(users, "everyone"), (2,))

    def test_unknown_segment(self):
        with self.assertRaises(ValueError):
            segment([], "any")


class PhoneTests(unittest.TestCase):
    def test_client_unique(self):
        c = [Candidate("8 (999) 000-00-01", 12, "client")]
        self.assertEqual(match_phone("+7 999 000 00 01", c).external_id, 12)

    def test_teacher_priority(self):
        c = [Candidate("89990000001", 12, "client"), Candidate("79990000001", 18, "staff")]
        self.assertEqual(match_phone("89990000001", c, teacher=True).kind, "staff")

    def test_ambiguous(self):
        c = [Candidate("89990000001", 12, "client"), Candidate("79990000001", 13, "client")]
        self.assertEqual(match_phone("79990000001", c).status, "ambiguous")

    def test_invalid_candidate_does_not_abort(self):
        c = [Candidate("INVALID", 12, "client"), Candidate("79990000001", 18, "client")]
        self.assertEqual(match_phone("89990000001", c).external_id, 18)

    def test_invalid_request(self):
        with self.assertRaises(ValueError):
            match_phone("hello", [])


class WindowsTests(unittest.TestCase):
    def setUp(self):
        self.hall = Hall("big", 101, frozenset({201}), 900, 1000, 1000, mapping_verified=True)
        self.history = [HistoricalWeek(MONDAY - timedelta(weeks=8-i), (), complete=True)
                        for i in range(8)]

    def windows(self, weeks=None, halls=None):
        return empty_windows(
            self.history if weeks is None else weeks,
            [self.hall] if halls is None else halls,
            MONDAY, studio_timezone=TZ, open_hour=7, close_hour=22, max_occupied_weeks=1,
        )

    def test_all_empty_windows(self):
        windows = self.windows()
        self.assertEqual(len(windows), 7)
        self.assertTrue(all((w.start_hour, w.end_hour) == (7, 22) for w in windows))
        self.assertEqual(next(w for w in windows if w.weekday == 0).potential_revenue, 13_900)

    def test_weekly_occupation_breaks_window(self):
        for i in range(2):
            week = self.history[i]
            start = week.monday.replace(hour=10)
            self.history[i] = HistoricalWeek(week.monday, (Interval("big", start, start + timedelta(hours=1)),), complete=True)
        monday_windows = [w for w in self.windows() if w.weekday == 0]
        self.assertEqual([(w.start_hour, w.end_hour) for w in monday_windows], [(11, 22), (7, 10)])

    def test_incomplete_history_rejected(self):
        with self.assertRaises(ValueError):
            self.windows(weeks=self.history[:-1])
        self.history[0] = HistoricalWeek(self.history[0].monday, (), complete=False)
        with self.assertRaises(ValueError):
            self.windows()

    def test_unverified_hall_rejected(self):
        hall = Hall("big", 101, frozenset({201}), 900, 1000, 1000)
        with self.assertRaises(ValueError):
            self.windows(halls=[hall])

    def test_invalid_week_order_rejected(self):
        with self.assertRaises(ValueError):
            self.windows(weeks=self.history[::-1])


if __name__ == "__main__":
    unittest.main()
