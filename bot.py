import logging
import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Iterable, List, Optional, Tuple

import firebase_admin
from firebase_admin import credentials, db
from telegram import ChatPermissions, Update, User
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
LOG_LEVEL_NAME = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_LEVEL = getattr(logging, LOG_LEVEL_NAME, logging.INFO)
logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
LOGGER = logging.getLogger("BreakTheICXBot")
LOGGER.setLevel(LOG_LEVEL)

DEFAULT_WELCOME = "👋 Welcome, {first}!"
DEFAULT_GOODBYE = "👋 Goodbye, {first}!"
FLOOD_WINDOW_SECONDS = 10
DEFAULT_FLOOD_LIMIT = 5


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} environment variable is required")
    return value


def initialize_firebase() -> None:
    cred_path = require_env("FIREBASE_CRED")
    db_url = require_env("FIREBASE_DB_URL")

    if not os.path.isfile(cred_path):
        raise FileNotFoundError(
            f"Firebase credential file not found at '{cred_path}'"
        )

    try:
        firebase_admin.get_app()
    except ValueError:
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred, {"databaseURL": db_url})


@dataclass
class HistoryMatch:
    user_id: str
    entries: List[str]


def history_to_list(raw_history: Optional[Iterable]) -> List[str]:
    if raw_history is None:
        return []
    if isinstance(raw_history, list):
        return [str(item) for item in raw_history if item]
    if isinstance(raw_history, dict):
        items = [value for _, value in sorted(raw_history.items()) if value]
        return [str(item) for item in items]
    return []


class FloodTracker:
    """Track message timestamps per user to enforce flood control."""

    def __init__(self, window_seconds: int) -> None:
        self.window = window_seconds
        self.events: Dict[Tuple[int, int], Deque[float]] = {}

    def increment(self, chat_id: int, user_id: int, now: float) -> int:
        key = (chat_id, user_id)
        queue = self.events.setdefault(key, deque())
        queue.append(now)
        cutoff = now - self.window
        while queue and queue[0] <= cutoff:
            queue.popleft()
        if not queue:
            self.events.pop(key, None)
            return 0
        return len(queue)


