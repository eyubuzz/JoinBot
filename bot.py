"""
Telegram Verification Bot
- Silently deletes messages from users not in required channels
- Reminds them to join with a Verify button
- Verify button checks membership and deletes the reminder on success
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
    CallbackQueryHandler,
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

REMINDER_COOLDOWN = 30


def _build_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"📢 Join @{ch}", url=f"https://t.me/{ch}")]
        for ch in config.REQUIRED_CHANNELS
    ]
    rows.append([InlineKeyboardButton("✅ I Joined — Verify Me", callback_data="verify")])
    return InlineKeyboardMarkup(rows)


async def _is_member(user_id: int, channel: str, bot) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=f"@{channel}", user_id=user_id)
        return member.status not in ("left", "kicked")
    except Forbidden:
        logger.error("Bot is not admin in @%s — add it as admin!", channel)
        return True
    except BadRequest as e:
        logger.error("BadRequest checking @%s: %s", channel, e)
        return True


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete messages from non-members and send a reminder with verify button."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user or user.is_bot:
        return

    # Skip messages posted by the channel itself (comment section posts)
    if message.sender_chat:
        return

    # Skip group admins and the channel owner
    try:
        member = await context.bot.get_chat_member(
            chat_id=message.chat_id, user_id=user.id
        )
        if member.status in ("administrator", "creator"):
            return
    except (BadRequest, Forbidden):
        pass

    missing = [
        ch for ch in config.REQUIRED_CHANNELS
        if not await _is_member(user.id, ch, context.bot)
    ]

    if not missing:
        return

    try:
        await message.delete()
    except (BadRequest, Forbidden):
        pass

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
        f"Join the channel below then tap <b>✅ I Joined — Verify Me</b>:\n\n"
        f"{channel_links}"
    )

    try:
        reminder = await context.bot.send_message(
            chat_id=message.chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=_build_keyboard(),
            disable_web_page_preview=True,
        )
        # Track the latest reminder message per user so verify can delete it
        context.bot_data.setdefault("reminders", {})[user.id] = {
            "chat_id": message.chat_id,
            "message_id": reminder.message_id,
        }
    except (BadRequest, Forbidden) as e:
        logger.warning("Could not send reminder to %d: %s", user.id, e)


async def on_verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Verify Me button — check membership and delete reminder on success."""
    query = update.callback_query
    user = query.from_user

    missing = [
        ch for ch in config.REQUIRED_CHANNELS
        if not await _is_member(user.id, ch, context.bot)
    ]

    if missing:
        names = ", ".join(f"@{ch}" for ch in missing)
        await query.answer(
            f"❌ You haven't joined: {names}\n\nJoin first then try again.",
            show_alert=True,
        )
        return

    # Verified — delete the reminder message
    await query.answer("✅ Verified! You can now chat.", show_alert=True)

    reminders: dict = context.bot_data.get("reminders", {})
    info = reminders.pop(user.id, None)
    if info:
        try:
            await context.bot.delete_message(
                chat_id=info["chat_id"],
                message_id=info["message_id"],
            )
        except (BadRequest, Forbidden):
            pass

    # Reset cooldown so they won't get a reminder if they accidentally trigger
    context.bot_data.get("cooldowns", {}).pop(user.id, None)

    logger.info("User %s (%d) verified.", user.full_name, user.id)


def main() -> None:
    app = Application.builder().token(config.BOT_TOKEN).build()

    app.add_handler(
        MessageHandler(
            filters.Chat(config.GROUP_CHAT_ID) & ~filters.COMMAND,
            on_message,
        )
    )
    app.add_handler(CallbackQueryHandler(on_verify, pattern="^verify$"))

    logger.info("Bot started.")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
