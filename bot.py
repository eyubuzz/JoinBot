"""
Telegram Verification Bot
- Silently deletes messages from users not in required channels
- Reminds them to join and re-checks on every message
- Covers users who join then later leave the channel
"""

import logging
import time

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    filters,
)

import config

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Seconds between reminder messages per user (avoid flooding the chat)
REMINDER_COOLDOWN = 30


def _build_join_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"📢 Join @{ch}", url=f"https://t.me/{ch}")]
        for ch in config.REQUIRED_CHANNELS
    ]
    return InlineKeyboardMarkup(rows)


async def _is_member(user_id: int, channel: str, bot) -> bool:
    """Returns True if the user is a member of the channel."""
    try:
        member = await bot.get_chat_member(chat_id=f"@{channel}", user_id=user_id)
        return member.status not in ("left", "kicked")
    except Forbidden:
        logger.error(
            "Bot is not an admin in @%s — add the bot as admin to that channel!", channel
        )
        return True  # Fail open so we don't block everyone
    except BadRequest as e:
        logger.error("BadRequest checking @%s membership: %s", channel, e)
        return True  # Fail open


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check every message — delete and remind if sender isn't in required channels."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user or user.is_bot:
        return

    # Check all required channels
    missing = []
    for channel in config.REQUIRED_CHANNELS:
        if not await _is_member(user.id, channel, context.bot):
            missing.append(channel)

    if not missing:
        return  # User is good — let the message through

    # Delete their message
    try:
        await message.delete()
    except (BadRequest, Forbidden):
        pass

    # Rate-limit reminders per user
    cooldowns: dict = context.bot_data.setdefault("cooldowns", {})
    now = time.monotonic()
    if now - cooldowns.get(user.id, 0.0) < REMINDER_COOLDOWN:
        return

    cooldowns[user.id] = now

    channel_links = "\n".join(
        f"  ➤ <a href='https://t.me/{ch}'>@{ch}</a>" for ch in missing
    )
    text = (
        f"🔒 <b>{user.first_name}</b>, you can't chat here yet!\n\n"
        f"Join the channel below to unlock the group:\n\n"
        f"{channel_links}"
    )

    try:
        reminder = await context.bot.send_message(
            chat_id=message.chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=_build_join_keyboard(),
            disable_web_page_preview=True,
        )
        # Auto-delete the reminder after 30 seconds to keep chat clean
        context.job_queue.run_once(
            lambda ctx: ctx.bot.delete_message(
                chat_id=message.chat_id, message_id=reminder.message_id
            ),
            when=30,
        )
    except (BadRequest, Forbidden) as e:
        logger.warning("Could not send reminder to %d: %s", user.id, e)


def main() -> None:
    app = Application.builder().token(config.BOT_TOKEN).build()

    app.add_handler(
        MessageHandler(
            filters.Chat(config.GROUP_CHAT_ID) & ~filters.COMMAND,
            on_message,
        )
    )

    logger.info("Bot started.")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
