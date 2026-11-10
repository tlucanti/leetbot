import unittest

import httpx

from leetbot.leetcode import LeetCodeClient, LeetCodeError, UserNotFound, validate_username


class LeetCodeClientTests(unittest.IsolatedAsyncioTestCase):
    async def client(self, handler):
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = LeetCodeClient(http)
        self.addAsyncCleanup(client.close)
        return client

    async def test_public_activity_and_canonical_username(self):
        import json

        def handler(request):
            payload = json.loads(request.content)
            self.assertEqual(payload["variables"], {"username": "alice", "limit": 20})
            self.assertIn("recentAcSubmissionList", payload["query"])
            self.assertIn("timestamp", payload["query"])
            self.assertNotRegex(payload["query"], r"\bid\b")
            return httpx.Response(200, json={"data": {
                "matchedUser": {"username": "Alice"},
                "recentAcSubmissionList": [
                    {"titleSlug": "two-sum", "timestamp": "1200"},
                    {"titleSlug": "two-sum", "timestamp": "1000"},
                    {"titleSlug": "three-sum", "timestamp": 1100},
                ],
            }})

        client = await self.client(handler)
        activity = await client.activity("alice")
        self.assertEqual(activity.username, "Alice")
        self.assertEqual(activity.problem_times, {"two-sum": 1000, "three-sum": 1100})

    async def test_missing_user(self):
        client = await self.client(lambda _: httpx.Response(200, json={"data": {
            "matchedUser": None, "recentAcSubmissionList": [],
        }}))
        with self.assertRaises(UserNotFound):
            await client.activity("missing")

    async def test_empty_history_is_valid(self):
        client = await self.client(lambda _: httpx.Response(200, json={"data": {
            "matchedUser": {"username": "new_user"}, "recentAcSubmissionList": [],
        }}))
        self.assertEqual((await client.activity("new_user")).problem_times, {})

    async def test_invalid_problem_slug_is_rejected(self):
        client = await self.client(lambda _: httpx.Response(200, json={"data": {
            "matchedUser": {"username": "alice"},
            "recentAcSubmissionList": [{"titleSlug": "../invalid", "timestamp": "1000"}],
        }}))
        with self.assertRaises(LeetCodeError):
            await client.activity("alice")

    async def test_invalid_or_missing_completion_time_is_rejected(self):
        for timestamp in (None, True, 1.5, -1, "invalid", "-100"):
            with self.subTest(timestamp=timestamp):
                client = await self.client(lambda _, value=timestamp: httpx.Response(200, json={"data": {
                    "matchedUser": {"username": "alice"},
                    "recentAcSubmissionList": [{"titleSlug": "two-sum", "timestamp": value}],
                }}))
                with self.assertRaises(LeetCodeError):
                    await client.activity("alice")
        client = await self.client(lambda _: httpx.Response(200, json={"data": {
            "matchedUser": {"username": "alice"}, "recentAcSubmissionList": [{"titleSlug": "two-sum"}],
        }}))
        with self.assertRaises(LeetCodeError):
            await client.activity("alice")

    async def test_graphql_error_is_not_treated_as_empty_history(self):
        client = await self.client(lambda _: httpx.Response(200, json={"errors": [{"message": "unavailable"}]}))
        with self.assertRaises(LeetCodeError):
            await client.activity("alice")

    async def test_missing_history_is_an_error(self):
        client = await self.client(lambda _: httpx.Response(200, json={"data": {"matchedUser": {"username": "alice"}}}))
        with self.assertRaises(LeetCodeError):
            await client.activity("alice")

    async def test_http_errors_and_non_json_responses(self):
        for status, body in ((429, "Too many requests"), (403, "Blocked"), (200, "<html>Unavailable</html>")):
            with self.subTest(status=status):
                client = await self.client(lambda _, s=status, b=body: httpx.Response(s, text=b))
                with self.assertRaises(LeetCodeError):
                    await client.activity("alice")

    async def test_question_uses_display_number_and_caches_metadata(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"data": {"question": {
                "questionFrontendId": "1", "title": "Two Sum", "titleSlug": "two-sum", "difficulty": "Easy",
            }}})

        client = await self.client(handler)
        question = await client.question("two-sum")
        self.assertEqual(question.number, "1")
        self.assertEqual(question.difficulty, "Easy")
        self.assertEqual(await client.question("two-sum"), question)
        self.assertEqual(len(requests), 1)

    async def test_incomplete_metadata_is_an_error(self):
        client = await self.client(lambda _: httpx.Response(200, json={"data": {"question": None}}))
        with self.assertRaises(LeetCodeError):
            await client.question("two-sum")


class UsernameTests(unittest.TestCase):
    def test_username_validation(self):
        self.assertEqual(validate_username(" Alice_123-test "), "Alice_123-test")
        for name in ("", "hello world", "<b>alice</b>", "https://leetcode.com/u/alice/", "../alice", "x" * 51):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_username(name)