class BreakTheICXBot:
    def __init__(self) -> None:
        self.bot_token = require_env("BOT_TOKEN")
        initialize_firebase()

        self.admins_ref = db.reference("admins")
        self.groups_ref = db.reference("groups")
        self.users_ref = db.reference("users")

        self.flood_tracker = FloodTracker(FLOOD_WINDOW_SECONDS)
        self.application: Application = (
            ApplicationBuilder()
            .token(self.bot_token)
            .concurrent_updates(True)
            .build()
        )
        self._register_handlers()

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------
    def _register_handlers(self) -> None:
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("about", self.about))

        self.application.add_handler(CommandHandler("setwelcome", self.set_welcome))
        self.application.add_handler(CommandHandler("setgoodbye", self.set_goodbye))
        self.application.add_handler(CommandHandler("welcome", self.toggle_welcome))
        self.application.add_handler(CommandHandler("goodbye", self.toggle_goodbye))
        self.application.add_handler(CommandHandler("setflood", self.set_flood_limit))

        self.application.add_handler(CommandHandler("addfilter", self.add_filter))
        self.application.add_handler(CommandHandler("delfilter", self.delete_filter))
        self.application.add_handler(CommandHandler("filters", self.list_filters))

        self.application.add_handler(CommandHandler("setlog", self.set_log_channel))
        self.application.add_handler(CommandHandler("unsetlog", self.unset_log_channel))
        self.application.add_handler(CommandHandler("logstatus", self.log_status))

        self.application.add_handler(CommandHandler("ban", self.ban))
        self.application.add_handler(CommandHandler("unban", self.unban))
        self.application.add_handler(CommandHandler("kick", self.kick))
        self.application.add_handler(CommandHandler("mute", self.mute))
        self.application.add_handler(CommandHandler("unmute", self.unmute))
        self.application.add_handler(CommandHandler("promote", self.promote))
        self.application.add_handler(CommandHandler("demote", self.demote))

        self.application.add_handler(CommandHandler("history", self.history))

        self.application.add_handler(
            MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, self.welcome_new_members)
        )
        self.application.add_handler(
            MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, self.member_left)
        )
        self.application.add_handler(MessageHandler(filters.ALL, self.check_messages))

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------
    def _is_admin(self, user_id: Optional[int]) -> bool:
        if user_id is None:
            return False
        try:
            return bool(self.admins_ref.child(str(user_id)).get())
        except Exception as exc:  # noqa: BLE001 - firebase exceptions vary
            LOGGER.error("Failed to check admin status: %s", exc)
            return False

    async def _ensure_admin(self, update: Update) -> bool:
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return False
        if not self._is_admin(user.id):
            await message.reply_text("🚫 Only configured admins may run this command.")
            return False
        return True

    def _group_ref(self, chat_id: int):
        return self.groups_ref.child(str(chat_id))

    def _user_ref(self, user_id: int):
        return self.users_ref.child(str(user_id))

    def _get_group_settings(self, chat_id: int) -> Dict:
        try:
            data = self._group_ref(chat_id).get()
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Failed to read settings for %s: %s", chat_id, exc)
            return {}
        if isinstance(data, dict):
            return data
        return {}

    def _update_group(self, chat_id: int, values: Dict[str, object]) -> bool:
        try:
            self._group_ref(chat_id).update(values)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Failed to update group %s: %s", chat_id, exc)
            return False
        return True

    def _delete_group_value(self, chat_id: int, key: str) -> bool:
        try:
            self._group_ref(chat_id).child(key).delete()
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Failed to delete %s for group %s: %s", key, chat_id, exc)
            return False
        return True

    def _get_filters(self, chat_id: int) -> Dict[str, str]:
        try:
            raw = self._group_ref(chat_id).child("filters").get()
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Failed to read filters for %s: %s", chat_id, exc)
            return {}
        if not isinstance(raw, dict):
            return {}
        filtered: Dict[str, str] = {}
        for key, value in raw.items():
            if isinstance(key, str) and isinstance(value, str):
                filtered[key] = value
        return filtered

    def _set_filter(self, chat_id: int, trigger: str, reply: str) -> bool:
        try:
            self._group_ref(chat_id).child("filters").child(trigger).set(reply)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Failed to set filter '%s' for %s: %s", trigger, chat_id, exc)
            return False
        return True

    def _remove_filter(self, chat_id: int, trigger: str) -> bool:
        try:
            self._group_ref(chat_id).child("filters").child(trigger).delete()
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Failed to delete filter '%s' for %s: %s", trigger, chat_id, exc)
            return False
        return True

    def _format_name(self, template: str, user: User) -> str:
        return (
            template.replace("{first}", user.first_name or "")
            .replace("{last}", user.last_name or "")
            .replace("{username}", f"@{user.username}" if user.username else "")
        )

    def _get_log_channel(self, chat_id: int) -> Optional[int]:
        settings = self._get_group_settings(chat_id)
        value = settings.get("log_channel") if isinstance(settings, dict) else None
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and value.strip():
            try:
                return int(value)
            except ValueError:
                LOGGER.warning("Log channel for %s is not numeric: %s", chat_id, value)
        return None

    async def _send_log(
        self, chat_id: int, context: ContextTypes.DEFAULT_TYPE, text: str
    ) -> None:
        log_chat = self._get_log_channel(chat_id)
        if log_chat is None:
            return
        try:
            await context.bot.send_message(
                chat_id=log_chat, text=text, parse_mode=ParseMode.HTML
            )
        except TelegramError as exc:
            LOGGER.warning("Unable to send log message to %s: %s", log_chat, exc)

    def _is_banned(self, chat_id: int, user_id: int) -> bool:
        try:
            return bool(
                self._group_ref(chat_id).child("blacklist").child(str(user_id)).get()
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Failed to read blacklist for %s: %s", chat_id, exc)
            return False

    def _set_ban_state(self, chat_id: int, user_id: int, state: bool) -> None:
        try:
            node = self._group_ref(chat_id).child("blacklist").child(str(user_id))
            if state:
                node.set(True)
            else:
                node.delete()
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Failed to update blacklist for %s/%s: %s", chat_id, user_id, exc)

    def _get_name_history(self, user_id: int) -> List[str]:
        try:
            raw = self._user_ref(user_id).child("history").get()
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Failed to read history for %s: %s", user_id, exc)
            return []
        return history_to_list(raw)

    def _append_name_history(self, user: User) -> None:
        if user.is_bot:
            return
        entry = " ".join(filter(None, [user.first_name, user.last_name or ""]))
        username = f"@{user.username}" if user.username else "no_username"
        formatted = f"{entry.strip() or user.first_name or 'Unknown'} ({username})"
        history = self._get_name_history(user.id)
        if history and history[-1] == formatted:
            return
        try:
            self._user_ref(user.id).child("history").push(formatted)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Failed to append history for %s: %s", user.id, exc)

    def _find_history_by_username(self, username: str) -> Optional[HistoryMatch]:
        username_lower = username.lower()
        try:
            all_users = self.users_ref.get() or {}
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Failed to read user history: %s", exc)
            return None
        if not isinstance(all_users, dict):
            return None
        for user_id, payload in all_users.items():
            if not isinstance(payload, dict):
                continue
            entries = history_to_list(payload.get("history"))
            if any(username_lower in entry.lower() for entry in entries):
                return HistoryMatch(user_id=str(user_id), entries=entries)
        return None

    def _parse_toggle(self, args: List[str]) -> Optional[bool]:
        if not args:
            return None
        value = args[0].strip().lower()
        if value in {"on", "true", "yes", "1"}:
            return True
        if value in {"off", "false", "no", "0"}:
            return False
        return None

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message:
            await message.reply_text("✅ BreakTheICX Bot is active and ready to help!")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if not message:
            return
        help_text = (
            "📌 <b>General Commands</b>\n"
            "/start – Check that the bot is alive\n"
            "/help – Show this message\n"
            "/about – Learn about BreakTheICX\n\n"
            "🔹 <b>Group Management</b>\n"
            "/welcome on|off – Enable or disable welcome messages\n"
            "/goodbye on|off – Enable or disable goodbye messages\n"
            "/setwelcome &lt;text&gt; – Configure welcome text\n"
            "/setgoodbye &lt;text&gt; – Configure goodbye text\n"
            "/setflood &lt;number&gt; – Flood limit in messages/10s\n\n"
            "🔹 <b>Moderation</b>\n"
            "/ban, /unban, /kick, /mute, /unmute, /promote, /demote (reply to a user)\n\n"
            "🔹 <b>Filters & Logging</b>\n"
            "/addfilter &lt;word&gt; &lt;reply&gt; – Add an auto-response\n"
            "/delfilter &lt;word&gt; – Remove a filter\n"
            "/filters – List active filters\n"
            "/setlog &lt;chat_id&gt; – Send moderation logs to chat\n"
            "/unsetlog – Stop sending logs\n"
            "/logstatus – Show the current log target\n\n"
            "🔹 <b>Name History</b>\n"
            "/history – Show your recorded names\n"
            "/history @username – Search another user\n"
        )
        await message.reply_text(help_text, parse_mode=ParseMode.HTML)

    async def about(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message:
            await message.reply_text(
                "🤖 BreakTheICX Bot – Telegram group moderation with Firebase persistence."
            )

    async def set_welcome(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None or not await self._ensure_admin(update):
            return
        if not context.args:
            await message.reply_text("Usage: /setwelcome <message>")
            return
        text = " ".join(context.args).strip()
        if not text:
            await message.reply_text("Welcome message cannot be empty.")
            return
        chat_id = update.effective_chat.id
        if self._update_group(chat_id, {"welcome_text": text}):
            await message.reply_text(f"✅ Welcome message set to:\n{text}")
            await self._send_log(
                chat_id,
                context,
                f"✍️ <b>Welcome message updated</b> by {update.effective_user.mention_html()}.",
            )
        else:
            await message.reply_text("⚠️ Failed to update welcome message. Try again later.")

    async def set_goodbye(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None or not await self._ensure_admin(update):
            return
        if not context.args:
            await message.reply_text("Usage: /setgoodbye <message>")
            return
        text = " ".join(context.args).strip()
        if not text:
            await message.reply_text("Goodbye message cannot be empty.")
            return
        chat_id = update.effective_chat.id
        if self._update_group(chat_id, {"goodbye_text": text}):
            await message.reply_text(f"✅ Goodbye message set to:\n{text}")
            await self._send_log(
                chat_id,
                context,
                f"✍️ <b>Goodbye message updated</b> by {update.effective_user.mention_html()}.",
            )
        else:
            await message.reply_text("⚠️ Failed to update goodbye message. Try again later.")

    async def toggle_welcome(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None or not await self._ensure_admin(update):
            return
        status = self._parse_toggle(context.args)
        if status is None:
            await message.reply_text("Usage: /welcome on|off")
            return
        chat_id = update.effective_chat.id
        if self._update_group(chat_id, {"welcome_on": status}):
            state = "enabled" if status else "disabled"
            await message.reply_text(f"✅ Welcome messages {state}.")
            await self._send_log(
                chat_id,
                context,
                f"⚙️ <b>Welcome messages {state}</b> by {update.effective_user.mention_html()}.",
            )
        else:
            await message.reply_text("⚠️ Unable to update welcome setting right now.")

    async def toggle_goodbye(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None or not await self._ensure_admin(update):
            return
        status = self._parse_toggle(context.args)
        if status is None:
            await message.reply_text("Usage: /goodbye on|off")
            return
        chat_id = update.effective_chat.id
        if self._update_group(chat_id, {"goodbye_on": status}):
            state = "enabled" if status else "disabled"
            await message.reply_text(f"✅ Goodbye messages {state}.")
            await self._send_log(
                chat_id,
                context,
                f"⚙️ <b>Goodbye messages {state}</b> by {update.effective_user.mention_html()}.",
            )
        else:
            await message.reply_text("⚠️ Unable to update goodbye setting right now.")

    async def set_flood_limit(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None or not await self._ensure_admin(update):
            return
        if not context.args:
            await message.reply_text("Usage: /setflood <number>")
            return
        try:
            limit = int(context.args[0])
        except ValueError:
            await message.reply_text("Flood limit must be a positive integer.")
            return
        if limit <= 0:
            await message.reply_text("Flood limit must be a positive integer.")
            return
        chat_id = update.effective_chat.id
        if self._update_group(chat_id, {"flood_limit": limit}):
            await message.reply_text(
                f"✅ Flood protection set to {limit} messages per {FLOOD_WINDOW_SECONDS}s."
            )
            await self._send_log(
                chat_id,
                context,
                f"🛡️ <b>Flood limit set to {limit}</b> by {update.effective_user.mention_html()}.",
            )
        else:
            await message.reply_text("⚠️ Failed to update flood limit. Try again later.")

    async def add_filter(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None or not await self._ensure_admin(update):
            return
        if len(context.args) < 2:
            await message.reply_text("Usage: /addfilter <word> <reply>")
            return
        trigger = context.args[0].strip().lower()
        reply_text = " ".join(context.args[1:]).strip()
        if not trigger or not reply_text:
            await message.reply_text("Filter trigger and reply cannot be empty.")
            return
        chat_id = update.effective_chat.id
        if self._set_filter(chat_id, trigger, reply_text):
            await message.reply_text(f"✅ Filter for '{trigger}' added.")
            await self._send_log(
                chat_id,
                context,
                f"🛡️ <b>Filter added</b> for <code>{trigger}</code> by {update.effective_user.mention_html()}.",
            )
        else:
            await message.reply_text("⚠️ Failed to store filter. Try again later.")

    async def delete_filter(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None or not await self._ensure_admin(update):
            return
        if not context.args:
            await message.reply_text("Usage: /delfilter <word>")
            return
        trigger = context.args[0].strip().lower()
        chat_id = update.effective_chat.id
        filters_map = self._get_filters(chat_id)
        if trigger not in filters_map:
            await message.reply_text("❓ That filter does not exist.")
            return
        if self._remove_filter(chat_id, trigger):
            await message.reply_text(f"✅ Filter '{trigger}' removed.")
            await self._send_log(
                chat_id,
                context,
                f"🛡️ <b>Filter removed</b> for <code>{trigger}</code> by {update.effective_user.mention_html()}.",
            )
        else:
            await message.reply_text("⚠️ Failed to delete filter. Try again later.")

    async def list_filters(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if not message:
            return
        filters_map = self._get_filters(update.effective_chat.id)
        if not filters_map:
            await message.reply_text("No filters configured.")
            return
        lines = [f"• <code>{word}</code> → {reply}" for word, reply in sorted(filters_map.items())]
        await message.reply_text(
            "🛡️ Active filters:\n" + "\n".join(lines),
            parse_mode=ParseMode.HTML,
        )

    async def set_log_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None or not await self._ensure_admin(update):
            return
        if not context.args:
            await message.reply_text("Usage: /setlog <chat_id>")
            return
        try:
            log_chat = int(context.args[0])
        except ValueError:
            await message.reply_text("Log chat must be a numeric chat ID.")
            return
        chat_id = update.effective_chat.id
        if self._update_group(chat_id, {"log_channel": log_chat}):
            await message.reply_text(f"✅ Logs will be sent to {log_chat}.")
            await self._send_log(
                chat_id,
                context,
                f"📝 <b>Log channel set to</b> <code>{log_chat}</code> by {update.effective_user.mention_html()}.",
            )
        else:
            await message.reply_text("⚠️ Unable to update log channel right now.")

    async def unset_log_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None or not await self._ensure_admin(update):
            return
        chat_id = update.effective_chat.id
        if self._delete_group_value(chat_id, "log_channel"):
            await message.reply_text("✅ Log channel removed.")
            await self._send_log(
                chat_id,
                context,
                f"📝 <b>Log channel removed</b> by {update.effective_user.mention_html()}.",
            )
        else:
            await message.reply_text("⚠️ Failed to remove log channel. Try again later.")

    async def log_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if not message:
            return
        log_chat = self._get_log_channel(update.effective_chat.id)
        if log_chat is None:
            await message.reply_text("ℹ️ No log channel configured.")
        else:
            await message.reply_text(
                f"📝 Logs are being sent to <code>{log_chat}</code>.",
                parse_mode=ParseMode.HTML,
            )

    def _target_from_reply(self, update: Update) -> Optional[User]:
        message = update.effective_message
        if not message or not message.reply_to_message:
            return None
        return message.reply_to_message.from_user

    async def _require_target(self, update: Update, action: str) -> Optional[User]:
        message = update.effective_message
        if not message:
            return None
        target = self._target_from_reply(update)
        if target is None:
            await message.reply_text(f"Reply to a user to {action} them.")
            return None
        if update.effective_user and target.id == update.effective_user.id:
            await message.reply_text("You cannot perform this action on yourself.")
            return None
        return target

    async def ban(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None or not await self._ensure_admin(update):
            return
        target = await self._require_target(update, "ban")
        if target is None:
            return
        self._set_ban_state(chat.id, target.id, True)
        try:
            await chat.ban_member(target.id)
        except TelegramError as exc:
            LOGGER.warning("Failed to ban user %s: %s", target.id, exc)
            await message.reply_text("⚠️ Unable to ban the user. Check bot permissions.")
            return
        await message.reply_text(
            f"🚫 {target.mention_html()} banned.", parse_mode=ParseMode.HTML
        )
        await self._send_log(
            chat.id,
            context,
            f"⛔️ <b>Banned</b> {target.mention_html()} by {update.effective_user.mention_html()}.",
        )

    async def unban(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None or not await self._ensure_admin(update):
            return
        target = await self._require_target(update, "unban")
        if target is None:
            return
        self._set_ban_state(chat.id, target.id, False)
        try:
            await chat.unban_member(target.id)
        except TelegramError as exc:
            LOGGER.warning("Failed to unban user %s: %s", target.id, exc)
            await message.reply_text("⚠️ Unable to unban the user. Check bot permissions.")
            return
        await message.reply_text(
            f"✅ {target.mention_html()} unbanned.", parse_mode=ParseMode.HTML
        )
        await self._send_log(
            chat.id,
            context,
            f"♻️ <b>Unbanned</b> {target.mention_html()} by {update.effective_user.mention_html()}.",
        )

    async def kick(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None or not await self._ensure_admin(update):
            return
        target = await self._require_target(update, "kick")
        if target is None:
            return
        try:
            await chat.ban_member(target.id)
            await chat.unban_member(target.id)
        except TelegramError as exc:
            LOGGER.warning("Failed to kick user %s: %s", target.id, exc)
            await message.reply_text("⚠️ Unable to kick the user. Check bot permissions.")
            return
        await message.reply_text(
            f"👢 {target.mention_html()} kicked.", parse_mode=ParseMode.HTML
        )
        await self._send_log(
            chat.id,
            context,
            f"👢 <b>Kicked</b> {target.mention_html()} by {update.effective_user.mention_html()}.",
        )

    async def mute(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None or not await self._ensure_admin(update):
            return
        target = await self._require_target(update, "mute")
        if target is None:
            return
        try:
            await chat.restrict_member(target.id, ChatPermissions(can_send_messages=False))
        except TelegramError as exc:
            LOGGER.warning("Failed to mute user %s: %s", target.id, exc)
            await message.reply_text("⚠️ Unable to mute the user. Check bot permissions.")
            return
        await message.reply_text(
            f"🔇 {target.mention_html()} muted.", parse_mode=ParseMode.HTML
        )
        await self._send_log(
            chat.id,
            context,
            f"🔇 <b>Muted</b> {target.mention_html()} by {update.effective_user.mention_html()}.",
        )

    async def unmute(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None or not await self._ensure_admin(update):
            return
        target = await self._require_target(update, "unmute")
        if target is None:
            return
        try:
            await chat.restrict_member(
                target.id,
                ChatPermissions(
                    can_send_messages=True,
                    can_send_media_messages=True,
                    can_send_other_messages=True,
                    can_send_polls=True,
                    can_add_web_page_previews=True,
                ),
            )
        except TelegramError as exc:
            LOGGER.warning("Failed to unmute user %s: %s", target.id, exc)
            await message.reply_text("⚠️ Unable to unmute the user. Check bot permissions.")
            return
        await message.reply_text(
            f"🔊 {target.mention_html()} unmuted.", parse_mode=ParseMode.HTML
        )
        await self._send_log(
            chat.id,
            context,
            f"🔊 <b>Unmuted</b> {target.mention_html()} by {update.effective_user.mention_html()}.",
        )

    async def promote(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None or not await self._ensure_admin(update):
            return
        target = await self._require_target(update, "promote")
        if target is None:
            return
        try:
            await context.bot.promote_chat_member(
                chat_id=chat.id,
                user_id=target.id,
                can_manage_chat=True,
                can_delete_messages=True,
                can_manage_video_chats=True,
                can_restrict_members=True,
                can_invite_users=True,
                can_pin_messages=True,
            )
        except TelegramError as exc:
            LOGGER.warning("Failed to promote user %s: %s", target.id, exc)
            await message.reply_text("⚠️ Unable to promote the user. Check bot permissions.")
            return
        await message.reply_text(
            f"⭐ {target.mention_html()} promoted.", parse_mode=ParseMode.HTML
        )
        await self._send_log(
            chat.id,
            context,
            f"⭐ <b>Promoted</b> {target.mention_html()} by {update.effective_user.mention_html()}.",
        )

    async def demote(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None or not await self._ensure_admin(update):
            return
        target = await self._require_target(update, "demote")
        if target is None:
            return
        try:
            await context.bot.promote_chat_member(
                chat_id=chat.id,
                user_id=target.id,
                can_manage_chat=False,
                can_delete_messages=False,
                can_manage_video_chats=False,
                can_restrict_members=False,
                can_invite_users=False,
                can_pin_messages=False,
                can_promote_members=False,
            )
        except TelegramError as exc:
            LOGGER.warning("Failed to demote user %s: %s", target.id, exc)
            await message.reply_text("⚠️ Unable to demote the user. Check bot permissions.")
            return
        await message.reply_text(
            f"⬇️ {target.mention_html()} demoted.", parse_mode=ParseMode.HTML
        )
        await self._send_log(
            chat.id,
            context,
            f"⬇️ <b>Demoted</b> {target.mention_html()} by {update.effective_user.mention_html()}.",
        )

    async def history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if not message:
            return
        if context.args:
            username = context.args[0].lstrip("@")
            match = self._find_history_by_username(username)
            if not match or not match.entries:
                await message.reply_text("No history recorded for that user.")
                return
            await message.reply_text(
                "History for {username}:\n".format(username=username) + "\n".join(match.entries)
            )
            return
        user = update.effective_user
        if not user:
            await message.reply_text("Unable to determine the requesting user.")
            return
        entries = self._get_name_history(user.id)
        if not entries:
            await message.reply_text("No name history recorded yet.")
            return
        await message.reply_text("Your name history:\n" + "\n".join(entries))

    # ------------------------------------------------------------------
    # Service message handlers
    # ------------------------------------------------------------------
    async def welcome_new_members(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if not message or not chat:
            return
        settings = self._get_group_settings(chat.id)
        if not settings.get("welcome_on", True):
            return
        template = settings.get("welcome_text") or DEFAULT_WELCOME
        for member in message.new_chat_members:
            if member.is_bot:
                continue
            text = self._format_name(template, member)
            await message.reply_text(text)
            await self._send_log(
                chat.id,
                context,
                f"👋 <b>Welcome message sent</b> to {member.mention_html()}.",
            )

    async def member_left(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if not message or not chat or not message.left_chat_member:
            return
        settings = self._get_group_settings(chat.id)
        if not settings.get("goodbye_on", True):
            return
        member = message.left_chat_member
        if member.is_bot:
            return
        template = settings.get("goodbye_text") or DEFAULT_GOODBYE
        text = self._format_name(template, member)
        await message.reply_text(text)
        await self._send_log(
            chat.id,
            context,
            f"👋 <b>Goodbye message sent</b> for {member.mention_html()}.",
        )

    # ------------------------------------------------------------------
    # Message moderation
    # ------------------------------------------------------------------
    async def check_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if (
            not message
            or not chat
            or not user
            or user.is_bot
            or message.new_chat_members
            or message.left_chat_member
        ):
            return

        self._append_name_history(user)

        if self._is_banned(chat.id, user.id):
            try:
                await chat.ban_member(user.id)
            except TelegramError as exc:
                LOGGER.warning("Failed to auto-ban user %s: %s", user.id, exc)
            else:
                await self._send_log(
                    chat.id,
                    context,
                    f"⛔️ <b>Auto-ban enforced</b> on {user.mention_html()}.",
                )
            return

        now = time.monotonic()
        flood_count = self.flood_tracker.increment(chat.id, user.id, now)
        settings = self._get_group_settings(chat.id)
        limit_raw = settings.get("flood_limit") if isinstance(settings, dict) else None
        try:
            flood_limit = int(limit_raw)
        except (TypeError, ValueError):
            flood_limit = DEFAULT_FLOOD_LIMIT
        if flood_limit <= 0:
            flood_limit = DEFAULT_FLOOD_LIMIT
        if flood_count > flood_limit:
            try:
                await chat.restrict_member(user.id, ChatPermissions(can_send_messages=False))
            except TelegramError as exc:
                LOGGER.warning("Failed to mute flooder %s: %s", user.id, exc)
            else:
                await message.reply_text(
                    f"🚨 {user.mention_html()} muted for flooding.",
                    parse_mode=ParseMode.HTML,
                )
                await self._send_log(
                    chat.id,
                    context,
                    f"🚨 <b>Flood mute applied</b> to {user.mention_html()}.",
                )
            return

        filters_map = settings.get("filters") if isinstance(settings, dict) else {}
        if not isinstance(filters_map, dict) or not filters_map:
            filters_map = self._get_filters(chat.id)
        if not filters_map:
            return
        text = message.text or message.caption or ""
        lowered = text.lower()
        for trigger, reply_text in filters_map.items():
            if not isinstance(trigger, str) or not isinstance(reply_text, str):
                continue
            if trigger.lower() and trigger.lower() in lowered:
                await message.reply_text(reply_text)
                await self._send_log(
                    chat.id,
                    context,
                    f"🛡️ <b>Filter triggered</b> (<code>{trigger}</code>) by {user.mention_html()}.",
                )
                break

    # ------------------------------------------------------------------
    # Entrypoint
    # ------------------------------------------------------------------
    def run(self) -> None:
        LOGGER.info("Starting BreakTheICX bot")
        self.application.run_polling()


if __name__ == "__main__":
    bot = BreakTheICXBot()
    bot.run()
