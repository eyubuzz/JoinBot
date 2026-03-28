"""
Telegram Verification Bot
- Mutes new members until they verify
- Requires joining specific channels
- Auto-kicks if not verified within timeout
"""

import asyncio
import logging
from datetime import datetime, timedelta

from telegram import (
    Chat,
    ChatMemberRestricted,
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
)

import config

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# No permissions = muted (can't send any messages)
MUTED_PERMISSIONS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False,
)

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
            # Can't check — assume not joined (bot must be in the channel)
            not_joined.append(channel)
    return not_joined


async def _auto_kick(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
    welcome_msg_id: int,
    first_name: str,
) -> None:
    """Called after timeout — kicks user if still unverified."""
    pending: dict = context.bot_data.get("pending", {})
    if user_id not in pending:
        return  # Already verified

    logger.info("Auto-kicking unverified user %s (%d)", first_name, user_id)
    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        # Immediately unban so they can rejoin later
        await context.bot.unban_chat_member(
            chat_id=chat_id, user_id=user_id, only_if_banned=True
        )
        await context.bot.delete_message(
            chat_id=chat_id, message_id=welcome_msg_id
        )
        kick_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏰ <b>{first_name}</b> was removed for not completing verification in time.",
            parse_mode=ParseMode.HTML,
        )
        await asyncio.sleep(10)
        await context.bot.delete_message(
            chat_id=chat_id, message_id=kick_msg.message_id
        )
    except (BadRequest, Forbidden) as e:
        logger.warning("Failed to kick %d: %s", user_id, e)
    finally:
        pending.pop(user_id, None)


async def on_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Triggered when a member joins the group."""
    result = update.chat_member
    if not result:
        return

    # Only handle joins in our target group
    if result.chat.id != config.GROUP_CHAT_ID:
        return

    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status

    # Detect a user joining (was not a member, now is)
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

    # Mute the new member
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=user.id,
            permissions=MUTED_PERMISSIONS,
        )
    except (BadRequest, Forbidden) as e:
        logger.warning("Could not mute %d: %s", user.id, e)
        return

    # Format welcome message
    username_part = f"@{user.username}" if user.username else user.first_name
    welcome_text = config.WELCOME_MESSAGE.format(
        first_name=user.first_name,
        username=username_part,
        group_name=chat.title or "the group",
        timeout=config.VERIFICATION_TIMEOUT,
    )

    # Append required channels list
    channel_list = "\n".join(f"  • @{ch}" for ch in config.REQUIRED_CHANNELS)
    welcome_text += f"\n\n📢 <b>Required channels:</b>\n{channel_list}"

    welcome_msg = await context.bot.send_message(
        chat_id=chat.id,
        text=welcome_text,
        parse_mode=ParseMode.HTML,
        reply_markup=_build_verification_keyboard(),
    )

    # Track pending verification
    if "pending" not in context.bot_data:
        context.bot_data["pending"] = {}
    context.bot_data["pending"][user.id] = {
        "chat_id": chat.id,
        "welcome_msg_id": welcome_msg.message_id,
        "first_name": user.first_name,
    }

    # Schedule auto-kick if timeout is set
    if config.VERIFICATION_TIMEOUT > 0:
        context.job_queue.run_once(
            callback=lambda ctx: _auto_kick(
                ctx,
                chat.id,
                user.id,
                welcome_msg.message_id,
                user.first_name,
            ),
            when=timedelta(minutes=config.VERIFICATION_TIMEOUT),
            name=f"kick_{user.id}",
        )


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

    # Check channel memberships
    not_joined = await _check_channel_memberships(user.id, context.bot)
    if not_joined:
        missing = ", ".join(f"@{ch}" for ch in not_joined)
        await query.answer(
            f"❌ Please join first: {missing}",
            show_alert=True,
        )
        return

    # Verification passed — grant full permissions
    try:
        await context.bot.restrict_chat_member(
            chat_id=info["chat_id"],
            user_id=user.id,
            permissions=FULL_PERMISSIONS,
        )
    except (BadRequest, Forbidden) as e:
        logger.warning("Could not unmute %d: %s", user.id, e)
        await query.answer("Something went wrong. Please contact an admin.", show_alert=True)
        return

    # Remove from pending (prevents auto-kick)
    pending.pop(user.id, None)

    # Cancel any scheduled kick job
    current_jobs = context.job_queue.get_jobs_by_name(f"kick_{user.id}")
    for job in current_jobs:
        job.schedule_removal()

    # Delete the verification message
    try:
        await context.bot.delete_message(
            chat_id=info["chat_id"],
            message_id=info["welcome_msg_id"],
        )
    except BadRequest:
        pass

    # Send public welcome
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
    app.add_handler(CallbackQueryHandler(on_verify_button, pattern="^verify$"))

    logger.info("Bot started. Listening for new members...")
    app.run_polling(allowed_updates=["chat_member", "callback_query"])


if __name__ == "__main__":
    main()
