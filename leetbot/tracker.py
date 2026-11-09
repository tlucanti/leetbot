"""Compare recent solved problems with saved IDs and record successful sends."""

import asyncio
from collections.abc import Awaitable, Callable
from html import escape
import logging

from telegram.error import TelegramError

from .leetcode import LeetCodeClient, LeetCodeError, Question, validate_username
from .storage import TrackingStore

logger = logging.getLogger(__name__)
SendNotification = Callable[[str], Awaitable[None]]


def profile_link(username: str) -> str:
    return f'<a href="https://leetcode.com/u/{escape(username, quote=True)}/">{escape(username)}</a>'


def format_notification(username: str, question: Question) -> str:
    badge = {"Easy": "🟢", "Medium": "🟡", "Hard": "🔴"}[question.difficulty]
    problem = f"#{escape(question.number)} · {escape(question.title)}"
    return (
        "🎉 <b>Problem solved!</b>\n\n"
        f"👤 {profile_link(username)}\n"
        f'🧩 <a href="https://leetcode.com/problems/{escape(question.slug, quote=True)}/">{problem}</a>\n'
        f"{badge} <b>{question.difficulty}</b>\n\n"
        "<i>Another step forward. Keep it up! 🚀</i>"
    )


class Tracker:
    def __init__(self, store: TrackingStore, leetcode: LeetCodeClient, send: SendNotification):
        self.store = store
        self.leetcode = leetcode
        self.send = send
        self._lock = asyncio.Lock()

    async def add(self, username: str) -> bool:
        username = validate_username(username)
        if self.store.contains(username):
            return False
        activity = await self.leetcode.activity(username)
        problem_ids = set()
        for slug in activity.problem_slugs:
            problem_ids.add((await self.leetcode.question(slug)).number)
        async with self._lock:
            return self.store.add(activity.username, problem_ids)

    async def remove(self, username: str) -> bool:
        async with self._lock:
            return self.store.remove(username)

    async def poll(self) -> None:
        for username in self.store.users():
            try:
                activity = await self.leetcode.activity(username)
            except LeetCodeError as exc:
                logger.warning("Cannot check %s: %s", username, exc)
                continue
            for slug in activity.problem_slugs:
                try:
                    question = await self.leetcode.question(slug)
                    async with self._lock:
                        if not self.store.contains(username):
                            break
                        if question.number in self.store.solved_ids(username):
                            continue
                        await self.send(format_notification(username, question))
                        self.store.record_solve(username, question.number)
                except (LeetCodeError, TelegramError, OSError) as exc:
                    logger.warning("Could not notify %s about %s: %s", username, slug, exc)
