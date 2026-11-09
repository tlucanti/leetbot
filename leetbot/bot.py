"""Telegram commands, configuration, and the application lifecycle."""

from dataclasses import dataclass
from html import escape
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import LinkPreviewOptions, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    AIORateLimiter,
    Application,
    CommandHandler,
    ContextTypes,
    Defaults,
)

from .leetcode import LeetCodeClient, LeetCodeError, UserNotFound, validate_username
from .storage import TrackingStore
from .tracker import Tracker, profile_link

PROJECT_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)

HELP_TEXT = """🧩 <b>LeetCode activity tracker</b>

Track public LeetCode profiles and celebrate new solves in the configured group.

➕ /add &lt;username&gt; — track a user here
➖ /remove &lt;username&gt; — stop tracking a user here
📋 /list — show this group's tracked users
❔ /help — show these commands

Example: <code>/add leetcode</code>

Existing recent solves are skipped when tracking starts. Repeat submissions to a problem already observed in this group are skipped too.

Use these commands in your group. If Telegram does not forward a command, address it to the bot: <code>/add@YourBot username</code>."""


@dataclass(frozen=True)
class Config:
    token: str
    poll_interval: int
    data_dir: Path
    chat_id: int | None = None

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv(PROJECT_ROOT / ".env")
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token or token == "replace_with_your_bot_token":
            raise ValueError("Set TELEGRAM_BOT_TOKEN in .env (see .env.example).")
        try:
            interval = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
        except ValueError as exc:
            raise ValueError("POLL_INTERVAL_SECONDS must be an integer of at least 10.") from exc
        if interval < 10:
            raise ValueError("POLL_INTERVAL_SECONDS must be at least 10.")
        chat_id_text = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        try:
            chat_id = int(chat_id_text) if chat_id_text else None
        except ValueError as exc:
            raise ValueError("TELEGRAM_CHAT_ID must be a negative Telegram group ID.") from exc
        if chat_id is not None and chat_id >= 0:
            raise ValueError("TELEGRAM_CHAT_ID must be a negative Telegram group ID.")
        data_dir = Path(os.getenv("DATA_DIR", "data")).expanduser()
        if not data_dir.is_absolute():
            data_dir = PROJECT_ROOT / data_dir
        return cls(token, interval, data_dir, chat_id)


def get_tracker(context: ContextTypes.DEFAULT_TYPE) -> Tracker:
    return context.application.bot_data["tracker"]


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        text = HELP_TEXT
        if update.effective_chat and update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            text += f"\n\nGroup ID: <code>{update.effective_chat.id}</code>\nSet TELEGRAM_CHAT_ID in .env to use this group."
        await update.effective_message.reply_text(text)


async def require_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.effective_chat and update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        chat_id = context.application.bot_data["chat_id"]
        if chat_id is None:
            await update.effective_message.reply_text(
                f"Set <code>TELEGRAM_CHAT_ID={update.effective_chat.id}</code> in .env and restart the bot."
            )
            return False
        if update.effective_chat.id != chat_id:
            await update.effective_message.reply_text("This bot is configured for a different group.")
            return False
        return True
    if update.effective_message:
        await update.effective_message.reply_text("Add me to a Telegram group and use this command there.")
    return False


async def command_username(update: Update, context: ContextTypes.DEFAULT_TYPE, command: str) -> str | None:
    if not await require_group(update, context):
        return None
    if len(context.args) != 1:
        await update.effective_message.reply_text(f"Usage: <code>/{command} &lt;LeetCode username&gt;</code>")
        return None
    try:
        return validate_username(context.args[0])
    except ValueError as exc:
        await update.effective_message.reply_text(escape(str(exc)))
        return None


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    username = await command_username(update, context, "add")
    if username is None:
        return
    try:
        added = await get_tracker(context).add(username)
    except UserNotFound:
        await update.effective_message.reply_text(f"No public LeetCode profile found for <b>{escape(username)}</b>.")
        return
    except LeetCodeError as exc:
        logger.warning("Cannot add %s: %s", username, exc)
        await update.effective_message.reply_text("LeetCode is unavailable right now. Please try /add again shortly.")
        return
    if added:
        text = f"✅ Now tracking {profile_link(username)} in this group.\nI'll post new solves here. Existing solves were skipped."
    else:
        text = f"ℹ️ {profile_link(username)} is already tracked in this group."
    await update.effective_message.reply_text(text)


async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    username = await command_username(update, context, "remove")
    if username is None:
        return
    removed = await get_tracker(context).remove(username)
    if removed:
        text = f"🛑 Stopped tracking <b>{escape(username)}</b> in this group."
    else:
        text = f"ℹ️ <b>{escape(username)}</b> is not tracked in this group."
    await update.effective_message.reply_text(text)


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update, context):
        return
    users = get_tracker(context).store.users()
    if not users:
        await update.effective_message.reply_text("📋 Nobody is tracked here yet. Use <code>/add username</code> to start.")
        return
    # Small chunks keep HTML links intact and messages below Telegram's size limit.
    for offset in range(0, len(users), 20):
        lines = [f"📋 <b>Tracked users ({len(users)})</b>\n"]
        lines.extend(f"• {profile_link(username)}" for username in users[offset : offset + 20])
        await update.effective_message.reply_text("\n".join(lines))


async def poll_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await get_tracker(context).poll()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # HTTP client INFO logging is disabled in main to keep the bot token out of URLs in logs.
    error = context.error
    logger.error("Bot operation failed: %s", type(error).__name__)
    if isinstance(update, Update) and update.effective_message:
        message = (
            "I couldn't save the tracking data. Check the bot's data folder permissions and free disk space."
            if isinstance(error, OSError)
            else "Something went wrong. Please try the command again shortly."
        )
        try:
            await update.effective_message.reply_text(message)
        except TelegramError:
            logger.warning("Could not deliver the error message")


def build_application(config: Config) -> Application:
    store = TrackingStore(config.data_dir)
    leetcode = LeetCodeClient()

    async def post_init(application: Application) -> None:
        await application.bot.set_my_commands(
            [("add", "Track a LeetCode user"), ("remove", "Stop tracking a user"), ("list", "List tracked users"), ("help", "Show help")]
        )

    async def post_shutdown(application: Application) -> None:
        await leetcode.close()

    application = (
        Application.builder()
        .token(config.token)
        .defaults(Defaults(parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True)))
        .rate_limiter(AIORateLimiter(max_retries=2))
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    async def send(text: str) -> None:
        await application.bot.send_message(chat_id=config.chat_id, text=text)

    application.bot_data["chat_id"] = config.chat_id
    application.bot_data["tracker"] = Tracker(store, leetcode, send)
    application.add_handler(CommandHandler(["start", "help"], help_command))
    application.add_handler(CommandHandler("add", add_command))
    application.add_handler(CommandHandler("remove", remove_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_error_handler(error_handler)
    if application.job_queue is None:
        raise RuntimeError('Install the job queue extra: pip install -r requirements.txt')
    if config.chat_id is not None:
        application.job_queue.run_repeating(
            poll_job,
            interval=config.poll_interval,
            first=1,
            name="leetcode-poll",
            job_kwargs={"max_instances": 1, "coalesce": True},
        )
    return application


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        config = Config.from_env()
        application = build_application(config)
    except (ValueError, OSError) as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc
    if config.chat_id is None:
        logger.info("Set TELEGRAM_CHAT_ID to enable tracking; /start in your group shows its ID")
    else:
        logger.info("Starting LeetCode tracker; polling every %s seconds", config.poll_interval)
    application.run_polling(allowed_updates=["message"], drop_pending_updates=False)
