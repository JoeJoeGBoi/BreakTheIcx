import os
import logging
import firebase_admin
from firebase_admin import credentials, db
from telegram import Update, ChatPermissions
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
)
from collections import defaultdict
import time

logging.basicConfig(level=logging.INFO)

# Firebase init
cred = credentials.Certificate(os.getenv("FIREBASE_CRED"))
firebase_admin.initialize_app(cred, {
    "databaseURL": os.getenv("FIREBASE_DB_URL")
})

# References
ADMINS_REF = db.reference("admins")
GROUPS_REF = db.reference("groups")
USERS_REF = db.reference("users")

BOT_TOKEN = os.getenv("BOT_TOKEN")

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
    text = " ".join(context.args)
    group_ref(update.effective_chat.id).update({"welcome_text": text})
    await update.message.reply_text(f"✅ Welcome message set to:\n{text}")

async def set_goodbye(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can set goodbye message.")
        return
    text = " ".join(context.args)
    group_ref(update.effective_chat.id).update({"goodbye_text": text})
    await update.message.reply_text(f"✅ Goodbye message set to:\n{text}")

async def toggle_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can toggle welcome.")
        return
    status = context.args[0].lower() == "on"
    group_ref(update.effective_chat.id).update({"welcome_on": status})
    await update.message.reply_text(f"✅ Welcome messages {'enabled' if status else 'disabled'}.")

async def toggle_goodbye(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only admins can toggle goodbye.")
        return
    status = context.args[0].lower() == "on"
    group_ref(update.effective_chat.id).update({"goodbye_on": status})
    await update.message.reply_text(f"✅ Goodbye messages {'enabled' if status else 'disabled'}.")

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
    except:
        pass
    await update.message.reply_text(f"🚫 {target.mention_html()} banned.", parse_mode="HTML")

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
    except:
        pass
    await update.message.reply_text(f"✅ {target.mention_html()} unbanned.", parse_mode="HTML")

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
    except:
        pass
    await update.message.reply_text(f"👢 {target.mention_html()} kicked.", parse_mode="HTML")

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

# -----------------------
# Name History (Sangmata)
# -----------------------
async def track_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    history = user_ref(user.id).child("history").get() or []
    new_name = f"{user.first_name} {user.last_name or ''} (@{user.username or 'no_username'})"
    if not history or history[-1] != new_name:
        user_ref(user.id).child("history").push(new_name)

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        username = context.args[0].lstrip("@")
        all_users = USERS_REF.get() or {}
        for uid, data in all_users.items():
            if any(username in h for h in data.get("history", [])):
                hist = "\n".join(data.get("history", []))
                await update.message.reply_text(f"History of {username}:\n{hist}")
                return
        await update.message.reply_text("User not found.")
    else:
        user_id = update.effective_user.id
        hist = user_ref(user_id).child("history").get() or []
        await update.message.reply_text("Your name history:\n" + "\n".join(hist))

# -----------------------
# Message Handler (Flood, Filters, Auto Ban)
# -----------------------
async def check_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    # Track name
    await track_name(update, context)
    # Auto-ban
    if is_banned(chat_id, user.id):
        try:
            await update.effective_chat.ban_member(user.id)
        except:
            pass
        return
    # Flood control
    now = time.time()
    user_message_times[(chat_id, user.id)].append(now)
    user_message_times[(chat_id, user.id)] = [t for t in user_message_times[(chat_id, user.id)] if now - t < 10]
    flood_limit = group_ref(chat_id).child("flood_limit").get() or 5
    if len(user_message_times[(chat_id, user.id)]) > flood_limit:
        await update.effective_chat.restrict_member(user.id, permissions=ChatPermissions(can_send_messages=False))
        await update.message.reply_text(f"🚨 {user.mention_html()} muted for flooding.", parse_mode="HTML")
        return
    # Filters
    filters_dict = group_ref(chat_id).child("filters").get() or {}
    for word, reply in filters_dict.items():
        if word.lower() in update.message.text.lower():
            await update.message.reply_text(reply)

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
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("kick", kick))
    app.add_handler(CommandHandler("mute", mute))
    app.add_handler(CommandHandler("unmute", unmute))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(MessageHandler(filters.ALL, check_messages))

    app.run_polling()
