"""Statistics over stored completion times, displayed in Moscow time."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
    counts = [
        (
            username,
            sum(start_timestamp <= completed_at <= end_timestamp for completed_at in store.completion_times(username).values()),
        )
        for username in store.users()
    ]
    counts.sort(key=lambda item: (-item[1], item[0].casefold()))
    day_label = "day" if days == 1 else "days"
    header = (
        f"📊 <b>LeetCode statistics · last {days} {day_label}</b>\n"
        f"🕒 {start.astimezone(MOSCOW):%d.%m.%Y %H:%M} → {end.astimezone(MOSCOW):%d.%m.%Y %H:%M} MSK\n"
    )
    if not counts:
        return [header + "\nNobody is tracked yet. Use <code>/add username</code> to start."]
    total = sum(count for _, count in counts)
    footer = f"\n<b>Total: {total}</b> problems · <b>{len(counts)}</b> tracked users"
    messages = []
    for offset in range(0, len(counts), 20):
        rows = [f"• {profile_link(username)} — <b>{count}</b> solved" for username, count in counts[offset : offset + 20]]
        messages.append(header + "\n" + "\n".join(rows) + "\n" + footer)
    return messages
