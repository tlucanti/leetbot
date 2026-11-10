"""Solve counts and chronological difficulty markers over stored completions."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .leetcode import DIFFICULTY_MARKERS
from .storage import TrackingStore
from .tracker import profile_link

MOSCOW = ZoneInfo("Europe/Moscow")


def statistics_messages(
    store: TrackingStore, days: int = 1, *, now: datetime | None = None
) -> list[str]:
    if type(days) is not int or days < 1:
        raise ValueError("N must be a positive integer")
    end = now if now is not None else datetime.now(timezone.utc)
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("The report time must include a timezone")
    end = end.astimezone(timezone.utc)
    try:
        start = end - timedelta(days=days)
    except OverflowError as exc:
        raise ValueError("N is outside the supported date range") from exc
    start_timestamp, end_timestamp = start.timestamp(), end.timestamp()
    users = [
        (
            username,
            sorted(
                (
                    task
                    for task in store.completed_problems(username).values()
                    if task.completed_at > 0 and start_timestamp <= task.completed_at <= end_timestamp
                ),
                key=lambda task: task.completed_at,
            ),
        )
        for username in store.users()
    ]
    users.sort(key=lambda item: len(item[1]), reverse=True)
    day_label = "day" if days == 1 else "days"
    header = f"📊 <b>LeetCode statistics · last {days} {day_label}</b>"
    if not users:
        return [header + "\n\nNobody is tracked yet. Use <code>/add username</code> to start.\n\nTotal: 0 problems"]
    total = sum(len(tasks) for _, tasks in users)
    footer = f"\n\nTotal: {total} problems"
    rows = []
    for username, tasks in users:
        if not tasks:
            rows.append(f"• {profile_link(username)} · 🤡")
            continue
        markers = [DIFFICULTY_MARKERS[task.difficulty] for task in tasks]
        rows.append(f"• {profile_link(username)} · {len(tasks)}")
        # Short lines allow even very long histories to fit across Telegram messages.
        rows.extend("  " + "".join(markers[offset : offset + 100]) for offset in range(0, len(markers), 100))
    messages = []
    current = header + "\n"
    for row in rows:
        # UTF-16 units include emoji correctly; counting HTML too leaves extra margin.
        if len((current + "\n" + row + footer).encode("utf-16-le")) // 2 > 3900:
            messages.append(current)
            current = header + "\n"
        current += "\n" + row
    messages.append(current + footer)
    return messages
