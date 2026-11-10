import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from telegram.error import NetworkError

from leetbot.leetcode import Activity, LeetCodeError, Question
from leetbot.storage import Completion, TrackingStore
from leetbot.tracker import Tracker, format_notification

QUESTIONS = {
    "two-sum": Question("1", "Two Sum", "two-sum", "Easy"),
    "add-two-numbers": Question("2", "Add Two Numbers", "add-two-numbers", "Medium"),
    "three-sum": Question("15", "3Sum", "three-sum", "Medium"),
}


class TrackerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.store = TrackingStore(self.path)
        self.api = AsyncMock()
        self.api.activity.return_value = Activity("Alice", {})
        self.api.question.side_effect = lambda slug: QUESTIONS[slug]
        self.send = AsyncMock()
        self.tracker = Tracker(self.store, self.api, self.send)

    async def test_add_baselines_unique_problem_ids_without_notifications(self):
        self.api.activity.return_value = Activity("Alice", {"two-sum": 1000, "three-sum": 1100})
        self.assertTrue(await self.tracker.add("alice"))
        await self.tracker.poll()
        self.send.assert_not_awaited()
        self.assertEqual(json.loads(self.store.state_path.read_text()), {"Alice": {
            "1": {"completed_at": 1000, "difficulty": "Easy"},
            "15": {"completed_at": 1100, "difficulty": "Medium"},
        }})

    async def test_add_is_case_insensitive_and_does_not_reset_state(self):
        self.assertTrue(await self.tracker.add("alice"))
        self.assertFalse(await self.tracker.add("ALICE"))
        self.assertEqual(self.api.activity.await_count, 1)

    async def test_poll_reports_only_difference_and_accumulates_successful_solves(self):
        self.api.activity.return_value = Activity("Alice", {"two-sum": 1000})
        await self.tracker.add("alice")
        self.api.activity.return_value = Activity("Alice", {"three-sum": 1100, "add-two-numbers": 1200, "two-sum": 1300})
        await self.tracker.poll()
        self.assertEqual(self.send.await_count, 2)
        self.assertEqual(self.store.solved_ids("Alice"), {"1", "2", "15"})
        self.assertEqual(self.store.completion_times("Alice"), {"1": 1000, "2": 1200, "15": 1100})
        self.assertEqual(self.store.completed_problems("Alice")["2"], Completion(1200, "Medium"))
        self.api.activity.return_value = Activity("Alice", {"three-sum": 1400})
        await self.tracker.poll()
        self.assertEqual(self.send.await_count, 2)
        self.assertEqual(self.store.solved_ids("Alice"), {"1", "2", "15"})

    async def test_successful_notification_is_sent_before_id_is_saved(self):
        await self.tracker.add("alice")
        self.api.activity.return_value = Activity("Alice", {"two-sum": 1000})

        async def send(text):
            self.assertEqual(self.store.solved_ids("Alice"), set())
            self.assertEqual(json.loads(self.store.state_path.read_text()), {"Alice": {}})

        self.send.side_effect = send
        await self.tracker.poll()
        self.send.assert_awaited_once()
        self.assertEqual(self.store.solved_ids("Alice"), {"1"})

    async def test_repeated_submissions_and_restarts_do_not_duplicate_notifications(self):
        await self.tracker.add("alice")
        self.api.activity.return_value = Activity("Alice", {"two-sum": 1000})
        await self.tracker.poll()
        self.send.assert_awaited_once()
        self.api.activity.return_value = Activity("Alice", {"two-sum": 2000})
        await Tracker(TrackingStore(self.path), self.api, self.send).poll()
        self.send.assert_awaited_once()
        self.assertEqual(TrackingStore(self.path).completion_times("Alice"), {"1": 1000})

    async def test_known_problem_stays_known_when_it_leaves_and_reenters_recent_activity(self):
        self.api.activity.return_value = Activity("Alice", {"two-sum": 1000})
        await self.tracker.add("alice")
        self.api.activity.return_value = Activity("Alice", {})
        await self.tracker.poll()
        self.api.activity.return_value = Activity("Alice", {"two-sum": 2000})
        await self.tracker.poll()
        self.send.assert_not_awaited()

    async def test_failed_send_is_not_saved_and_is_retried_after_restart(self):
        await self.tracker.add("alice")
        self.api.activity.return_value = Activity("Alice", {"two-sum": 1000})
        self.send.side_effect = NetworkError("Temporary failure")
        with self.assertLogs("leetbot.tracker", level="WARNING"):
            await self.tracker.poll()
        self.assertEqual(json.loads(self.store.state_path.read_text()), {"Alice": {}})
        self.send.reset_mock(side_effect=True)
        reloaded = TrackingStore(self.path)
        await Tracker(reloaded, self.api, self.send).poll()
        self.send.assert_awaited_once()
        self.assertEqual(reloaded.solved_ids("Alice"), {"1"})
        self.assertEqual(reloaded.completion_times("Alice"), {"1": 1000})
        self.assertEqual(reloaded.completed_problems("Alice")["1"].difficulty, "Easy")

    async def test_each_problem_is_saved_individually_and_failed_send_does_not_block_others(self):
        await self.tracker.add("alice")
        self.api.activity.return_value = Activity("Alice", {"two-sum": 1000, "add-two-numbers": 1100})

        async def send(text):
            if "#1 ·" in text:
                raise NetworkError("Temporary failure")

        self.send.side_effect = send
        with self.assertLogs("leetbot.tracker", level="WARNING"):
            await self.tracker.poll()
        self.assertEqual(self.store.solved_ids("Alice"), {"2"})
        self.send.reset_mock(side_effect=True)
        await self.tracker.poll()
        self.send.assert_awaited_once()
        self.assertIn("#1 ·", self.send.await_args.args[0])
        self.assertEqual(self.store.solved_ids("Alice"), {"1", "2"})

    async def test_failed_problem_that_leaves_recent_activity_is_not_queued(self):
        await self.tracker.add("alice")
        self.api.activity.return_value = Activity("Alice", {"two-sum": 1000})
        self.send.side_effect = NetworkError("Temporary failure")
        with self.assertLogs("leetbot.tracker", level="WARNING"):
            await self.tracker.poll()
        self.send.reset_mock(side_effect=True)
        self.api.activity.return_value = Activity("Alice", {})
        await Tracker(TrackingStore(self.path), self.api, self.send).poll()
        self.send.assert_not_awaited()
        self.assertEqual(self.store.solved_ids("Alice"), set())

    async def test_metadata_failure_does_not_save_problem(self):
        await self.tracker.add("alice")
        self.api.activity.return_value = Activity("Alice", {"two-sum": 1000})
        self.api.question.side_effect = LeetCodeError("Unavailable")
        with self.assertLogs("leetbot.tracker", level="WARNING"):
            await self.tracker.poll()
        self.send.assert_not_awaited()
        self.assertEqual(self.store.solved_ids("Alice"), set())

    async def test_api_failure_preserves_state(self):
        self.api.activity.return_value = Activity("Alice", {"two-sum": 1000})
        await self.tracker.add("alice")
        before = self.store.state_path.read_bytes()
        self.api.activity.side_effect = LeetCodeError("Unavailable")
        with self.assertLogs("leetbot.tracker", level="WARNING"):
            await self.tracker.poll()
        self.send.assert_not_awaited()
        self.assertEqual(self.store.state_path.read_bytes(), before)

    async def test_remove_deletes_json_entry_and_readd_uses_new_baseline(self):
        await self.tracker.add("alice")
        self.assertTrue(await self.tracker.remove("ALICE"))
        self.assertFalse(await self.tracker.remove("alice"))
        self.assertEqual(json.loads(self.store.state_path.read_text()), {})
        self.api.activity.return_value = Activity("Alice", {"two-sum": 1000})
        await self.tracker.poll()
        self.send.assert_not_awaited()
        self.assertTrue(await self.tracker.add("alice"))
        await self.tracker.poll()
        self.send.assert_not_awaited()
        self.assertEqual(self.store.solved_ids("Alice"), {"1"})

    async def test_state_write_failure_after_send_leaves_problem_unsaved_for_retry(self):
        await self.tracker.add("alice")
        self.api.activity.return_value = Activity("Alice", {"two-sum": 1000})
        with patch("leetbot.storage.os.replace", side_effect=OSError("Disk full")):
            with self.assertLogs("leetbot.tracker", level="WARNING"):
                await self.tracker.poll()
        self.send.assert_awaited_once()
        self.assertEqual(self.store.solved_ids("Alice"), set())
        self.assertEqual(TrackingStore(self.path).solved_ids("Alice"), set())
        await self.tracker.poll()
        self.assertEqual(self.send.await_count, 2)
        self.assertEqual(self.store.solved_ids("Alice"), {"1"})

    async def test_removal_during_activity_request_prevents_notification(self):
        await self.tracker.add("alice")

        async def activity(username):
            await self.tracker.remove(username)
            return Activity("Alice", {"two-sum": 1000})

        self.api.activity.side_effect = activity
        await self.tracker.poll()
        self.send.assert_not_awaited()
        self.assertEqual(self.store.users(), [])

    async def test_failed_baseline_fetch_does_not_create_user(self):
        self.api.activity.return_value = Activity("Alice", {"two-sum": 1000})
        self.api.question.side_effect = LeetCodeError("Unavailable")
        with self.assertRaises(LeetCodeError):
            await self.tracker.add("alice")
        self.assertFalse(self.store.contains("alice"))


class FormattingTests(unittest.TestCase):
    def test_notification_has_all_requested_details_and_escapes_html(self):
        message = format_notification("Alice_123", Question("1", "A < B & C", "two-sum", "Hard"))
        self.assertIn("Alice_123", message)
        self.assertIn("#1", message)
        self.assertIn("https://leetcode.com/problems/two-sum/", message)
        self.assertIn("A &lt; B &amp; C", message)
        self.assertIn("🔴 <b>Hard</b>", message)
