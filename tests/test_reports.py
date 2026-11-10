from datetime import datetime, timezone
from pathlib import Path
import re
import tempfile
import unittest

from leetbot.reports import MOSCOW, statistics_messages
from leetbot.storage import Completion, TrackingStore


class StatisticsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = TrackingStore(Path(directory.name))
        self.now = datetime(2026, 11, 10, 5, tzinfo=timezone.utc)
        self.end_timestamp = int(self.now.timestamp())

    def test_default_window_boundaries_and_all_tracked_users(self):
        cutoff = self.end_timestamp - 86400
        self.store.add("Alice", {
            "1": Completion(cutoff, "Easy"),
            "2": Completion(cutoff + 1, "Medium"),
            "3": Completion(self.end_timestamp, "Hard"),
            "4": Completion(cutoff - 1, "Easy"),
            "5": Completion(self.end_timestamp + 1, "Easy"),
        })
        self.store.add("Bob", {"6": Completion(cutoff - 1, "Easy")})
        self.store.add("Charlie", {})
        report = statistics_messages(self.store, now=self.now)[0]
        self.assertIn("last 1 day", report)
        self.assertIn('>Alice</a> · 3\n  🟢🟡🔴', report)
        self.assertIn('>Bob</a> · 🤡', report)
        self.assertIn('>Charlie</a> · 🤡', report)
        self.assertIn("Total: 3 problems", report)
        self.assertNotIn("🕒", report)
        self.assertNotIn("tracked users", report)

    def test_n_days_uses_a_rolling_window_and_sorts_by_count(self):
        self.store.add("Alice", {"1": Completion(self.end_timestamp - 2 * 86400, "Easy")})
        self.store.add("Bob", {
            "2": Completion(self.end_timestamp - 6 * 86400, "Hard"),
            "3": Completion(self.end_timestamp - 3600, "Medium"),
        })
        self.store.add("Charlie", {"4": Completion(self.end_timestamp - 8 * 86400, "Easy")})
        daily = statistics_messages(self.store, now=self.now)[0]
        weekly = statistics_messages(self.store, 7, now=self.now)[0]
        self.assertIn('>Alice</a> · 🤡', daily)
        self.assertIn('>Bob</a> · 1\n  🟡', daily)
        self.assertIn('>Alice</a> · 1\n  🟢', weekly)
        self.assertIn('>Bob</a> · 2\n  🔴🟡', weekly)
        self.assertIn('>Charlie</a> · 🤡', weekly)
        self.assertLess(weekly.index('>Bob</a>'), weekly.index('>Alice</a>'))
        self.assertIn("Total: 3 problems", weekly)

    def test_compact_format_and_markers_follow_completion_order(self):
        self.store.add("Alice", {
            "3": Completion(self.end_timestamp - 60, "Hard"),
            "1": Completion(self.end_timestamp - 3600, "Easy"),
            "2": Completion(self.end_timestamp - 120, "Medium"),
        })
        self.store.add("Bob", {"4": Completion(0, None)})
        self.store.add("Charlie", {"5": Completion(self.end_timestamp - 30, "Easy")})
        report = statistics_messages(self.store, now=self.now)[0]
        plain_text = re.sub(r"<[^>]+>", "", report)
        self.assertEqual(plain_text, (
            "📊 LeetCode statistics · last 1 day\n\n"
            "• Alice · 3\n"
            "  🟢🟡🔴\n"
            "• Charlie · 1\n"
            "  🟢\n"
            "• Bob · 🤡\n\n"
            "Total: 4 problems"
        ))

    def test_sort_uses_task_count_without_difficulty_weights(self):
        self.store.add("Alice", {str(i): Completion(self.end_timestamp - i, "Easy") for i in range(3)})
        self.store.add("Bob", {str(i): Completion(self.end_timestamp - i, "Hard") for i in range(2)})
        report = statistics_messages(self.store, now=self.now)[0]
        self.assertLess(report.index('>Alice</a>'), report.index('>Bob</a>'))

    def test_zero_timestamps_never_count_even_in_windows_before_1970(self):
        self.store.add("Alice", {"1": Completion(0, None), "2": Completion(0, "Hard")})
        report = statistics_messages(self.store, 36500, now=self.now)[0]
        self.assertIn('>Alice</a> · 🤡', report)
        self.assertIn("Total: 0 problems", report)
        self.assertNotIn("🔴", report)

    def test_utc_and_moscow_reference_times_produce_same_report(self):
        self.store.add("Alice", {"1": Completion(self.end_timestamp - 3600, "Easy")})
        self.assertEqual(
            statistics_messages(self.store, now=self.now),
            statistics_messages(self.store, now=self.now.astimezone(MOSCOW)),
        )

    def test_empty_tracking_list(self):
        messages = statistics_messages(self.store, now=self.now)
        self.assertEqual(len(messages), 1)
        self.assertIn("Nobody is tracked", messages[0])
        self.assertIn("Total: 0 problems", messages[0])

    def test_invalid_days_and_unrepresentable_windows(self):
        for days in (0, -1, 1.5, True, "7", 10**30):
            with self.subTest(days=days), self.assertRaises(ValueError):
                statistics_messages(self.store, days, now=self.now)

    def test_naive_reference_time_is_rejected(self):
        with self.assertRaises(ValueError):
            statistics_messages(self.store, now=datetime(2026, 11, 10))

    def test_multiple_messages_include_every_user_and_one_group_total(self):
        for index in range(80):
            self.store.add(f"user{index:02d}", {"1": Completion(self.end_timestamp, "Easy")})
        messages = statistics_messages(self.store, now=self.now)
        self.assertGreater(len(messages), 1)
        self.assertEqual(sum(text.count("</a> ·") for text in messages), 80)
        self.assertEqual(sum(text.count("Total: 80 problems") for text in messages), 1)
        self.assertTrue(all(len(text.encode("utf-16-le")) // 2 <= 3900 for text in messages))
        for index in range(80):
            self.assertEqual(sum(text.count(f">user{index:02d}</a>") for text in messages), 1)

    def test_long_marker_history_fits_telegram_limits_without_losing_tasks(self):
        self.store.add("Alice", {
            str(index): Completion(self.end_timestamp - index, "Easy") for index in range(2500)
        })
        messages = statistics_messages(self.store, now=self.now)
        self.assertGreater(len(messages), 1)
        self.assertEqual(sum(text.count("🟢") for text in messages), 2500)
        self.assertTrue(all(len(text.encode("utf-16-le")) // 2 <= 3900 for text in messages))
        self.assertIn("Total: 2500 problems", messages[-1])

    def test_removed_users_are_excluded(self):
        self.store.add("Alice", {"1": Completion(self.end_timestamp, "Easy")})
        self.store.add("Bob", {"2": Completion(self.end_timestamp, "Medium")})
        self.store.remove("Alice")
        report = statistics_messages(self.store, now=self.now)[0]
        self.assertNotIn('>Alice</a>', report)
        self.assertIn('>Bob</a>', report)
        self.assertIn("Total: 1 problems", report)
