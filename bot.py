import logging
import os
import time
from collections import defaultdict
from typing import Dict, Iterable, List, Optional

import firebase_admin
from firebase_admin import credentials, db
from telegram import ChatPermissions, Update
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
LOGGER = logging.getLogger(__name__)

DEFAULT_WELCOME = "👋 Welcome, {first}!"
DEFAULT_GOODBYE = "👋 Goodbye, {first}!"


def initialize_firebase() -> None:
    """Initialise the Firebase connection based on environment variables."""

    firebase_cred_path = os.getenv("FIREBASE_CRED")
    firebase_db_url = os.getenv("FIREBASE_DB_URL")

    if not firebase_cred_path or not firebase_db_url:
        raise RuntimeError("FIREBASE_CRED and FIREBASE_DB_URL must be set")

    if not os.path.isfile(firebase_cred_path):
        raise FileNotFoundError(
            f"Firebase credential file not found at {firebase_cred_path}"
        )

    try:
        firebase_admin.get_app()
    except ValueError:
        cred = credentials.Certificate(firebase_cred_path)
        firebase_admin.initialize_app(cred, {"databaseURL": firebase_db_url})


initialize_firebase()

# References
ADMINS_REF = db.reference("admins")
GROUPS_REF = db.reference("groups")
USERS_REF = db.reference("users")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")

# In-memory flood tracking
user_message_times = defaultdict(list)


# Helper functions
def is_admin(user_id: int) -> bool:
    return ADMINS_REF.child(str(user_id)).get() is True

def group_ref(chat_id: int):
    return GROUPS_REF.child(str(chat_id))

def user_ref(user_id: int):
    return USERS_REF.child(str(user_id))

def is_banned(chat_id: int, user_id: int) -> bool:
    return group_ref(chat_id).child("blacklist").child(str(user_id)).get() is True

def format_name_vars(text: str, user) -> str:
    return (
        text.replace("{first}", user.first_name or "")
        .replace("{last}", user.last_name or "")
        .replace("{username}", f"@{user.username}" if user.username else "")
    )


def history_to_list(history_data: Optional[Iterable]) -> List[str]:
    if not history_data:
        return []
    if isinstance(history_data, list):
        return [str(item) for item in history_data if item]
    if isinstance(history_data, dict):
        return [
            str(value)
            for key, value in sorted(history_data.items())
            if value is not None
        ]
    return []


def get_name_history(user_id: int) -> List[str]:
    return history_to_list(user_ref(user_id).child("history").get())


def get_group_settings(chat_id: int) -> Dict:
    settings = group_ref(chat_id).get()
    if isinstance(settings, dict):
        return settings
    return {}


def get_log_channel(chat_id: int) -> Optional[int]:
    log_chat_id = group_ref(chat_id).child("log_channel").get()
    if isinstance(log_chat_id, (int, float)):
        return int(log_chat_id)
    if isinstance(log_chat_id, str) and log_chat_id.strip():
        try:
            return int(log_chat_id.strip())
        except ValueError:
            LOGGER.warning("Invalid log channel stored for %s", chat_id)
    return None


