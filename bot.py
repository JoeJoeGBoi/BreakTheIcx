import html
import os
import logging
import time
from collections import defaultdict
from typing import Any, Dict, List

import firebase_admin
from firebase_admin import credentials, db
from telegram import ChatPermissions, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO)

# Firebase init
firebase_cred_path = os.getenv("FIREBASE_CRED")
firebase_db_url = os.getenv("FIREBASE_DB_URL")

if not firebase_cred_path or not firebase_db_url:
    raise RuntimeError("FIREBASE_CRED and FIREBASE_DB_URL must be set")

cred = credentials.Certificate(firebase_cred_path)
firebase_admin.initialize_app(cred, {"databaseURL": firebase_db_url})

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
FIREBASE_INVALID_KEY_CHARS = (".", "#", "$", "[", "]", "/")


def sanitize_key(key: str) -> str:
    sanitized = key
    for char in FIREBASE_INVALID_KEY_CHARS:
        sanitized = sanitized.replace(char, "_")
    return sanitized


def ensure_list(raw: Any) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [entry for entry in raw if isinstance(entry, str)]
    if isinstance(raw, dict):
        ordered_keys = sorted(raw.keys())
        return [raw[key] for key in ordered_keys if isinstance(raw[key], str)]
    return []


def normalize_filters(raw: Any) -> Dict[str, Dict[str, str]]:
    result: Dict[str, Dict[str, str]] = {}
    if not isinstance(raw, dict):
        return result
    for key, value in raw.items():
        if isinstance(value, dict):
            trigger = value.get("trigger")
            reply = value.get("reply")
            if trigger and reply is not None:
                result[str(key)] = {
                    "trigger": str(trigger),
                    "reply": str(reply),
                }
        elif isinstance(value, str):
            result[str(key)] = {"trigger": str(key), "reply": value}
    return result


def get_filters(chat_id: int) -> Dict[str, Dict[str, str]]:
    return normalize_filters(group_ref(chat_id).child("filters").get())


def update_name_history(user) -> None:
    if user is None:
        return
    history_ref = user_ref(user.id).child("history")
    history = ensure_list(history_ref.get())
    new_name = f"{user.first_name or ''} {user.last_name or ''} (@{user.username or 'no_username'})".strip()
    if not history or history[-1] != new_name:
        history.append(new_name)
        history_ref.set(history)


