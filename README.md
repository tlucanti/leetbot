# LeetBot

A Telegram bot that follows public LeetCode activity and posts new solves to your group. It runs locally with long polling; no server URL, webhook, database, or LeetCode login is needed.

Notifications include the LeetCode username and profile link, the linked problem number and title, and a colored difficulty badge:

> 🎉 **Problem solved!**
>
> 👤 [Alice](https://leetcode.com/u/Alice/)
>
> 🧩 [#1 · Two Sum](https://leetcode.com/problems/two-sum/)
>
> 🟢 **Easy**
>
> *Another step forward. Keep it up! 🚀*

## Setup

Requires Python 3.10 or newer.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

1. Create a Telegram bot with [@BotFather](https://t.me/BotFather) using `/newbot`.
2. Edit `.env` and set `TELEGRAM_BOT_TOKEN` to the token BotFather gives you.
3. Add your bot to the Telegram group and allow it to send messages.
4. Start it from this folder:

   ```sh
   python bot.py
   ```

5. In the group, send `/start@YourBotUsername` to see the group ID. Set `TELEGRAM_CHAT_ID` in `.env` to that negative number, then restart the bot.
6. Send `/add@YourBotUsername leetcode_username` in the configured group. Subsequent notifications go to that group.

Keep the process running to receive notifications. Stop with Ctrl+C. Run one instance per token and data directory.

The bot uses the [Telegram Bot API](https://core.telegram.org/bots/api) through [python-telegram-bot](https://docs.python-telegram-bot.org/en/stable/). Commands addressed to the bot work with Telegram's default group privacy setting, so you do not need to disable privacy mode. If a bare `/add` or `/list` does not reach the bot, use `/add@YourBotUsername` or `/list@YourBotUsername`.

## Commands

| Command | Action |
| --- | --- |
| `/add username` | Track a LeetCode user in this group. |
| `/remove username` | Remove that user and their solved IDs from the JSON file. |
| `/list` | Show this group's tracked users. |
| `/help` or `/start` | Show help. |

Use LeetCode usernames, not Telegram usernames or profile URLs. Usernames are matched without regard to case. The bot serves one configured group, and any member of that group can manage the list. Commands from other groups cannot change it. Private chat provides help. When `TELEGRAM_CHAT_ID` is blank, the bot provides help and group IDs, but tracking is disabled until the group is configured.

## Configuration and local files

Edit `.env`, or provide environment variables directly. Existing environment variables take precedence over `.env`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Required | Your Telegram bot token. |
| `TELEGRAM_CHAT_ID` | Blank | Negative ID of the group receiving notifications; required to enable tracking. |
| `POLL_INTERVAL_SECONDS` | `60` | Seconds between checks; minimum `10`. |
| `DATA_DIR` | `data` | Folder for local tracking files; relative paths start at this project folder. |

`data/state.json` is the only tracking file. It maps each tracked username to the unique problem IDs already known for that user:

```json
{
  "Alice": ["1", "15", "42"],
  "Bob": []
}
```

IDs are the displayed LeetCode problem numbers, stored as strings because some problems have identifiers such as `LCR 001`. An empty list still means the user is tracked. No submission IDs, timestamps, or notification queues are stored. The group ID lives in `.env`, outside the tracking state. Files are saved through atomic replacement. `.env` and runtime data are excluded from Git.

Use the bot's commands to add or remove users. Keep `state.json` when moving the bot or making a backup, and stop the process before editing it manually. If the state file is corrupt, the bot stops with an error instead of overwriting it; restore a backup. Deleting `state.json` clears the tracking list, so users need to be added again. Removing and re-adding a user starts a fresh baseline.

## Detection and delivery

- `/add` validates the public profile, fetches the latest **20 accepted submissions**, extracts unique solved problems, and saves their IDs without sending notifications.
- Every poll fetches the latest 20 accepted submissions from `https://leetcode.com/graphql/` and extracts unique problems. Problem details, including the displayed ID and difficulty, are fetched separately and cached in memory.
- Problems whose IDs are already saved are skipped. Each new problem is sent to Telegram, then its ID is immediately added to the JSON file after a successful send. The saved list accumulates solves; it is never replaced by just the latest 20 submissions.
- Failed sends, metadata requests, or state writes leave the problem unsaved, allowing another attempt on the next poll while it remains in the recent feed. Successful notifications are saved individually, so one failed message does not undo other successes. There is no queue. Telegram sends use rate limiting.
- The initial baseline covers recent solves, not the user's entire history. A new accepted submission to an older problem outside that baseline can still generate a notification.
- Solves can be missed if more than 20 accepted submissions arrive between successful checks, including during downtime. A failed notification is no longer retried if its problem disappears from the latest 20 submissions. Private or hidden activity cannot be tracked. LeetCode's website GraphQL endpoint can change or rate-limit requests.
- A crash after Telegram accepts a message but before its problem ID is saved can cause that message to be resent on the next poll.

## Tests

```sh
source .venv/bin/activate
python -m unittest discover -s tests -v
```

The tests use mock HTTP responses and a fake Telegram API, so they need no token or network access. They cover command routing and notification formatting, baseline handling, duplicate suppression, restarts, group configuration, partial send failures, retrying unsaved problems from the recent feed, user removal, and atomic file writes.
