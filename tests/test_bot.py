from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from telegram import Update
from telegram.request import HTTPXRequest

from leetbot.bot import Config, build_application, daily_statistics_job
from leetbot.leetcode import Activity, LeetCodeError, Question, UserNotFound
from leetbot.storage import Completion


class TelegramIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Exercise real command routing and HTML sending without contacting Telegram."""

    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.calls = []

        async def request(_request, url, method, request_data=None, **kwargs):
            endpoint = url.rsplit("/", 1)[-1]
            params = request_data.parameters if request_data else {}
            self.calls.append((endpoint, params))
            if endpoint == "getMe":
                result = {"id": 123, "is_bot": True, "first_name": "LeetBot", "username": "LeetTrackerBot"}
            elif endpoint == "setMyCommands":
                result = True
            elif endpoint == "sendMessage":
                chat_id = params["chat_id"]
                result = {
                    "message_id": len(self.calls), "date": 100,
                    "chat": {"id": chat_id, "type": "supergroup" if chat_id < 0 else "private"},
                    "text": params["text"],
                }
            else:
                raise AssertionError(f"Unexpected Telegram API request: {endpoint}")
            return 200, json.dumps({"ok": True, "result": result}).encode()

        patcher = patch.object(HTTPXRequest, "do_request", request)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = build_application(Config("123:test-token", 60, Path(directory.name), chat_id=-1001))
        self.addAsyncCleanup(self.close_application)
        self.api = AsyncMock()
        self.api.activity.return_value = Activity("Alice", {})
        self.api.question.return_value = Question("1", "Two Sum", "two-sum", "Easy")
        self.tracker = self.app.bot_data["tracker"]
        self.tracker.leetcode = self.api
        await self.app.initialize()
        await self.app.post_init(self.app)

    async def close_application(self):
        await self.app.shutdown()
        await self.app.post_shutdown(self.app)

    async def command(self, text, chat_id=-1001):
        update = Update.de_json({
            "update_id": len(self.calls),
            "message": {
                "message_id": 1, "date": 100,
                "from": {"id": 42, "is_bot": False, "first_name": "Tester"},
                "chat": {"id": chat_id, "type": "supergroup" if chat_id < 0 else "private"},
                "text": text,
                "entities": [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}],
            },
        }, self.app.bot)
        await self.app.process_update(update)

    def sent(self):
        return [params for endpoint, params in self.calls if endpoint == "sendMessage"]

    async def test_add_list_notification_and_remove_through_real_handlers(self):
        await self.command("/add@LeetTrackerBot alice")
        self.assertIn("Now tracking", self.sent()[-1]["text"])
        await self.command("/list")
        self.assertIn("Alice", self.sent()[-1]["text"])
        self.api.activity.return_value = Activity("Alice", {"two-sum": 1000})
        await self.tracker.poll()
        notification = self.sent()[-1]
        self.assertEqual(notification["chat_id"], -1001)
        self.assertEqual(notification["parse_mode"], "HTML")
        self.assertTrue(notification["link_preview_options"]["is_disabled"])
        self.assertIn("#1 · Two Sum", notification["text"])
        self.assertIn("🟢 <b>Easy</b>", notification["text"])
        await self.command("/remove alice")
        self.assertIn("Stopped tracking", self.sent()[-1]["text"])
        self.assertEqual(self.tracker.store.users(), [])
        self.assertEqual(json.loads(self.tracker.store.state_path.read_text()), {})

    async def test_other_groups_cannot_manage_shared_tracking_list(self):
        await self.command("/add alice", chat_id=-1002)
        self.assertIn("different group", self.sent()[-1]["text"])
        self.api.activity.assert_not_awaited()

    async def test_missing_group_configuration_explains_how_to_set_it(self):
        self.app.bot_data["chat_id"] = None
        await self.command("/add alice")
        self.assertIn("TELEGRAM_CHAT_ID=-1001", self.sent()[-1]["text"])
        self.api.activity.assert_not_awaited()

    async def test_help_shows_group_id_for_configuration(self):
        await self.command("/start")
        self.assertIn("Group ID: <code>-1001</code>", self.sent()[-1]["text"])

    async def test_commands_require_group_and_username(self):
        await self.command("/add alice", chat_id=42)
        self.assertIn("Telegram group", self.sent()[-1]["text"])
        await self.command("/add")
        self.assertIn("Usage:", self.sent()[-1]["text"])
        await self.command("/remove alice bob")
        self.assertIn("Usage:", self.sent()[-1]["text"])
        self.api.activity.assert_not_awaited()

    async def test_missing_user_and_api_failure_get_friendly_replies(self):
        self.api.activity.side_effect = UserNotFound("missing")
        await self.command("/add missing")
        self.assertIn("No public LeetCode profile", self.sent()[-1]["text"])
        self.api.activity.side_effect = LeetCodeError("Unavailable")
        with self.assertLogs("leetbot.bot", level="WARNING"):
            await self.command("/add alice")
        self.assertIn("unavailable", self.sent()[-1]["text"])
        self.assertEqual(self.tracker.store.users(), [])

    async def test_empty_list_and_help(self):
        await self.command("/list")
        self.assertIn("Nobody is tracked", self.sent()[-1]["text"])
        await self.command("/help", chat_id=42)
        self.assertIn("/add", self.sent()[-1]["text"])
        self.assertIn("/remove", self.sent()[-1]["text"])

    async def test_stat_default_and_explicit_days(self):
        now = int(datetime.now(timezone.utc).timestamp())
        self.tracker.store.add("Alice", {"1": Completion(now - 3600, "Easy"), "2": Completion(now - 2 * 86400, "Hard")})
        self.tracker.store.add("Bob", {})
        await self.command("/stat")
        daily = self.sent()[-1]["text"]
        self.assertIn("last 1 day", daily)
        self.assertIn('>Alice</a> · 1\n  🟢', daily)
        self.assertIn('>Bob</a> · 🤡', daily)
        await self.command("/stat@LeetTrackerBot 7")
        self.assertIn("last 7 days", self.sent()[-1]["text"])
        self.assertIn('>Alice</a> · 2\n  🔴🟢', self.sent()[-1]["text"])
        self.api.activity.assert_not_awaited()

    async def test_stat_invalid_arguments_get_usage(self):
        for text in ("/stat 0", "/stat -1", "/stat abc", "/stat 1.5", "/stat 1 2", "/stat " + "9" * 30):
            with self.subTest(text=text):
                await self.command(text)
                self.assertIn("Usage:", self.sent()[-1]["text"])
        self.api.activity.assert_not_awaited()

    async def test_stat_requires_configured_group(self):
        await self.command("/stat", chat_id=42)
        self.assertIn("Telegram group", self.sent()[-1]["text"])
        await self.command("/stat 7", chat_id=-1002)
        self.assertIn("different group", self.sent()[-1]["text"])

    async def test_daily_job_sends_default_report_to_configured_group(self):
        now = int(datetime.now(timezone.utc).timestamp())
        self.tracker.store.add("Alice", {"1": Completion(now - 3600, "Easy"), "2": Completion(now - 2 * 86400, "Hard")})
        self.tracker.store.add("Bob", {})
        await daily_statistics_job(SimpleNamespace(application=self.app))
        report = self.sent()[-1]
        self.assertEqual(report["chat_id"], -1001)
        self.assertEqual(report["parse_mode"], "HTML")
        self.assertIn("last 1 day", report["text"])
        self.assertIn('>Alice</a> · 1\n  🟢', report["text"])
        self.assertIn('>Bob</a> · 🤡', report["text"])

    async def test_daily_schedule_fires_at_0800_moscow_every_day(self):
        jobs = self.app.job_queue.get_jobs_by_name("daily-statistics")
        self.assertEqual(len(jobs), 1)
        trigger = jobs[0].job.trigger
        self.assertEqual(str(trigger.timezone), "Europe/Moscow")
        before = datetime(2026, 11, 10, 4, 59, tzinfo=timezone.utc)
        first = trigger.get_next_fire_time(None, before)
        self.assertEqual(first.astimezone(timezone.utc), datetime(2026, 11, 10, 5, tzinfo=timezone.utc))
        second = trigger.get_next_fire_time(first, first + timedelta(seconds=1))
        self.assertEqual(second.astimezone(timezone.utc), datetime(2026, 11, 11, 5, tzinfo=timezone.utc))

    async def test_command_menu_includes_stat(self):
        commands = next(params["commands"] for endpoint, params in self.calls if endpoint == "setMyCommands")
        self.assertIn("stat", [item["command"] for item in commands])


class ConfigTests(unittest.TestCase):
    def setUp(self):
        loader = patch("leetbot.bot.load_dotenv")
        loader.start()
        self.addCleanup(loader.stop)

    def test_missing_token_has_actionable_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "TELEGRAM_BOT_TOKEN"):
                Config.from_env()

    def test_invalid_poll_interval_is_rejected(self):
        for value in ("0", "-1", "abc", "9"):
            with self.subTest(value=value), patch.dict(os.environ, {
                "TELEGRAM_BOT_TOKEN": "123:test", "POLL_INTERVAL_SECONDS": value,
            }, clear=True):
                with self.assertRaisesRegex(ValueError, "POLL_INTERVAL_SECONDS"):
                    Config.from_env()

    def test_configuration_resolves_relative_data_directory(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123:test", "DATA_DIR": "custom-data"}, clear=True):
            config = Config.from_env()
        self.assertTrue(config.data_dir.is_absolute())
        self.assertEqual(config.data_dir.name, "custom-data")
        self.assertEqual(config.poll_interval, 60)
        self.assertIsNone(config.chat_id)

    def test_target_group_configuration(self):
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "123:test", "TELEGRAM_CHAT_ID": "-1001",
        }, clear=True):
            self.assertEqual(Config.from_env().chat_id, -1001)

    def test_invalid_target_group_is_rejected(self):
        for value in ("0", "123", "not-a-number"):
            with self.subTest(value=value), patch.dict(os.environ, {
                "TELEGRAM_BOT_TOKEN": "123:test", "TELEGRAM_CHAT_ID": value,
            }, clear=True):
                with self.assertRaisesRegex(ValueError, "TELEGRAM_CHAT_ID"):
                    Config.from_env()