async def send_log(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
    log_chat_id = group_ref(chat_id).child("log_channel").get()
    if not log_chat_id:
        return
    try:
        target_chat = int(str(log_chat_id))
    except (TypeError, ValueError):
        target_chat = log_chat_id
    try:
        await context.bot.send_message(chat_id=target_chat, text=text, parse_mode="HTML")
    except Exception:
        logging.warning("Failed to send log message for chat %s", chat_id, exc_info=True)


def is_admin(user_id: int) -> bool:
    return ADMINS_REF.child(str(user_id)).get() is True

def group_ref(chat_id: int):
    return GROUPS_REF.child(str(chat_id))

def user_ref(user_id: int):
    return USERS_REF.child(str(user_id))

def is_banned(chat_id: int, user_id: int) -> bool:
    return group_ref(chat_id).child("blacklist").child(str(user_id)).get() is True

def format_name_vars(text, user):
    return text.replace("{first}", user.first_name or "") \
               .replace("{last}", user.last_name or "") \
               .replace("{username}", f"@{user.username}" if user.username else "")

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
/unban (reply) → Unban user
/kick (reply) → Kick user
/mute (reply) → Mute user
/unmute (reply) → Unmute user

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
        await update.message.reply_text("Usage: /setwelcome <text>")
        return
    text = " ".join(context.args).strip()
    group_ref(update.effective_chat.id).update({"welcome_text": text})
    await update.message.reply_text(f"✅ Welcome message set to:\n{text}")
    await send_log(
        context,
        update.effective_chat.id,
        f"📝 Welcome message updated by {update.effective_user.mention_html()}: {html.escape(text)}",
    )

async def set_goodbye(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can set goodbye message.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /setgoodbye <text>")
        return
    text = " ".join(context.args).strip()
    group_ref(update.effective_chat.id).update({"goodbye_text": text})
    await update.message.reply_text(f"✅ Goodbye message set to:\n{text}")
    await send_log(
        context,
        update.effective_chat.id,
        f"📤 Goodbye message updated by {update.effective_user.mention_html()}: {html.escape(text)}",
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
        context,
        update.effective_chat.id,
        f"🔔 Welcome messages {'enabled' if status else 'disabled'} by {update.effective_user.mention_html()}.",
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
        context,
        update.effective_chat.id,
        f"🔔 Goodbye messages {'enabled' if status else 'disabled'} by {update.effective_user.mention_html()}.",
    )

# -----------------------
# Filters, Flood & Logging
# -----------------------
async def set_flood(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can set flood limit.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /setflood <number>")
        return
    try:
        limit = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Flood limit must be a number.")
        return
    if limit < 1:
        await update.message.reply_text("Flood limit must be at least 1.")
        return
    group_ref(update.effective_chat.id).update({"flood_limit": limit})
    await update.message.reply_text(f"✅ Flood limit set to {limit} messages per 10 seconds.")
    await send_log(
        context,
        update.effective_chat.id,
        f"🌊 Flood limit set to {limit} by {update.effective_user.mention_html()}.",
    )


async def add_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can add filters.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /addfilter <word> <reply>")
        return
    trigger = context.args[0]
    reply_text = " ".join(context.args[1:]).strip()
    if not reply_text:
        await update.message.reply_text("Reply text cannot be empty.")
        return
    key = sanitize_key(trigger.lower())
    filters_data = get_filters(update.effective_chat.id)
    filters_data[key] = {"trigger": trigger, "reply": reply_text}
    group_ref(update.effective_chat.id).child("filters").set(filters_data)
    await update.message.reply_text(f"✅ Filter added for '{trigger}'.")
    await send_log(
        context,
        update.effective_chat.id,
        f"🛡️ Filter '<b>{html.escape(trigger)}</b>' added by {update.effective_user.mention_html()}.",
    )


async def delete_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can delete filters.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /delfilter <word>")
        return
    trigger = context.args[0]
    key = sanitize_key(trigger.lower())
    filters_data = get_filters(update.effective_chat.id)
    removed = filters_data.pop(key, None)
    if removed is None:
        await update.message.reply_text(f"No filter found for '{trigger}'.")
        return
    group_ref(update.effective_chat.id).child("filters").set(filters_data)
    await update.message.reply_text(f"✅ Filter '{trigger}' removed.")
    await send_log(
        context,
        update.effective_chat.id,
        f"🧹 Filter '<b>{html.escape(trigger)}</b>' removed by {update.effective_user.mention_html()}.",
    )


async def list_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context  # unused
    filters_data = get_filters(update.effective_chat.id)
    if not filters_data:
        await update.message.reply_text("No filters configured.")
        return
    sorted_filters = sorted(
        filters_data.values(), key=lambda item: item.get("trigger", "").lower()
    )
    lines = [f"• {item['trigger']} → {item['reply']}" for item in sorted_filters]
    await update.message.reply_text("Current filters:\n" + "\n".join(lines))


async def set_log_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can set log channel.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /setlog <chat_id>")
        return
    target = context.args[0]
    group_ref(update.effective_chat.id).update({"log_channel": target})
    await update.message.reply_text(f"✅ Log channel set to {target}.")
    await send_log(
        context,
        update.effective_chat.id,
        f"🗒️ Log channel updated by {update.effective_user.mention_html()} to {html.escape(target)}.",
    )


async def unset_log_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can unset log channel.")
        return
    if group_ref(update.effective_chat.id).child("log_channel").get():
        await send_log(
            context,
            update.effective_chat.id,
            f"🗒️ Log channel removed by {update.effective_user.mention_html()}.",
        )
    group_ref(update.effective_chat.id).child("log_channel").delete()
    await update.message.reply_text("✅ Log channel removed.")


async def log_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context  # unused
    log_chat_id = group_ref(update.effective_chat.id).child("log_channel").get()
    if log_chat_id:
        await update.message.reply_text(f"ℹ️ Logging to chat ID: {log_chat_id}")
    else:
        await update.message.reply_text("ℹ️ Logging channel not configured.")

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
    except Exception:
        logging.debug("Failed to ban user %s in chat %s", target.id, chat_id, exc_info=True)
    await update.message.reply_text(f"🚫 {target.mention_html()} banned.", parse_mode="HTML")
    await send_log(
        context,
        chat_id,
        f"🚫 {target.mention_html()} banned by {update.effective_user.mention_html()}.",
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
    except Exception:
        logging.debug("Failed to unban user %s in chat %s", target.id, chat_id, exc_info=True)
    await update.message.reply_text(f"✅ {target.mention_html()} unbanned.", parse_mode="HTML")
    await send_log(
        context,
        chat_id,
        f"✅ {target.mention_html()} unbanned by {update.effective_user.mention_html()}.",
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
    except Exception:
        logging.debug("Failed to kick user %s in chat %s", target.id, update.effective_chat.id, exc_info=True)
    await update.message.reply_text(f"👢 {target.mention_html()} kicked.", parse_mode="HTML")
    await send_log(
        context,
        update.effective_chat.id,
        f"👢 {target.mention_html()} kicked by {update.effective_user.mention_html()}.",
    )

async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user to /mute them.")
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can use this command.")
        return
    target = update.message.reply_to_message.from_user
    await update.effective_chat.restrict_member(target.id, permissions=ChatPermissions(can_send_messages=False))
    await update.message.reply_text(f"🔇 {target.mention_html()} muted.", parse_mode="HTML")
    await send_log(
        context,
        update.effective_chat.id,
        f"🔇 {target.mention_html()} muted by {update.effective_user.mention_html()}.",
    )

async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user to /unmute them.")
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can use this command.")
        return
    target = update.message.reply_to_message.from_user
    await update.effective_chat.restrict_member(target.id, permissions=ChatPermissions(can_send_messages=True))
    await update.message.reply_text(f"🔊 {target.mention_html()} unmuted.", parse_mode="HTML")
    await send_log(
        context,
        update.effective_chat.id,
        f"🔊 {target.mention_html()} unmuted by {update.effective_user.mention_html()}.",
    )

# -----------------------
# Name History (Sangmata)
# -----------------------
async def track_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context  # unused
    user = update.effective_user
    update_name_history(user)

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        username = context.args[0].lstrip("@")
        all_users = USERS_REF.get() or {}
        for uid, data in all_users.items():
            if not isinstance(data, dict):
                continue
            hist_entries = ensure_list(data.get("history"))
            if any(username.lower() in entry.lower() for entry in hist_entries):
                hist = "\n".join(hist_entries) if hist_entries else "No history recorded."
                await update.message.reply_text(f"History of {username}:\n{hist}")
                return
        await update.message.reply_text("User not found.")
    else:
        user_id = update.effective_user.id
        hist = ensure_list(user_ref(user_id).child("history").get())
        if hist:
            await update.message.reply_text("Your name history:\n" + "\n".join(hist))
        else:
            await update.message.reply_text("No name history recorded yet.")

# -----------------------
# Message Handler (Flood, Filters, Auto Ban)
# -----------------------
async def check_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message

    if message is None:
        # Nothing to do for updates without a message payload (e.g. joins, callbacks)
        return

    chat = update.effective_chat
    if chat is None:
        return
    chat_id = chat.id

    if message.new_chat_members:
        welcome_on = group_ref(chat_id).child("welcome_on").get()
        welcome_text = group_ref(chat_id).child("welcome_text").get() or "Welcome, {first}!"
        for member in message.new_chat_members:
            update_name_history(member)
            if welcome_on:
                await message.reply_text(format_name_vars(welcome_text, member))
            await send_log(
                context,
                chat_id,
                f"👋 {member.mention_html()} joined {html.escape(chat.title or 'the chat')}.",
            )
        return

    if message.left_chat_member:
        member = message.left_chat_member
        update_name_history(member)
        goodbye_on = group_ref(chat_id).child("goodbye_on").get()
        goodbye_text = group_ref(chat_id).child("goodbye_text").get() or "Goodbye, {first}!"
        if goodbye_on:
            await message.reply_text(format_name_vars(goodbye_text, member))
        await send_log(
            context,
            chat_id,
            f"👋 {member.mention_html()} left {html.escape(chat.title or 'the chat')}.",
        )
        return

    user = message.from_user
    if user is None:
        return

    update_name_history(user)

    if is_banned(chat_id, user.id):
        try:
            await chat.ban_member(user.id)
        except Exception:
            logging.debug("Failed to re-ban user %s", user.id, exc_info=True)
        await send_log(
            context,
            chat_id,
            f"⛔ Blocked message from banned user {user.mention_html()}.",
        )
        return

    now = time.time()
    user_key = (chat_id, user.id)
    user_message_times[user_key].append(now)
    user_message_times[user_key] = [t for t in user_message_times[user_key] if now - t < 10]
    flood_limit = group_ref(chat_id).child("flood_limit").get() or 5
    if len(user_message_times[user_key]) > flood_limit:
        await chat.restrict_member(user.id, permissions=ChatPermissions(can_send_messages=False))
        await message.reply_text(f"🚨 {user.mention_html()} muted for flooding.", parse_mode="HTML")
        await send_log(
            context,
            chat_id,
            f"🚨 {user.mention_html()} muted for flooding (> {flood_limit} msgs/10s).",
        )
        user_message_times[user_key].clear()
        return

    filters_dict = get_filters(chat_id)
    text = message.text or message.caption or ""
    lowered = text.lower()
    for data in filters_dict.values():
        trigger = data.get("trigger", "")
        reply_text = data.get("reply", "")
        if trigger and reply_text and trigger.lower() in lowered:
            await message.reply_text(reply_text)
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
    app.add_handler(CommandHandler("setflood", set_flood))
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
    app.add_handler(CommandHandler("history", history))
    app.add_handler(MessageHandler(filters.ALL, check_messages))

    app.run_polling()
