"""
Telegram Verification Bot
- Intercepts messages from unverified members and reminds them to verify
- Requires joining specific channels before being allowed to chat
"""

import asyncio
import logging
import time

from telegram import (
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
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

# Seconds between reminder messages per user (avoid spamming the chat)
REMINDER_COOLDOWN = 30

# Seconds before the verified message auto-deletes
VERIFIED_DELETE_DELAY = 10


def _build_verification_keyboard() -> InlineKeyboardMarkup:
    channel_buttons = [
        InlineKeyboardButton(
            text=f"📢 Join @{ch}",
            url=f"https://t.me/{ch}",
        )
        for ch in config.REQUIRED_CHANNELS
    ]
    rows = [[btn] for btn in channel_buttons]
    rows.append([InlineKeyboardButton("✅  Verify Me", callback_data="verify")])
    return InlineKeyboardMarkup(rows)


async def _check_channel_memberships(user_id: int, bot) -> list[str]:
    """Returns list of channels the user has NOT joined yet."""
    not_joined = []
    for channel in config.REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=f"@{channel}", user_id=user_id)
            if member.status in ("left", "kicked"):
                not_joined.append(channel)
        except (BadRequest, Forbidden):
            not_joined.append(channel)
    return not_joined


async def _delete_after(bot, chat_id: int, message_id: int, delay: int) -> None:
    """Deletes a message after a delay (seconds)."""
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except (BadRequest, Forbidden):
        pass


async def on_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Triggered when a member joins the group."""
    result = update.chat_member
    if not result:
        return

    if result.chat.id != config.GROUP_CHAT_ID:
        return

    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status

    if new_status not in ("member", "restricted") or old_status in (
        "member",
        "administrator",
        "creator",
        "restricted",
    ):
        return

    user = result.new_chat_member.user
    if user.is_bot:
        return

    chat: Chat = result.chat
    logger.info("New member: %s (%d) in %s", user.full_name, user.id, chat.title)

    channel_list = "\n".join(
        f"  ➤ <a href='https://t.me/{ch}'>@{ch}</a>" for ch in config.REQUIRED_CHANNELS
    )

    welcome_text = (
        f"👋 <b>Welcome, {user.first_name}!</b>\n\n"
        f"To unlock chatting in <b>{chat.title or 'this group'}</b>, "
        f"you must first join our channel:\n\n"
        f"{channel_list}\n\n"
        f"Once joined, tap <b>✅ Verify Me</b> below.\n\n"
        f"<i>Your messages will be removed until you're verified.</i>"
    )

    welcome_msg = await context.bot.send_message(
        chat_id=chat.id,
        text=welcome_text,
        parse_mode=ParseMode.HTML,
        reply_markup=_build_verification_keyboard(),
        disable_web_page_preview=True,
    )

    if "pending" not in context.bot_data:
        context.bot_data["pending"] = {}

    context.bot_data["pending"][user.id] = {
        "chat_id": chat.id,
        "welcome_msg_id": welcome_msg.message_id,
        "first_name": user.first_name,
        "last_reminded": 0.0,
        "reminder_msg_ids": [],
    }


async def on_unverified_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Intercepts messages from unverified users, deletes them, and sends a reminder."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    pending: dict = context.bot_data.get("pending", {})
    if user.id not in pending:
        return

    info = pending[user.id]

    # Delete their message silently
    try:
        await message.delete()
    except (BadRequest, Forbidden):
        pass

    # Rate-limit reminders
    now = time.monotonic()
    if now - info.get("last_reminded", 0.0) < REMINDER_COOLDOWN:
        return

    info["last_reminded"] = now

    channel_list = "\n".join(
        f"  ➤ <a href='https://t.me/{ch}'>@{ch}</a>" for ch in config.REQUIRED_CHANNELS
    )
    reminder_text = (
        f"🔒 <b>{user.first_name}</b>, you're not verified yet!\n\n"
        f"Join the channel below then tap <b>✅ Verify Me</b>:\n\n"
        f"{channel_list}"
    )

    try:
        reminder_msg = await context.bot.send_message(
            chat_id=info["chat_id"],
            text=reminder_text,
            parse_mode=ParseMode.HTML,
            reply_markup=_build_verification_keyboard(),
            disable_web_page_preview=True,
        )
        # Track reminder so we can clean it up when user verifies
        info["reminder_msg_ids"].append(reminder_msg.message_id)
    except (BadRequest, Forbidden) as e:
        logger.warning("Could not send reminder to %d: %s", user.id, e)


async def on_verify_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the Verify Me button press."""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    pending: dict = context.bot_data.get("pending", {})

    if user.id not in pending:
        await query.answer("You're already verified! ✅", show_alert=True)
        return

    info = pending[user.id]

    not_joined = await _check_channel_memberships(user.id, context.bot)
    if not_joined:
        missing = "  ➤ @" + "\n  ➤ @".join(not_joined)
        await query.answer(
            f"❌ You haven't joined:\n\n{missing}\n\nJoin and try again.",
            show_alert=True,
        )
        return

    # Remove from pending — user is now verified and free to chat naturally
    pending.pop(user.id, None)

    # Delete the original welcome message
    try:
        await context.bot.delete_message(
            chat_id=info["chat_id"],
            message_id=info["welcome_msg_id"],
        )
    except (BadRequest, Forbidden):
        pass

    # Delete any reminder messages we sent
    for msg_id in info.get("reminder_msg_ids", []):
        try:
            await context.bot.delete_message(chat_id=info["chat_id"], message_id=msg_id)
        except (BadRequest, Forbidden):
            pass

    # Send verified message then auto-delete it
    verified_text = (
        f"🎉 <b>{user.first_name} is now verified!</b>\n\n"
        f"Welcome to the community — you can now chat freely! 🙌"
    )
    verified_msg = await context.bot.send_message(
        chat_id=info["chat_id"],
        text=verified_text,
        parse_mode=ParseMode.HTML,
    )

    # Schedule auto-delete of the verified message
    asyncio.create_task(
        _delete_after(context.bot, info["chat_id"], verified_msg.message_id, VERIFIED_DELETE_DELAY)
    )

    logger.info("User %s (%d) verified successfully.", user.full_name, user.id)


def main() -> None:
    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .build()
    )

    app.add_handler(
        ChatMemberHandler(on_new_member, ChatMemberHandler.CHAT_MEMBER)
    )
    app.add_handler(
        MessageHandler(
            filters.Chat(config.GROUP_CHAT_ID) & ~filters.COMMAND,
            on_unverified_message,
        )
    )
    app.add_handler(CallbackQueryHandler(on_verify_button, pattern="^verify$"))

    logger.info("Bot started. Listening for new members...")
    app.run_polling(allowed_updates=["chat_member", "message", "callback_query"])


if __name__ == "__main__":
    main()
