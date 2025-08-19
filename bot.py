import os
import logging
import firebase_admin
from firebase_admin import credentials, db
from telegram import Update, ChatPermissions
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
)

logging.basicConfig(level=logging.INFO)

# Firebase init
cred = credentials.Certificate(os.getenv("FIREBASE_CRED"))
firebase_admin.initialize_app(cred, {
    "databaseURL": os.getenv("FIREBASE_DB_URL")
})

ADMINS_REF = db.reference("admins")

BOT_TOKEN = os.getenv("BOT_TOKEN")

# Helpers
def is_admin(user_id: int) -> bool:
    return ADMINS_REF.child(str(user_id)).get() is True

def group_ref(chat_id: int):
    return db.reference(f"groups/{chat_id}")

def is_banned(chat_id: int, user_id: int) -> bool:
    return group_ref(chat_id).child("blacklist").child(str(user_id)).get() is True

# Auto-check messages
async def check_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if is_banned(chat_id, user_id):
        try:
            await update.effective_chat.ban_member(user_id)
            await context.bot.send_message(update.effective_chat.id,
                                           f"🚫 {update.effective_user.mention_html()} is banned in this group.",
                                           parse_mode="HTML")
        except Exception as e:
            logging.error(f"Ban enforcement failed: {e}")

# /ban command (reply to user)
async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user to /ban them.")
        return

    admin_id = update.effective_user.id
    if not is_admin(admin_id):
        await update.message.reply_text("🚫 Only admins can use this command.")
        return

    target = update.message.reply_to_message.from_user
    chat_id = update.effective_chat.id

    group_ref(chat_id).child("blacklist").child(str(target.id)).set(True)

    try:
        await update.effective_chat.ban_member(target.id)
    except:
        pass

    await update.message.reply_text(f"✅ {target.mention_html()} has been banned in this group.", parse_mode="HTML")

# /unban
async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user to /unban them.")
        return

    admin_id = update.effective_user.id
    if not is_admin(admin_id):
        await update.message.reply_text("🚫 Only admins can use this command.")
        return

    target = update.message.reply_to_message.from_user
    chat_id = update.effective_chat.id

    group_ref(chat_id).child("blacklist").child(str(target.id)).delete()
    try:
        await update.effective_chat.unban_member(target.id)
    except:
        pass

    await update.message.reply_text(f"✅ {target.mention_html()} has been unbanned in this group.", parse_mode="HTML")

# /kick
async def kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user to /kick them.")
        return

    admin_id = update.effective_user.id
    if not is_admin(admin_id):
        await update.message.reply_text("🚫 Only admins can use this command.")
        return

    target = update.message.reply_to_message.from_user
    try:
        await update.effective_chat.ban_member(target.id)
        await update.effective_chat.unban_member(target.id)  # quick unban = kick
    except:
        pass

    await update.message.reply_text(f"👢 {target.mention_html()} has been kicked.", parse_mode="HTML")

# /listbans
async def listbans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = update.effective_user.id
    if not is_admin(admin_id):
        await update.message.reply_text("🚫 Only admins can use this command.")
        return

    chat_id = update.effective_chat.id
    banned = group_ref(chat_id).child("blacklist").get() or {}
    if not banned:
        await update.message.reply_text("✅ No users are banned in this group.")
        return

    text = "\n".join([f"🚫 {uid}" for uid in banned.keys()])
    await update.message.reply_text(f"🔥 Banned users in this group:\n{text}")

# Entrypoint
if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.ALL, check_messages))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("kick", kick))
    app.add_handler(CommandHandler("listbans", listbans))

    app.run_polling()