async def send_log(chat_id: int, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    log_chat_id = get_log_channel(chat_id)
    if not log_chat_id:
        return
    try:
        await context.bot.send_message(log_chat_id, text, parse_mode="HTML")
    except TelegramError as exc:
        LOGGER.warning("Failed to send log message: %s", exc)

# -----------------------
# Command Handlers
# -----------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ BreakTheICX Bot is active!")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
📌 General Commands
/start → Activate bot
/help → Show this message
/about → About BreakTheICX Bot

🔹 Group Management
/welcome on|off → Enable/disable welcome messages
/goodbye on|off → Enable/disable goodbye messages
/setwelcome <text> → Set welcome message
/setgoodbye <text> → Set goodbye message
/ban (reply) → Ban user
/kick (reply) → Kick user
/mute (reply) → Mute user
/unmute (reply) → Unmute user
/promote (reply) → Promote user
/demote (reply) → Demote user

🔹 Filters & Anti-Spam
/addfilter <word> <reply> → Add filter
/delfilter <word> → Remove filter
/filters → List filters
/setflood <number> → Max messages per 10 sec

🔹 Logging & Settings
/setlog <chat_id> → Set log channel
/unsetlog → Remove log channel
/logstatus → Show log channel

🔹 Sangmata (Name History)
/history → Your past names
/history @username → Specific user history
"""
    await update.message.reply_text(text)

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 BreakTheICX Bot v1.0 — Group moderation & spam protection!")

# -----------------------
# Group Management
# -----------------------
async def set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can set welcome message.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /setwelcome <message>")
        return
    text = " ".join(context.args)
    group_ref(update.effective_chat.id).update({"welcome_text": text})
    await update.message.reply_text(f"✅ Welcome message set to:\n{text}")
    await send_log(
        update.effective_chat.id,
        context,
        f"✍️ <b>Welcome message updated by</b> {update.effective_user.mention_html()}.",
    )

async def set_goodbye(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can set goodbye message.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /setgoodbye <message>")
        return
    text = " ".join(context.args)
    group_ref(update.effective_chat.id).update({"goodbye_text": text})
    await update.message.reply_text(f"✅ Goodbye message set to:\n{text}")
    await send_log(
        update.effective_chat.id,
        context,
        f"✍️ <b>Goodbye message updated by</b> {update.effective_user.mention_html()}.",
    )

async def toggle_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can toggle welcome.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /welcome on|off")
        return
    status = context.args[0].lower() == "on"
    group_ref(update.effective_chat.id).update({"welcome_on": status})
    await update.message.reply_text(
        f"✅ Welcome messages {'enabled' if status else 'disabled'}."
    )
    await send_log(
        update.effective_chat.id,
        context,
        f"⚙️ <b>Welcome messages {'enabled' if status else 'disabled'}</b> by {update.effective_user.mention_html()}.",
    )

async def toggle_goodbye(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can toggle goodbye.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /goodbye on|off")
        return
    status = context.args[0].lower() == "on"
    group_ref(update.effective_chat.id).update({"goodbye_on": status})
    await update.message.reply_text(
        f"✅ Goodbye messages {'enabled' if status else 'disabled'}."
    )
    await send_log(
        update.effective_chat.id,
        context,
        f"⚙️ <b>Goodbye messages {'enabled' if status else 'disabled'}</b> by {update.effective_user.mention_html()}.",
    )


async def set_flood_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can set flood limit.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /setflood <number>")
        return
    try:
        limit = int(context.args[0])
        if limit < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Flood limit must be a positive integer.")
        return
    group_ref(update.effective_chat.id).update({"flood_limit": limit})
    await update.message.reply_text(f"✅ Flood limit set to {limit} messages/10s.")
    await send_log(
        update.effective_chat.id,
        context,
        f"⚙️ <b>Flood limit</b> set to {limit} by {update.effective_user.mention_html()}.",
    )


async def add_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can add filters.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /addfilter <word> <reply>")
        return
    word = context.args[0].lower()
    reply = " ".join(context.args[1:])
    group_ref(update.effective_chat.id).child("filters").child(word).set(reply)
    await update.message.reply_text(f"✅ Filter for '{word}' added.")
    await send_log(
        update.effective_chat.id,
        context,
        f"🛡️ <b>Filter added</b> for <code>{word}</code> by {update.effective_user.mention_html()}.",
    )


async def delete_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can delete filters.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /delfilter <word>")
        return
    word = context.args[0].lower()
    filter_node = group_ref(update.effective_chat.id).child("filters").child(word)
    if filter_node.get() is None:
        await update.message.reply_text("❓ Filter not found.")
        return
    filter_node.delete()
    await update.message.reply_text(f"✅ Filter '{word}' removed.")
    await send_log(
        update.effective_chat.id,
        context,
        f"🛡️ <b>Filter removed</b> for <code>{word}</code> by {update.effective_user.mention_html()}.",
    )


async def list_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    filters_dict = group_ref(update.effective_chat.id).child("filters").get() or {}
    if not isinstance(filters_dict, dict) or not filters_dict:
        await update.message.reply_text("No filters set.")
        return
    entries = "\n".join(
        f"• <code>{word}</code> → {reply}" for word, reply in sorted(filters_dict.items())
    )
    await update.message.reply_text(
        f"🛡️ Active filters:\n{entries}", parse_mode="HTML"
    )

# -----------------------
# Moderation Commands
# -----------------------
async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user to /ban them.")
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can use this command.")
        return
    target = update.message.reply_to_message.from_user
    chat_id = update.effective_chat.id
    group_ref(chat_id).child("blacklist").child(str(target.id)).set(True)
    try:
        await update.effective_chat.ban_member(target.id)
    except TelegramError as exc:
        LOGGER.warning("Failed to ban user: %s", exc)
        await update.message.reply_text("⚠️ Failed to ban the user. Check bot permissions.")
        return
    await update.message.reply_text(
        f"🚫 {target.mention_html()} banned.", parse_mode="HTML"
    )
    await send_log(
        chat_id,
        context,
        f"⛔️ <b>Banned</b> {target.mention_html()} by {update.effective_user.mention_html()}.",
    )

async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user to /unban them.")
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can use this command.")
        return
    target = update.message.reply_to_message.from_user
    chat_id = update.effective_chat.id
    group_ref(chat_id).child("blacklist").child(str(target.id)).delete()
    try:
        await update.effective_chat.unban_member(target.id)
    except TelegramError as exc:
        LOGGER.warning("Failed to unban user: %s", exc)
        await update.message.reply_text("⚠️ Failed to unban the user. Check bot permissions.")
        return
    await update.message.reply_text(
        f"✅ {target.mention_html()} unbanned.", parse_mode="HTML"
    )
    await send_log(
        chat_id,
        context,
        f"♻️ <b>Unbanned</b> {target.mention_html()} by {update.effective_user.mention_html()}.",
    )

async def kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user to /kick them.")
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can use this command.")
        return
    target = update.message.reply_to_message.from_user
    try:
        await update.effective_chat.ban_member(target.id)
        await update.effective_chat.unban_member(target.id)
    except TelegramError as exc:
        LOGGER.warning("Failed to kick user: %s", exc)
        await update.message.reply_text("⚠️ Failed to kick the user. Check bot permissions.")
        return
    await update.message.reply_text(
        f"👢 {target.mention_html()} kicked.", parse_mode="HTML"
    )
    await send_log(
        update.effective_chat.id,
        context,
        f"👢 <b>Kicked</b> {target.mention_html()} by {update.effective_user.mention_html()}.",
    )

async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user to /mute them.")
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can use this command.")
        return
    target = update.message.reply_to_message.from_user
    try:
        await update.effective_chat.restrict_member(
            target.id, permissions=ChatPermissions(can_send_messages=False)
        )
    except TelegramError as exc:
        LOGGER.warning("Failed to mute user: %s", exc)
        await update.message.reply_text("⚠️ Failed to mute the user. Check bot permissions.")
        return
    await update.message.reply_text(
        f"🔇 {target.mention_html()} muted.", parse_mode="HTML"
    )
    await send_log(
        update.effective_chat.id,
        context,
        f"🔇 <b>Muted</b> {target.mention_html()} by {update.effective_user.mention_html()}.",
    )

async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user to /unmute them.")
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can use this command.")
        return
    target = update.message.reply_to_message.from_user
    try:
        await update.effective_chat.restrict_member(
            target.id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
    except TelegramError as exc:
        LOGGER.warning("Failed to unmute user: %s", exc)
        await update.message.reply_text("⚠️ Failed to unmute the user. Check bot permissions.")
        return
    await update.message.reply_text(
        f"🔊 {target.mention_html()} unmuted.", parse_mode="HTML"
    )
    await send_log(
        update.effective_chat.id,
        context,
        f"🔊 <b>Unmuted</b> {target.mention_html()} by {update.effective_user.mention_html()}.",
    )


async def promote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user to /promote them.")
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can use this command.")
        return
    target = update.message.reply_to_message.from_user
    chat_id = update.effective_chat.id
    try:
        await context.bot.promote_chat_member(
            chat_id=chat_id,
            user_id=target.id,
            can_manage_chat=True,
            can_delete_messages=True,
            can_manage_video_chats=True,
            can_restrict_members=True,
            can_invite_users=True,
            can_pin_messages=True,
        )
    except TelegramError as exc:
        LOGGER.warning("Failed to promote user: %s", exc)
        await update.message.reply_text("⚠️ Failed to promote the user. Check bot permissions.")
        return
    await update.message.reply_text(
        f"⭐ {target.mention_html()} promoted.", parse_mode="HTML"
    )
    await send_log(
        chat_id,
        context,
        f"⭐ <b>Promoted</b> {target.mention_html()} by {update.effective_user.mention_html()}.",
    )


async def demote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user to /demote them.")
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can use this command.")
        return
    target = update.message.reply_to_message.from_user
    chat_id = update.effective_chat.id
    try:
        await context.bot.promote_chat_member(
            chat_id=chat_id,
            user_id=target.id,
            is_anonymous=False,
            can_manage_chat=False,
            can_delete_messages=False,
            can_manage_video_chats=False,
            can_restrict_members=False,
            can_invite_users=False,
            can_pin_messages=False,
            can_promote_members=False,
        )
    except TelegramError as exc:
        LOGGER.warning("Failed to demote user: %s", exc)
        await update.message.reply_text("⚠️ Failed to demote the user. Check bot permissions.")
        return
    await update.message.reply_text(
        f"⬇️ {target.mention_html()} demoted.", parse_mode="HTML"
    )
    await send_log(
        chat_id,
        context,
        f"⬇️ <b>Demoted</b> {target.mention_html()} by {update.effective_user.mention_html()}.",
    )


async def set_log_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can set log channel.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /setlog <chat_id>")
        return
    chat_id_arg = context.args[0]
    try:
        log_chat_id = int(chat_id_arg)
    except ValueError:
        await update.message.reply_text("Log channel must be a numeric chat ID.")
        return
    group_ref(update.effective_chat.id).update({"log_channel": log_chat_id})
    await update.message.reply_text(f"✅ Log channel set to {log_chat_id}.")
    await send_log(
        update.effective_chat.id,
        context,
        f"📝 <b>Log channel updated</b> to <code>{log_chat_id}</code> by {update.effective_user.mention_html()}.",
    )


async def unset_log_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can unset log channel.")
        return
    group_ref(update.effective_chat.id).child("log_channel").delete()
    await update.message.reply_text("✅ Log channel removed.")
    await send_log(
        update.effective_chat.id,
        context,
        f"📝 <b>Log channel removed</b> by {update.effective_user.mention_html()}.",
    )


async def log_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_chat_id = get_log_channel(update.effective_chat.id)
    if log_chat_id:
        await update.message.reply_text(f"📝 Logs are sent to: <code>{log_chat_id}</code>", parse_mode="HTML")
    else:
        await update.message.reply_text("ℹ️ No log channel configured.")


async def welcome_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return
    chat_id = update.effective_chat.id
    settings = get_group_settings(chat_id)
    if not settings.get("welcome_on", True):
        return
    template = settings.get("welcome_text") or DEFAULT_WELCOME
    for member in message.new_chat_members:
        if member.is_bot:
            continue
        text = format_name_vars(template, member)
        await message.reply_text(text)
        await send_log(
            chat_id,
            context,
            f"👋 <b>Welcome</b> sent to {member.mention_html()}.",
        )


async def member_left(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.left_chat_member:
        return
    chat_id = update.effective_chat.id
    settings = get_group_settings(chat_id)
    if not settings.get("goodbye_on", True):
        return
    member = message.left_chat_member
    if member.is_bot:
        return
    template = settings.get("goodbye_text") or DEFAULT_GOODBYE
    text = format_name_vars(template, member)
    await message.reply_text(text)
    await send_log(
        chat_id,
        context,
        f"👋 <b>Goodbye</b> sent for {member.mention_html()}.",
    )

# -----------------------
# Name History (Sangmata)
# -----------------------
async def track_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.is_bot:
        return
    history = get_name_history(user.id)
    display_name = " ".join(
        part for part in [user.first_name, user.last_name] if part
    ).strip() or user.first_name or "Unknown"
    username = f"@{user.username}" if user.username else "no_username"
    new_name = f"{display_name} ({username})"
    if not history or history[-1] != new_name:
        user_ref(user.id).child("history").push(new_name)

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        username = context.args[0].lstrip("@")
        all_users = USERS_REF.get() or {}
        if not isinstance(all_users, dict):
            all_users = {}
        username_lower = username.lower()
        for _uid, data in all_users.items():
            hist_list = history_to_list(data.get("history"))
            if any(username_lower in h.lower() for h in hist_list):
                hist = "\n".join(hist_list)
                await update.message.reply_text(
                    f"History of {username}:\n{hist}"
                )
                return
        await update.message.reply_text("User not found.")
    else:
        user = update.effective_user
        if not user:
            await update.message.reply_text("Unable to determine user.")
            return
        hist = get_name_history(user.id)
        if not hist:
            await update.message.reply_text("No name history yet.")
            return
        await update.message.reply_text("Your name history:\n" + "\n".join(hist))

# -----------------------
# Message Handler (Flood, Filters, Auto Ban)
# -----------------------
async def check_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or message.new_chat_members or message.left_chat_member:
        return
    user = update.effective_user
    if not user or user.is_bot:
        return
    chat_id = update.effective_chat.id
    await track_name(update, context)

    # Auto-ban repeated offenders
    if is_banned(chat_id, user.id):
        try:
            await update.effective_chat.ban_member(user.id)
            await send_log(
                chat_id,
                context,
                f"⛔️ <b>Auto-ban</b> enforced on {user.mention_html()}.",
            )
        except TelegramError as exc:
            LOGGER.warning("Failed to auto-ban user: %s", exc)
        return

    # Flood control
    now = time.time()
    key = (chat_id, user.id)
    user_message_times[key].append(now)
    user_message_times[key] = [t for t in user_message_times[key] if now - t < 10]
    flood_limit = get_group_settings(chat_id).get("flood_limit", 5)
    try:
        flood_limit = int(flood_limit)
    except (TypeError, ValueError):
        flood_limit = 5
    if len(user_message_times[key]) > flood_limit:
        try:
            await update.effective_chat.restrict_member(
                user.id, permissions=ChatPermissions(can_send_messages=False)
            )
        except TelegramError as exc:
            LOGGER.warning("Failed to mute for flooding: %s", exc)
        else:
            await message.reply_text(
                f"🚨 {user.mention_html()} muted for flooding.", parse_mode="HTML"
            )
            await send_log(
                chat_id,
                context,
                f"🚨 <b>Flood mute</b> applied to {user.mention_html()}.",
            )
        return

    # Filters
    filters_dict = group_ref(chat_id).child("filters").get()
    if not isinstance(filters_dict, dict):
        return
    text = message.text or message.caption or ""
    text_lower = text.lower()
    for word, reply in filters_dict.items():
        if not isinstance(word, str) or not isinstance(reply, str):
            continue
        trigger = word.lower()
        if trigger and trigger in text_lower:
            await message.reply_text(reply)
            await send_log(
                chat_id,
                context,
                f"🛡️ <b>Filter</b> '<code>{trigger}</code>' triggered by {user.mention_html()}.",
            )
            break

# -----------------------
# Main
# -----------------------
if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("about", about))
    app.add_handler(CommandHandler("setwelcome", set_welcome))
    app.add_handler(CommandHandler("setgoodbye", set_goodbye))
    app.add_handler(CommandHandler("welcome", toggle_welcome))
    app.add_handler(CommandHandler("goodbye", toggle_goodbye))
    app.add_handler(CommandHandler("setflood", set_flood_limit))
    app.add_handler(CommandHandler("addfilter", add_filter))
    app.add_handler(CommandHandler("delfilter", delete_filter))
    app.add_handler(CommandHandler("filters", list_filters))
    app.add_handler(CommandHandler("setlog", set_log_channel))
    app.add_handler(CommandHandler("unsetlog", unset_log_channel))
    app.add_handler(CommandHandler("logstatus", log_status))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("kick", kick))
    app.add_handler(CommandHandler("mute", mute))
    app.add_handler(CommandHandler("unmute", unmute))
    app.add_handler(CommandHandler("promote", promote))
    app.add_handler(CommandHandler("demote", demote))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_members))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, member_left))
    app.add_handler(MessageHandler(filters.ALL, check_messages))

    app.run_polling()
