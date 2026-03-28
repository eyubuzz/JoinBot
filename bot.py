"""
Telegram Verification Bot
- Intercepts messages from unverified members and reminds them to verify
- Requires joining specific channels before being allowed to chat
"""

import logging
import time

from telegram import (
    Chat,
    ChatPermissions,
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

# Full permissions granted after verification
FULL_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_change_info=False,
    can_invite_users=True,
    can_pin_messages=False,
)

# Seconds between reminder messages per user (avoid spamming)
REMINDER_COOLDOWN = 30


def _build_verification_keyboard() -> InlineKeyboardMarkup:
    channel_buttons = [
        InlineKeyboardButton(
            text=f"📢 Join @{ch}",
            url=f"https://t.me/{ch}",
        )
        for ch in config.REQUIRED_CHANNELS
    ]
    rows = [[btn] for btn in channel_buttons]
    rows.append([InlineKeyboardButton("Verify Me ✅", callback_data="verify")])
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

    username_part = f"@{user.username}" if user.username else user.first_name
    welcome_text = config.WELCOME_MESSAGE.format(
        first_name=user.first_name,
        username=username_part,
        group_name=chat.title or "the group",
    )

    channel_list = "\n".join(f"  • @{ch}" for ch in config.REQUIRED_CHANNELS)
    welcome_text += f"\n\n📢 <b>Required channels:</b>\n{channel_list}"

    welcome_msg = await context.bot.send_message(
        chat_id=chat.id,
        text=welcome_text,
        parse_mode=ParseMode.HTML,
        reply_markup=_build_verification_keyboard(),
    )

    if "pending" not in context.bot_data:
        context.bot_data["pending"] = {}
    context.bot_data["pending"][user.id] = {
        "chat_id": chat.id,
        "welcome_msg_id": welcome_msg.message_id,
        "first_name": user.first_name,
        "last_reminded": 0.0,
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

    # Delete their message
    try:
        await message.delete()
    except (BadRequest, Forbidden):
        pass

    # Rate-limit reminders so we don't flood the chat
    now = time.monotonic()
    if now - info.get("last_reminded", 0.0) < REMINDER_COOLDOWN:
        return

    info["last_reminded"] = now

    channel_list = "\n".join(f"  • @{ch}" for ch in config.REQUIRED_CHANNELS)
    reminder_text = (
        f"⚠️ <b>{user.first_name}</b>, you need to verify before you can chat!\n\n"
        f"📢 <b>Join these channels first:</b>\n{channel_list}\n\n"
        f"Then click <b>Verify Me ✅</b> below."
    )

    try:
        await context.bot.send_message(
            chat_id=info["chat_id"],
            text=reminder_text,
            parse_mode=ParseMode.HTML,
            reply_markup=_build_verification_keyboard(),
        )
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
        missing = ", ".join(f"@{ch}" for ch in not_joined)
        await query.answer(
            f"❌ Please join first: {missing}",
            show_alert=True,
        )
        return

    # Grant full permissions
    try:
        await context.bot.restrict_chat_member(
            chat_id=info["chat_id"],
            user_id=user.id,
            permissions=FULL_PERMISSIONS,
        )
    except (BadRequest, Forbidden) as e:
        logger.warning("Could not set permissions for %d: %s", user.id, e)
        await query.answer("Something went wrong. Please contact an admin.", show_alert=True)
        return

    pending.pop(user.id, None)

    # Delete the verification message
    try:
        await context.bot.delete_message(
            chat_id=info["chat_id"],
            message_id=info["welcome_msg_id"],
        )
    except BadRequest:
        pass

    verified_text = config.VERIFIED_MESSAGE.format(
        first_name=user.first_name,
        username=f"@{user.username}" if user.username else user.first_name,
    )
    await context.bot.send_message(
        chat_id=info["chat_id"],
        text=verified_text,
        parse_mode=ParseMode.HTML,
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
