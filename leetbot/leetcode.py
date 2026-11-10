"""Small client for the GraphQL endpoint used by leetcode.com."""

from dataclasses import dataclass
import re
from typing import Any

import httpx

RECENT_LIMIT = 20
DIFFICULTY_MARKERS = {"Easy": "🟢", "Medium": "🟡", "Hard": "🔴"}
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,50}\Z")
SLUG_PATTERN = re.compile(r"[a-zA-Z0-9-]+\Z")

ACTIVITY_QUERY = """
query RecentAccepted($username: String!, $limit: Int!) {
  matchedUser(username: $username) { username }
  recentAcSubmissionList(username: $username, limit: $limit) {
    titleSlug
    timestamp
  }
}
"""

QUESTION_QUERY = """
query QuestionDetails($titleSlug: String!) {
  question(titleSlug: $titleSlug) {
    questionFrontendId
    title
    titleSlug
    difficulty
  }
}
"""


class LeetCodeError(Exception):
    """LeetCode is unavailable or returned an unusable response."""


class UserNotFound(LeetCodeError):
    """No public LeetCode profile exists for the requested username."""


def validate_username(username: str) -> str:
    username = username.strip()
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError("Use a LeetCode username with letters, numbers, _ or - (up to 50 characters).")
    return username


@dataclass(frozen=True)
class Activity:
    username: str
    problem_times: dict[str, int]


@dataclass(frozen=True)
class Question:
    number: str
    title: str
    slug: str
    difficulty: str


class LeetCodeClient:
    def __init__(self, http: httpx.AsyncClient | None = None):
        self._http = http or httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={
                "User-Agent": "LeetBot/1.0",
                "Referer": "https://leetcode.com/",
                "Accept": "application/json",
            },
        )
        self._questions: dict[str, Question] = {}

    async def close(self) -> None:
        await self._http.aclose()

    async def _query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._http.post(
                "https://leetcode.com/graphql/", json={"query": query, "variables": variables}
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LeetCodeError(f"LeetCode request failed: {type(exc).__name__}") from exc
        if not isinstance(payload, dict) or payload.get("errors") or not isinstance(payload.get("data"), dict):
            raise LeetCodeError("LeetCode returned GraphQL errors or missing data")
        return payload["data"]

    async def activity(self, username: str) -> Activity:
        data = await self._query(
            ACTIVITY_QUERY, {"username": validate_username(username), "limit": RECENT_LIMIT}
        )
        if "matchedUser" not in data:
            raise LeetCodeError("LeetCode response is missing the user profile")
        if data["matchedUser"] is None:
            raise UserNotFound(username)
        try:
            canonical_name = validate_username(data["matchedUser"]["username"])
            items = data["recentAcSubmissionList"]
            if not isinstance(items, list):
                raise ValueError("Missing recent accepted submissions")
            problem_times: dict[str, int] = {}
            for item in items:
                slug, raw_timestamp = item["titleSlug"], item["timestamp"]
                if not isinstance(slug, str) or not SLUG_PATTERN.fullmatch(slug):
                    raise ValueError("Invalid problem slug")
                if type(raw_timestamp) not in (str, int):
                    raise ValueError("Invalid completion time")
                timestamp = int(raw_timestamp)
                if timestamp < 0:
                    raise ValueError("Invalid completion time")
                # Keep the earliest accepted time visible in the recent feed for each problem.
                problem_times[slug] = min(problem_times.get(slug, timestamp), timestamp)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise LeetCodeError("LeetCode returned malformed user activity") from exc
        return Activity(canonical_name, problem_times)

    async def question(self, slug: str) -> Question:
        if slug in self._questions:
            return self._questions[slug]
        data = await self._query(QUESTION_QUERY, {"titleSlug": slug})
        try:
            item = data["question"]
            number = item["questionFrontendId"]
            title = item["title"]
            difficulty = item["difficulty"]
            if (
                not isinstance(number, str)
                or not number
                or not isinstance(title, str)
                or not title
                or item["titleSlug"] != slug
                or difficulty not in ("Easy", "Medium", "Hard")
            ):
                raise ValueError("Invalid problem details")
            question = Question(number, title, slug, difficulty)
        except (KeyError, TypeError, ValueError) as exc:
            raise LeetCodeError(f"LeetCode returned incomplete problem details for {slug}") from exc
        self._questions[slug] = question
        return question
