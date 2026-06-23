"""
All telegram-bot command and message handlers.

Permissions:
  admin      â†’ everything + Admin Panel
  subscriber â†’ full menu + start/stop broadcasting
  guest      â†’ full menu, setup only â€” start/stop blocked
  banned     â†’ all blocked
"""

import html
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from telegram.constants import ParseMode

import config
import database as db
import account_manager as am
import broadcaster

from loguru import logger

# â”€â”€â”€ Conversation states â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
ADD_CHOOSE_METHOD, ADD_PHONE, ADD_OTP, ADD_PASSWORD, ADD_SESSION_STRING = range(5)
AWAITING_MSG      = 20
SUB_CHOOSE_PLAN   = 30
SUB_AWAIT_RECEIPT = 31


# â”€â”€â”€ Dynamic plan helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _get_plan(key: str) -> dict:
    """Return plan data, merging DB overrides on top of config defaults."""
    base = dict(config.PLANS.get(key, {}))
    if not base:
        return {}
    name_  = await db.get_setting(f"plan_{key}_name",     "")
    price_ = await db.get_setting(f"plan_{key}_price",    "")
    accts_ = await db.get_setting(f"plan_{key}_accounts", "")
    days_  = await db.get_setting(f"plan_{key}_days",     "")
    if name_:  base["name"] = name_
    if price_: base["price"] = int(price_)
    if accts_: base["account_limit"] = int(accts_)
    if days_:  base["duration_days"] = int(days_)
    base["label"] = f"{base['name']} â€” â‚¹{base['price']}/week ({base['account_limit']} Acc)"
    return base


async def _get_all_plans() -> dict[str, dict]:
    return {key: await _get_plan(key) for key in config.PLANS}


# â”€â”€â”€ Decorators â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def admin_only_deco(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.id not in config.ADMIN_IDS:
            await update.effective_message.reply_text("âŒ Admin only.")  # type: ignore[union-attr]
            return ConversationHandler.END
        return await func(update, ctx)
    wrapper.__name__ = func.__name__
    return wrapper


# â”€â”€â”€ Permission helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _level(user_id: int) -> str:
    """Return 'admin', 'subscriber', 'guest', or 'banned'."""
    if user_id in config.ADMIN_IDS:
        return "admin"
    if await db.is_banned(user_id):
        return "banned"
    if await db.get_user_subscription(user_id):
        return "subscriber"
    return "guest"


async def _maintenance_block(update: Update) -> bool:
    """Return True (and reply) if maintenance mode is ON and user is not admin."""
    user = update.effective_user
    if user and user.id in config.ADMIN_IDS:
        return False
    mode = await db.get_setting("maintenance_mode", "0")
    if mode == "1":
        await update.effective_message.reply_text(  # type: ignore[union-attr]
            "ðŸ”§ <b>Bot is under maintenance.</b>\nPlease check back soon!",
            parse_mode=ParseMode.HTML,
        )
        return True
    return False


# â”€â”€â”€ Shared back-button helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _back_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅ Back", callback_data="menu:back_main"),
        InlineKeyboardButton("🏠 Home", callback_data="menu:back_main"),
    ]])


def _back_ads_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅ Back", callback_data="menu:ads_manager"),
        InlineKeyboardButton("🏠 Home", callback_data="menu:back_main"),
    ]])


# â”€â”€â”€ In-place edit helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _safe_edit(query, text: str, parse_mode=None, reply_markup=None) -> None:
    """
    Edit the message that contains the tapped button.
    Falls back to a new reply message only if editing is impossible
    (e.g. message too old, already deleted, or contains a photo).
    """
    try:
        await query.edit_message_text(
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
    except Exception as exc:
        err = str(exc).lower()
        if "not modified" in err:
            return  # Content identical â€” silently ignore
        # Message is a photo/sticker, already deleted, or token expired â†’ new message
        try:
            await query.message.reply_text(
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
        except Exception as exc2:
            logger.warning("_safe_edit fallback also failed: {}", exc2)


# â”€â”€â”€ Keyboards â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _main_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🚀 Broadcast Manager", callback_data="menu:broadcast_manager"),
            InlineKeyboardButton("👤 Accounts", callback_data="menu:accounts"),
        ],
        [
            InlineKeyboardButton("💬 Messages", callback_data="menu:ads_manager"),
            InlineKeyboardButton("🤖 Auto Reply", callback_data="menu:autoreply"),
        ],
        [
            InlineKeyboardButton("📊 Dashboard", callback_data="menu:stats"),
            InlineKeyboardButton("⚙ Settings", callback_data="menu:settings"),
        ],
        [
            InlineKeyboardButton("💳 Subscription", callback_data="menu:subscribe"),
            InlineKeyboardButton("❓ Help", callback_data="menu:howto"),
        ],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("ðŸ”§ Admin Panel", callback_data="admin:main")])
    return InlineKeyboardMarkup(rows)


def _admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("â³ Pending Orders",   callback_data="admin:pending"),
            InlineKeyboardButton("ðŸ’Ž Active Subs",       callback_data="admin:active_subs"),
        ],
        [
            InlineKeyboardButton("ðŸ“Š Full Stats",        callback_data="admin:stats"),
            InlineKeyboardButton("ðŸ‘¥ All Users",          callback_data="admin:users"),
        ],
        [InlineKeyboardButton("ðŸ“¦ Package Manager",      callback_data="admin_pkg:list")],
        [InlineKeyboardButton("ðŸ“£ Broadcast to Users",   callback_data="admin:announce")],
        [
            InlineKeyboardButton("ðŸš« Ban User",          callback_data="admin:ban"),
            InlineKeyboardButton("âœ… Unban User",         callback_data="admin:unban"),
        ],
        [InlineKeyboardButton("âš™ï¸ Settings",             callback_data="admin:settings")],
        [InlineKeyboardButton("ðŸ”™ Back to Main",         callback_data="admin:back")],
    ])


def _settings_keyboard(maintenance: str, upi_enabled: str) -> InlineKeyboardMarkup:
    maint_label = "ðŸ”´ Turn Maintenance OFF" if maintenance == "1" else "ðŸŸ¢ Turn Maintenance ON"
    upi_label   = "ðŸ”´ Disable UPI Payments" if upi_enabled  == "1" else "ðŸŸ¢ Enable UPI Payments"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(maint_label,                  callback_data="admsettings:toggle_maintenance")],
        [InlineKeyboardButton(upi_label,                    callback_data="admsettings:toggle_upi")],
        [InlineKeyboardButton("ðŸ’³ Change UPI ID",           callback_data="admsettings:change_upi")],
        [InlineKeyboardButton("ðŸ“· Upload Payment QR Code",  callback_data="admsettings:upload_qr")],
        [InlineKeyboardButton("ðŸ‘¤ Change Support Contact",  callback_data="admsettings:change_support")],
        [InlineKeyboardButton("ðŸ“ Change Welcome Message",  callback_data="admsettings:change_welcome")],
        [InlineKeyboardButton("ðŸ—‘ Clear Welcome Message",   callback_data="admsettings:clear_welcome")],
        [InlineKeyboardButton("ðŸ”™ Back",                    callback_data="admin:main")],
    ])


# â”€â”€â”€ /start â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    await db.upsert_user(user.id, f"@{user.username}" if user.username else None, full_name)

    if await _maintenance_block(update):
        return

    if await db.is_banned(user.id):
        await update.message.reply_text("ðŸš« You are banned from using this bot.")  # type: ignore[union-attr]
        return

    await _send_main_menu(update.message, user.id)  # type: ignore[union-attr]


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "/start - Open Bot\n"
        "/help - Show Help\n"
        "/startbroadcast - Start Broadcasting\n"
        "/stopbroadcast - Stop Broadcasting\n"
        "/addaccount - Add Account\n"
        "/removeaccount - Remove Account\n"
        "/accounts - View Accounts\n"
        "/addmessage - Add Message\n"
        "/messages - View Messages\n"
        "/autoreply - Auto Reply Settings\n"
        "/dashboard - Dashboard\n"
        "/settings - Settings"
    )
    await update.message.reply_text(text, reply_markup=_back_main_kb())  # type: ignore[union-attr]


async def _send_main_menu(target, user_id: int, query=None) -> None:
    """Build and send (or edit) the main menu.

    Pass ``query`` when called from a callback so the existing message is
    edited in-place instead of posting a new one.
    """
    lvl        = await _level(user_id)
    is_admin   = lvl == "admin"
    is_sub     = lvl in ("admin", "subscriber")
    accounts   = await db.get_accounts(user_id)
    messages   = await db.get_messages(user_id)
    preset_key = await db.get_user_broadcast_setting(user_id, "speed_preset", "medium")
    preset     = config.SPEED_PRESETS.get(preset_key or "medium", config.SPEED_PRESETS["medium"])
    bc_status  = "▶ Running" if broadcaster.is_running(user_id) else "⏹ Stopped"

    welcome_msg = await db.get_setting("welcome_msg", "")
    user_info   = await db.get_user(user_id) if hasattr(db, "get_user") else {}
    full_name   = (user_info or {}).get("full_name", "")

    if welcome_msg:
        header = welcome_msg
    elif is_admin:
        header = "ðŸ›  <b>Telegram Broadcast Manager</b>"
    elif is_sub:
        sub = await db.get_user_subscription(user_id)
        exp = sub.get("expires_at", "")[:10] if sub else ""
        header = (
            f"ðŸ‘‹ <b>Welcome back, {html.escape(full_name)}!</b>\n"
            f"âœ… Active Plan: {sub['plan_name']} â€” expires {exp}"
        ) if sub else f"ðŸ‘‹ <b>Welcome!</b>"
    else:
        header = (
            "ðŸ‘‹ <b>Welcome!</b>\n\n"
            "ðŸ’Ž Subscribe to unlock <b>Start/Stop Broadcasting</b>.\n"
            "All other features are free to use!"
        )

    text = (
        f"{header}\n\n"
        f"ðŸ‘¥ Accounts: <b>{len(accounts)}</b>\n"
        f"ðŸ’¬ Messages: <b>{len(messages)}</b> in pool\n"
        f"âš¡ Speed: <b>{preset['label']} {preset['risk']}</b>\n"
        f"ðŸ“¡ Broadcasting: <b>{bc_status}</b>"
    )
    kb = _main_keyboard(is_admin)
    if query:
        await _safe_edit(query, text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await target.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


# â”€â”€â”€ Main menu router â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def cb_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # type: ignore[union-attr]
    user   = update.effective_user
    if not user:
        return

    if await db.is_banned(user.id):
        await query.answer("ðŸš« You are banned.", show_alert=True)
        return

    if await _maintenance_block(update):
        return

    action = query.data.split(":", 1)[1]  # type: ignore[union-attr]
    lvl    = await _level(user.id)
    is_admin = lvl == "admin"

    # â”€â”€ Back to main menu â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    if action == "back_main":
        await _send_main_menu(query.message, user.id, query=query)  # type: ignore[union-attr]
        return

    # â”€â”€ Actions open to everyone â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    if action == "howto":
        await _safe_edit(
            query,
            "ðŸ“– <b>How to Use</b>\n\n"
            "1ï¸âƒ£ <b>Add Accounts</b> â€” /add or tap ðŸ‘¤ Add Accounts\n"
            "2ï¸âƒ£ Make sure those accounts <b>are already in the groups</b> you want to reach\n"
            "3ï¸âƒ£ <b>Add Messages</b> â€” tap ðŸ“¢ Ads Manager â†’ Add Message (multiple = random per group)\n"
            "4ï¸âƒ£ <b>Select Accounts</b> â€” choose which ones broadcast\n"
            "5ï¸âƒ£ <b>Pick Speed</b> â€” âš¡ SuperFast â†’ ðŸ¢ Slow â†’ ðŸŽ›ï¸ Custom\n"
            "6ï¸âƒ£ <b>Start!</b> â€” tap â–¶ï¸ Start Broadcast\n\n"
            "ðŸ“Œ Groups shuffled every cycle\n"
            "ðŸ“Œ Different message per group (random)\n"
            "ðŸ“Œ Dead accounts auto-marked ðŸ’€\n"
            "ðŸ“Œ Cycle report sent to you after every round\n\n"
            "<b>Subscription:</b>\n"
            "â­ Basic â‚¹49/week â€” 5 accounts\n"
            "ðŸ’Ž Pro â‚¹99/week â€” 10 accounts",
            parse_mode=ParseMode.HTML,
            reply_markup=_back_main_kb(),
        )
        return

    if action == "support":
        support = await db.get_setting("support_contact", "@AKKI_THE_BOSS")
        await _safe_edit(
            query,
            "ðŸ’¬ <b>Support</b>\n\n"
            "Having trouble? Contact the admin directly:\n"
            f"ðŸ‘¤ {html.escape(support)}\n\n"
            "Payment issue? Use ðŸ’Ž Subscriptions â†’ send your screenshot again.",
            parse_mode=ParseMode.HTML,
            reply_markup=_back_main_kb(),
        )
        return

    if action == "broadcast_manager":
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("▶ Start", callback_data="menu:startauto"),
                InlineKeyboardButton("⏹ Stop", callback_data="menu:stopauto"),
            ],
            [
                InlineKeyboardButton("⚡ Speed", callback_data="menu:speed"),
                InlineKeyboardButton("👥 Select Accounts", callback_data="menu:select_accounts"),
            ],
            [
                InlineKeyboardButton("⬅ Back", callback_data="menu:back_main"),
                InlineKeyboardButton("🏠 Home", callback_data="menu:back_main"),
            ],
        ])
        await _safe_edit(query, "🚀 <b>Broadcast Manager</b>", parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if action == "accounts":
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("➕ Add", callback_data="menu:add_accounts"),
                InlineKeyboardButton("📋 View", callback_data="menu:list_accounts"),
            ],
            [
                InlineKeyboardButton("🗑 Remove", callback_data="menu:remove_accounts"),
                InlineKeyboardButton("🟢 Status", callback_data="menu:maintain"),
            ],
            [
                InlineKeyboardButton("⬅ Back", callback_data="menu:back_main"),
                InlineKeyboardButton("🏠 Home", callback_data="menu:back_main"),
            ],
        ])
        await _safe_edit(query, "👤 <b>Accounts</b>", parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if action == "settings":
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⚡ Speed", callback_data="menu:speed"),
                InlineKeyboardButton("💬 Support", callback_data="menu:support"),
            ],
            [
                InlineKeyboardButton("⬅ Back", callback_data="menu:back_main"),
                InlineKeyboardButton("🏠 Home", callback_data="menu:back_main"),
            ],
        ])
        await _safe_edit(query, "⚙ <b>Settings</b>", parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if action == "autoreply":
        await _safe_edit(
            query,
            "🤖 <b>Auto Reply</b>\n\nAuto Reply settings will stay here when enabled.",
            parse_mode=ParseMode.HTML,
            reply_markup=_back_main_kb(),
        )
        return

    if action == "list_accounts":
        accounts = await db.get_accounts(user.id)
        if not accounts:
            await _safe_edit(query, "📋 No accounts added yet.", reply_markup=_back_main_kb())
            return
        lines = ["📋 <b>Accounts</b>\n"]
        for i, acc in enumerate(accounts, 1):
            icon = {"active": "🟢", "offline": "🔴", "dead": "💀"}.get(acc["status"], "⚪")
            lines.append(f"{i}. <b>{html.escape(acc['name'])}</b>\n   {html.escape(acc['phone'])} {icon} {acc['status'].capitalize()}")
        await _safe_edit(query, "\n\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=_back_main_kb())
        return

    if action == "remove_accounts":
        accounts = await db.get_accounts(user.id)
        if not accounts:
            await _safe_edit(query, "No accounts to remove.", reply_markup=_back_main_kb())
            return
        keyboard = [[InlineKeyboardButton(
            f"{'🟢' if a['status']=='active' else '🔴'} {a['name']} - {a['phone']}",
            callback_data=f"rm:{a['id']}",
        )] for a in accounts]
        keyboard.append([
            InlineKeyboardButton("⬅ Back", callback_data="menu:accounts"),
            InlineKeyboardButton("🏠 Home", callback_data="menu:back_main"),
        ])
        await _safe_edit(query, "🗑 Select account to remove:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if action == "stats":
        accounts = await db.get_accounts(user.id)
        messages = await db.get_messages(user.id)
        s        = broadcaster.get_stats(user.id)
        preset_key = await db.get_user_broadcast_setting(user.id, "speed_preset", "medium")
        preset   = config.SPEED_PRESETS.get(preset_key or "medium", config.SPEED_PRESETS["medium"])
        await _safe_edit(
            query,
            f"ðŸ“Š <b>Your Stats</b>\n\n"
            f"ðŸ“¡ Broadcasting: {'▶ Running' if broadcaster.is_running(user.id) else '⏹ Stopped'}\n"
            f"ðŸ” Cycles: <b>{s['cycles']}</b>\n"
            f"âœ… Sent: <b>{s['sent']}</b>\n"
            f"âŒ Failed: <b>{s['failed']}</b>\n"
            f"ðŸ’€ Dead accounts: <b>{s['dead_accounts']}</b>\n"
            f"ðŸ‘¥ Accounts: <b>{len(accounts)}</b>\n"
            f"ðŸ’¬ Messages: <b>{len(messages)}</b>\n"
            f"âš¡ Speed: {preset['label']} {preset['risk']}",
            parse_mode=ParseMode.HTML,
            reply_markup=_back_main_kb(),
        )
        return

    if action == "subscribe":
        await _show_plans(query.message, query=query)
        return

    if action == "add_accounts":
        await _safe_edit(
            query,
            "ðŸ‘¤ <b>Add Account</b>\n\nUse /add to log in a Telegram account via Phone+OTP or Session String.",
            parse_mode=ParseMode.HTML,
            reply_markup=_back_main_kb(),
        )
        return

    if action == "ads_manager":
        messages = await db.get_messages(user.id)
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("âž• Add Message",   callback_data="menu:do_addmsg"),
                InlineKeyboardButton("ðŸ“‹ View Messages", callback_data="menu:do_msgs"),
            ],
            [InlineKeyboardButton("ðŸ”™ Back", callback_data="menu:back_main")],
        ])
        await _safe_edit(
            query,
            f"ðŸ“¢ <b>Ads Manager</b>\n\nMessages in pool: <b>{len(messages)}</b>\n"
            "Random message sent to each group per send.",
            parse_mode=ParseMode.HTML, reply_markup=kb,
        )
        return

    if action == "do_addmsg":
        ctx.user_data["awaiting_addmsg"] = True  # type: ignore[index]
        # Must send new message â€” bot awaits a text reply
        await query.message.reply_text(  # type: ignore[union-attr]
            "ðŸ’¬ Send your broadcast message text (HTML/Markdown/plain/emojis).\n/cancel to abort."
        )
        return

    if action == "do_msgs":
        await _show_msgs_menu(query.message, user.id, query=query)
        return

    if action == "maintain":
        accounts = await db.get_accounts(user.id)
        active  = [a for a in accounts if a["status"] == "active"]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("ðŸ”„ Reconnect All", callback_data="menu:reconnect_all")],
            [InlineKeyboardButton("ðŸ”™ Back",           callback_data="menu:back_main")],
        ])
        lines = ["ðŸŸ¢ <b>Account Status</b>\n"]
        for acc in accounts:
            icon = {"active": "ðŸŸ¢", "offline": "ðŸ”´", "dead": "ðŸ’€"}.get(acc["status"], "âšª")
            lines.append(f"{icon} <b>{html.escape(acc['name'])}</b> â€” {html.escape(acc['phone'])}")
        lines.append(f"\nâœ… Active: {len(active)} | âš ï¸ Other: {len(accounts)-len(active)}")
        await _safe_edit(
            query,
            "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb,
        )
        return

    if action == "reconnect_all":
        await am.load_all_clients(user.id)
        await _safe_edit(
            query,
            "ðŸ”„ Reconnect attempted for all accounts.",
            reply_markup=_back_main_kb(),
        )
        return

    if action == "speed":
        await _show_speed_menu(query.message, user.id, query=query)
        return

    if action == "select_accounts":
        await _show_select_accounts(query.message, user.id, query=query)
        return

    # â”€â”€ Start / Stop â€” requires subscription â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    if action in ("startauto", "stopauto"):
        if lvl == "guest":
            await query.answer(
                "ðŸ’Ž Subscribe to unlock Start/Stop Broadcasting!\nTap ðŸ’Ž Subscriptions to get started.",
                show_alert=True,
            )
            return

        if action == "startauto":
            if broadcaster.is_running(user.id):
                await query.answer("ðŸ“¡ Already running.", show_alert=True)
                return
            messages = await db.get_messages(user.id)
            if not messages:
                await _safe_edit(query, "âŒ No messages in pool. Tap ðŸ“¢ Ads Manager â†’ Add Message.", reply_markup=_back_main_kb())
                return
            accounts = await db.get_accounts(user.id)
            if not accounts:
                await _safe_edit(query, "âŒ No accounts. Tap ðŸ‘¤ Add Accounts.", reply_markup=_back_main_kb())
                return
            preset_key = await db.get_user_broadcast_setting(user.id, "speed_preset", "medium")
            preset = config.SPEED_PRESETS.get(preset_key or "medium", config.SPEED_PRESETS["medium"])
            
            await broadcaster.start_broadcast(user.id)
            await _safe_edit(
                query,
                f"â–¶ï¸ <b>Broadcasting started!</b>\n\n"
                f"âš¡ {preset['label']} {preset['risk']}\n"
                f"ðŸ’¬ {len(messages)} messages | ðŸ‘¥ {len(accounts)} accounts\n\n"
                "ðŸ”¸ Groups auto-fetched & shuffled\n"
                "ðŸ”¸ Random message per group\n"
                "ðŸ”¸ Dead accounts auto-detected ðŸ’€\n"
                "ðŸ”¸ Cycle report sent after every round",
                parse_mode=ParseMode.HTML,
                reply_markup=_back_main_kb(),
            )
        else:
            stopped = await broadcaster.stop_broadcast(user.id)
            s = broadcaster.get_stats(user.id)
            if stopped:
                await _safe_edit(
                    query,
                    f"â¹ <b>Stopped.</b>\n\nCycles {s['cycles']} | âœ… {s['sent']} | âŒ {s['failed']} | ðŸ’€ {s['dead_accounts']}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=_back_main_kb(),
                )
            else:
                await query.answer("Not running.", show_alert=True)
        return


# â”€â”€â”€ Admin Panel router â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def cb_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # type: ignore[union-attr]
    user  = update.effective_user
    if not user or user.id not in config.ADMIN_IDS:
        await query.answer("âŒ Admins only.", show_alert=True)
        return

    action = query.data.split(":", 1)[1]  # type: ignore[union-attr]

    # â”€â”€ Main admin panel â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if action in ("main", "back"):
        counts   = await db.get_sub_counts()
        users    = await db.get_user_count()
        maint    = await db.get_setting("maintenance_mode", "0")
        upi_on   = await db.get_setting("upi_enabled", "1")
        await _safe_edit(
            query,
            f"ðŸ”§ <b>Admin Panel</b>\n\n"
            f"Orders â€” Total: {counts['total']} | Pending: {counts['pending']} | "
            f"Approved: {counts['approved']} | Rejected: {counts['rejected']}\n"
            f"Users: {users}\n"
            f"Maintenance: {'ON ðŸ”´' if maint == '1' else 'OFF ðŸŸ¢'}\n"
            f"UPI: {'ON ðŸŸ¢' if upi_on == '1' else 'OFF ðŸ”´'}",
            parse_mode=ParseMode.HTML,
            reply_markup=_admin_panel_keyboard(),
        )
        return

    # â”€â”€ Pending orders â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if action == "pending":
        subs = await db.get_subscriptions("pending")
        if not subs:
            await _safe_edit(
                query,
                "âœ… No pending orders.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ðŸ”™ Back", callback_data="admin:main")]]),
            )
            return
        # Edit the current message to show a summary header, then post each order
        await _safe_edit(
            query,
            f"â³ <b>Pending Orders ({len(subs)})</b>\n\nShowing orders below ðŸ‘‡",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ðŸ”™ Back", callback_data="admin:main")]]),
        )
        for s in subs[:10]:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("âœ… Approve", callback_data=f"subapprove:{s['id']}"),
                InlineKeyboardButton("âŒ Reject",  callback_data=f"subreject:{s['id']}"),
            ]])
            file_id = s.get("payment_file_id")
            caption = (
                f"ðŸ’³ <b>Order #{s['id']}</b>\n"
                f"ðŸ‘¤ {html.escape(s['full_name'])} ({s.get('username','N/A')})\n"
                f"ðŸ†” <code>{s['user_id']}</code>\n"
                f"ðŸ“¦ {s['plan_name']} â€” â‚¹{s['price']}/week\n"
                f"ðŸ‘¥ Limit: {s['account_limit']} accounts"
            )
            if file_id:
                await query.message.reply_photo(file_id, caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb)  # type: ignore[union-attr]
            else:
                await query.message.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=kb)  # type: ignore[union-attr]
        return

    # â”€â”€ Stats â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if action == "stats":
        counts   = await db.get_sub_counts()
        users    = await db.get_user_count()
        accounts = await db.get_all_accounts_for_admin()
        messages = await db.get_all_messages_for_admin()
        s        = broadcaster.get_stats(user.id)
        subs     = await db.get_subscriptions("approved")
        revenue  = sum(x["price"] for x in subs)
        await _safe_edit(
            query,
            f"ðŸ“Š <b>Full Stats</b>\n\n"
            f"ðŸ‘¥ Bot users: {users}\n"
            f"ðŸ’Ž Active subs: {counts['approved']}\n"
            f"â³ Pending orders: {counts['pending']}\n"
            f"ðŸ’° Active revenue: â‚¹{revenue}/week\n\n"
            f"ðŸ“¡ Broadcasting: {'▶ Running' if broadcaster.is_running(user.id) else '⏹ Stopped'}\n"
            f"ðŸ” Cycles this session: {s['cycles']}\n"
            f"âœ… Messages sent: {s['sent']}\n"
            f"âŒ Failed: {s['failed']}\n"
            f"ðŸ’€ Dead accounts detected: {s['dead_accounts']}\n\n"
            f"ðŸ” Telethon accounts: {len(accounts)}\n"
            f"ðŸ’¬ Message pool: {len(messages)} msgs",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ðŸ”™ Back", callback_data="admin:main")]]),
        )
        return

    # â”€â”€ All users â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if action == "users":
        users_list = await db.get_all_users()
        if not users_list:
            await _safe_edit(
                query,
                "No users yet.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ðŸ”™ Back", callback_data="admin:main")]]),
            )
            return
        lines = [f"ðŸ‘¥ <b>All Users ({len(users_list)})</b>\n"]
        for u in users_list[:30]:
            uname  = u.get("username") or "â€”"
            name   = html.escape(u.get("full_name") or "Unknown")
            uid    = u["user_id"]
            seen   = (u.get("last_seen") or "")[:10]
            sub    = await db.get_user_subscription(uid)
            sub_icon = "ðŸ’Ž" if sub else "ðŸ‘¤"
            lines.append(f"{sub_icon} {name} | {uname} | <code>{uid}</code> | {seen}")
        if len(users_list) > 30:
            lines.append(f"\n<i>...and {len(users_list)-30} more</i>")
        await _safe_edit(
            query,
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ðŸ”™ Back", callback_data="admin:main")]]),
        )
        return

    # â”€â”€ Active subscribers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if action == "active_subs":
        subs = await db.get_subscriptions("approved")
        if not subs:
            await _safe_edit(
                query,
                "ðŸ’Ž No active subscribers yet.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ðŸ”™ Back", callback_data="admin:main")]]),
            )
            return
        lines = [f"ðŸ’Ž <b>Active Subscribers ({len(subs)})</b>\n"]
        for s in subs[:20]:
            exp = s.get("expires_at", "")[:10]
            lines.append(
                f"ðŸ‘¤ {html.escape(s['full_name'])} ({html.escape(s.get('username','N/A'))})\n"
                f"   ðŸ“¦ {html.escape(s['plan_name'])} | ðŸ“… Expires: {exp}"
            )
        if len(subs) > 20:
            lines.append(f"\n<i>...and {len(subs)-20} more</i>")
        await _safe_edit(
            query,
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ðŸ”™ Back", callback_data="admin:main")]]),
        )
        return

    # â”€â”€ Broadcast to all bot users â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if action == "announce":
        ctx.user_data["admin_action"] = "announce"  # type: ignore[index]
        await query.message.reply_text(  # type: ignore[union-attr]
            "ðŸ“£ <b>Announce to All Users</b>\n\n"
            "Send the message you want to broadcast to all bot users:\n/cancel to abort.",
            parse_mode=ParseMode.HTML,
        )
        return

    # â”€â”€ Ban user â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if action == "ban":
        ctx.user_data["admin_action"] = "ban"  # type: ignore[index]
        await query.message.reply_text(  # type: ignore[union-attr]
            "ðŸš« <b>Ban User</b>\n\nSend the user ID to ban:\n/cancel to abort.",
            parse_mode=ParseMode.HTML,
        )
        return

    # â”€â”€ Unban user â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if action == "unban":
        ctx.user_data["admin_action"] = "unban"  # type: ignore[index]
        await query.message.reply_text(  # type: ignore[union-attr]
            "âœ… <b>Unban User</b>\n\nSend the user ID to unban:\n/cancel to abort.",
            parse_mode=ParseMode.HTML,
        )
        return

    # â”€â”€ Settings â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if action == "settings":
        await _show_settings(query.message, query=query)
        return


async def _show_settings(target, query=None) -> None:
    maint    = await db.get_setting("maintenance_mode", "0")
    upi_on   = await db.get_setting("upi_enabled", "1")
    upi_id   = await db.get_setting("upi_id", config.DEFAULT_UPI_ID)
    qr_id    = await db.get_setting("payment_qr_file_id", "")
    support  = await db.get_setting("support_contact", "@AKKI_THE_BOSS")
    welcome  = await db.get_setting("welcome_msg", "")
    text = (
        "âš™ï¸ <b>Bot Settings</b>\n"
        "â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n\n"
        f"ðŸ”§ Maintenance: <b>{'ON ðŸ”´' if maint == '1' else 'OFF ðŸŸ¢'}</b>\n"
        f"ðŸ’³ UPI Payments: <b>{'Enabled ðŸŸ¢' if upi_on == '1' else 'Disabled ðŸ”´'}</b>\n"
        f"ðŸ†” UPI ID: <code>{html.escape(upi_id)}</code>\n"
        f"ðŸ“· Payment QR: <b>{'Set âœ…' if qr_id else 'Not set âŒ'}</b>\n"
        f"ðŸ‘¤ Support Contact: <b>{html.escape(support)}</b>\n"
        f"ðŸ“ Welcome Msg: <b>{'Custom âœ…' if welcome else 'Default'}</b>"
    )
    kb = _settings_keyboard(maint, upi_on)
    if query:
        await _safe_edit(query, text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await target.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


# â”€â”€â”€ Settings callbacks â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def cb_admin_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # type: ignore[union-attr]
    user  = update.effective_user
    if not user or user.id not in config.ADMIN_IDS:
        await query.answer("âŒ Admins only.", show_alert=True)
        return

    action = query.data.split(":", 1)[1]  # type: ignore[union-attr]

    if action == "toggle_maintenance":
        current = await db.get_setting("maintenance_mode", "0")
        new_val = "0" if current == "1" else "1"
        await db.set_setting("maintenance_mode", new_val)
        state = "ON ðŸ”´" if new_val == "1" else "OFF ðŸŸ¢"
        await query.answer(f"Maintenance mode: {state}", show_alert=True)
        await _show_settings(query.message, query=query)  # type: ignore[union-attr]
        return

    if action == "toggle_upi":
        current = await db.get_setting("upi_enabled", "1")
        new_val = "0" if current == "1" else "1"
        await db.set_setting("upi_enabled", new_val)
        state = "Enabled ðŸŸ¢" if new_val == "1" else "Disabled ðŸ”´"
        await query.answer(f"UPI Payments: {state}", show_alert=True)
        await _show_settings(query.message, query=query)  # type: ignore[union-attr]
        return

    if action == "change_upi":
        ctx.user_data["admin_action"] = "change_upi"  # type: ignore[index]
        await query.message.reply_text(  # type: ignore[union-attr]
            "ðŸ’³ <b>Change UPI ID</b>\n\nSend the new UPI ID:\nExample: <code>yourname@upi</code>\n\n/cancel to abort.",
            parse_mode=ParseMode.HTML,
        )
        return

    if action == "upload_qr":
        ctx.user_data["admin_action"] = "upload_qr"  # type: ignore[index]
        await query.message.reply_text(  # type: ignore[union-attr]
            "ðŸ“· <b>Upload Payment QR Code</b>\n\nSend the QR code image as a photo.\nUsers will see it when they choose a plan.\n\n/cancel to abort.",
            parse_mode=ParseMode.HTML,
        )
        return

    if action == "change_support":
        ctx.user_data["admin_action"] = "change_support"  # type: ignore[index]
        await query.message.reply_text(  # type: ignore[union-attr]
            "ðŸ‘¤ <b>Change Support Contact</b>\n\nSend the new support username:\nExample: <code>@YourUsername</code>\n\n/cancel to abort.",
            parse_mode=ParseMode.HTML,
        )
        return

    if action == "change_welcome":
        ctx.user_data["admin_action"] = "change_welcome"  # type: ignore[index]
        current = await db.get_setting("welcome_msg", "")
        await query.message.reply_text(  # type: ignore[union-attr]
            f"ðŸ“ <b>Change Welcome Message</b>\n\nSupports HTML tags.\nCurrent: <i>{'(default)' if not current else html.escape(current[:100])}</i>\n\nSend the new message:\n/cancel to abort.",
            parse_mode=ParseMode.HTML,
        )
        return

    if action == "clear_welcome":
        await db.set_setting("welcome_msg", "")
        await query.answer("âœ… Welcome message cleared â€” default will be used.", show_alert=True)  # type: ignore[union-attr]
        await _show_settings(query.message, query=query)  # type: ignore[union-attr]
        return


# â”€â”€â”€ Package Manager callback â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def cb_admin_pkg(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # type: ignore[union-attr]
    user  = update.effective_user
    if not user or user.id not in config.ADMIN_IDS:
        await query.answer("âŒ Admins only.", show_alert=True)
        return

    action = query.data.split(":", 1)[1]  # type: ignore[union-attr]

    # â”€â”€ List all plans â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if action == "list":
        plans = await _get_all_plans()
        subs  = await db.get_subscriptions("approved")
        lines = ["ðŸ“¦ <b>Package Manager</b>\nâ”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n"]
        kb    = []
        for key, p in plans.items():
            count = len([s for s in subs if s["plan_key"] == key])
            lines.append(
                f"<b>{html.escape(p['name'])}</b>\n"
                f"   ðŸ’° â‚¹{p['price']}/week | ðŸ‘¥ {p['account_limit']} accounts | "
                f"ðŸ—“ {p['duration_days']} days\n"
                f"   Active subscribers: {count}"
            )
            kb.append([InlineKeyboardButton(f"âœï¸ Edit {p['name']}", callback_data=f"admin_pkg:edit:{key}")])
        upi_id = await db.get_setting("upi_id", config.DEFAULT_UPI_ID)
        lines.append(f"\nðŸ’³ <b>UPI ID:</b> <code>{html.escape(upi_id)}</code>")
        kb.append([InlineKeyboardButton("ðŸ”™ Back", callback_data="admin:main")])
        await _safe_edit(
            query,
            "\n\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    # â”€â”€ Edit a specific plan â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if action.startswith("edit:") and action.count(":") == 1:
        key  = action.split(":")[1]
        plan = await _get_plan(key)
        if not plan:
            await query.answer("Plan not found.", show_alert=True)
            return
        await _safe_edit(
            query,
            f"âœï¸ <b>Editing: {html.escape(plan['name'])}</b>\n\n"
            f"ðŸ’° Price: â‚¹{plan['price']}/week\n"
            f"ðŸ‘¥ Account limit: {plan['account_limit']}\n"
            f"ðŸ—“ Duration: {plan['duration_days']} days\n\n"
            "Choose what to edit:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("ðŸ“ Plan Name",      callback_data=f"admin_pkg:editfield:{key}:name")],
                [InlineKeyboardButton("ðŸ’° Price (â‚¹)",      callback_data=f"admin_pkg:editfield:{key}:price")],
                [InlineKeyboardButton("ðŸ‘¥ Account Limit",  callback_data=f"admin_pkg:editfield:{key}:accounts")],
                [InlineKeyboardButton("ðŸ—“ Duration (days)", callback_data=f"admin_pkg:editfield:{key}:days")],
                [InlineKeyboardButton("ðŸ”™ Back",           callback_data="admin_pkg:list")],
            ]),
        )
        return

    # â”€â”€ Prompt for a specific field â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if action.startswith("editfield:"):
        parts  = action.split(":")   # editfield, key, field
        key    = parts[1]
        field  = parts[2]
        plan   = await _get_plan(key)
        labels = {"name": "Plan Name", "price": "Price (â‚¹/week, number)", "accounts": "Account Limit (number)", "days": "Duration (days, number)"}
        ctx.user_data["admin_action"] = f"pkg_edit_{field}:{key}"  # type: ignore[index]
        await query.message.reply_text(  # type: ignore[union-attr]
            f"âœï¸ <b>Edit {labels.get(field, field)} for {html.escape(plan.get('name','?'))}</b>\n\n"
            f"Current value: <code>{plan.get({'name':'name','price':'price','accounts':'account_limit','days':'duration_days'}.get(field,field),'?')}</code>\n\n"
            f"Send the new value or /cancel to abort.",
            parse_mode=ParseMode.HTML,
        )
        return


# â”€â”€â”€ Admin free-text action handler â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def handle_admin_actions(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or user.id not in config.ADMIN_IDS:
        return
    action = ctx.user_data.get("admin_action")  # type: ignore[union-attr]
    if not action:
        return

    text = (update.message.text or "").strip()  # type: ignore[union-attr]
    ctx.user_data["admin_action"] = None  # type: ignore[index]

    if action == "change_upi":
        if not text:
            await update.message.reply_text("âš ï¸ Empty â€” cancelled.")  # type: ignore[union-attr]
            return
        await db.set_setting("upi_id", text)
        await update.message.reply_text(  # type: ignore[union-attr]
            f"âœ… UPI ID updated to <code>{html.escape(text)}</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("âš™ï¸ Back to Settings", callback_data="admin:settings")]]),
        )

    elif action == "change_support":
        if not text:
            await update.message.reply_text("âš ï¸ Empty â€” cancelled.")  # type: ignore[union-attr]
            return
        await db.set_setting("support_contact", text)
        await update.message.reply_text(  # type: ignore[union-attr]
            f"âœ… Support contact updated to <b>{html.escape(text)}</b>.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("âš™ï¸ Back to Settings", callback_data="admin:settings")]]),
        )

    elif action == "change_welcome":
        await db.set_setting("welcome_msg", text)
        await update.message.reply_text(  # type: ignore[union-attr]
            "âœ… Welcome message updated.\n\nUsers will see this on /start.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("âš™ï¸ Back to Settings", callback_data="admin:settings")]]),
        )

    elif action == "ban":
        try:
            uid = int(text)
        except ValueError:
            await update.message.reply_text("âš ï¸ Invalid user ID. Send a numeric ID.")  # type: ignore[union-attr]
            return
        await db.ban_user(uid)
        await update.message.reply_text(f"ðŸš« User <code>{uid}</code> banned.", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
        try:
            await update.get_bot().send_message(uid, "ðŸš« You have been banned from this bot.")
        except Exception:
            pass

    elif action == "unban":
        try:
            uid = int(text)
        except ValueError:
            await update.message.reply_text("âš ï¸ Invalid user ID.")  # type: ignore[union-attr]
            return
        done = await db.unban_user(uid)
        if done:
            await update.message.reply_text(f"âœ… User <code>{uid}</code> unbanned.", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
            try:
                await update.get_bot().send_message(uid, "âœ… Your ban has been lifted. Use /start to continue.")
            except Exception:
                pass
        else:
            await update.message.reply_text(f"âš ï¸ User <code>{uid}</code> was not banned.", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]

    elif action == "announce":
        if not text:
            await update.message.reply_text("âš ï¸ Empty message â€” cancelled.")  # type: ignore[union-attr]
            return
        users_list = await db.get_all_users()
        sent = failed = 0
        for u in users_list:
            try:
                await update.get_bot().send_message(u["user_id"], text, parse_mode=ParseMode.HTML)
                sent += 1
            except Exception:
                failed += 1
        await update.message.reply_text(  # type: ignore[union-attr]
            f"ðŸ“£ <b>Announcement sent!</b>\nâœ… {sent} delivered | âŒ {failed} failed",
            parse_mode=ParseMode.HTML,
        )

    elif action.startswith("pkg_edit_"):
        # Format: pkg_edit_{field}:{plan_key}
        rest  = action[len("pkg_edit_"):]   # e.g. "name:basic" or "price:pro"
        field, key = rest.split(":", 1)
        plan  = await _get_plan(key)
        if not plan:
            await update.message.reply_text("âš ï¸ Plan not found.")  # type: ignore[union-attr]
            return

        if field == "name":
            if not text:
                await update.message.reply_text("âš ï¸ Empty â€” cancelled.")  # type: ignore[union-attr]
                return
            await db.set_setting(f"plan_{key}_name", text)
            await update.message.reply_text(  # type: ignore[union-attr]
                f"âœ… Plan name updated to <b>{html.escape(text)}</b>.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ðŸ“¦ Back to Package Manager", callback_data="admin_pkg:list")]]),
            )
        elif field == "price":
            try:
                val = int(text)
                assert val > 0
            except (ValueError, AssertionError):
                await update.message.reply_text("âš ï¸ Enter a positive whole number. Example: <code>49</code>", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
                ctx.user_data["admin_action"] = action  # type: ignore[index]
                return
            await db.set_setting(f"plan_{key}_price", str(val))
            await update.message.reply_text(  # type: ignore[union-attr]
                f"âœ… Price updated to â‚¹{val}/week.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ðŸ“¦ Back to Package Manager", callback_data="admin_pkg:list")]]),
            )
        elif field == "accounts":
            try:
                val = int(text)
                assert val > 0
            except (ValueError, AssertionError):
                await update.message.reply_text("âš ï¸ Enter a positive whole number. Example: <code>5</code>", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
                ctx.user_data["admin_action"] = action  # type: ignore[index]
                return
            await db.set_setting(f"plan_{key}_accounts", str(val))
            await update.message.reply_text(  # type: ignore[union-attr]
                f"âœ… Account limit updated to {val}.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ðŸ“¦ Back to Package Manager", callback_data="admin_pkg:list")]]),
            )
        elif field == "days":
            try:
                val = int(text)
                assert val > 0
            except (ValueError, AssertionError):
                await update.message.reply_text("âš ï¸ Enter a positive whole number. Example: <code>7</code>", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
                ctx.user_data["admin_action"] = action  # type: ignore[index]
                return
            await db.set_setting(f"plan_{key}_days", str(val))
            await update.message.reply_text(  # type: ignore[union-attr]
                f"âœ… Duration updated to {val} days.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ðŸ“¦ Back to Package Manager", callback_data="admin_pkg:list")]]),
            )


# â”€â”€â”€ Receipt fallback (catches photo when user came via menu, not /subscribe) â”€

async def handle_sub_receipt_fallback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or user.id in config.ADMIN_IDS:
        return
    if not ctx.user_data.get("sub_plan_key"):  # type: ignore[union-attr]
        return
    # Delegate to the real receipt handler (it clears sub_plan_key when done)
    await sub_receive_receipt(update, ctx)


# â”€â”€â”€ Admin photo handler (QR code upload) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def handle_admin_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or user.id not in config.ADMIN_IDS:
        return
    action = ctx.user_data.get("admin_action")  # type: ignore[union-attr]
    if action != "upload_qr":
        return
    ctx.user_data["admin_action"] = None  # type: ignore[index]
    photo = update.message.photo  # type: ignore[union-attr]
    if not photo:
        await update.message.reply_text("âš ï¸ No photo received â€” cancelled.")  # type: ignore[union-attr]
        return
    file_id = photo[-1].file_id
    await db.set_setting("payment_qr_file_id", file_id)
    await update.message.reply_text(  # type: ignore[union-attr]
        "âœ… <b>Payment QR code saved!</b>\nUsers will see it when selecting a plan.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("âš™ï¸ Back to Settings", callback_data="admin:settings")]]),
    )


# â”€â”€â”€ Subscription flow â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _show_plans(target, query=None) -> None:
    upi_enabled = await db.get_setting("upi_enabled", "1")
    upi_id      = await db.get_setting("upi_id", config.DEFAULT_UPI_ID)
    plans       = await _get_all_plans()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(p["label"], callback_data=f"subplan:{key}")]
        for key, p in plans.items()
    ] + [
        [InlineKeyboardButton("ðŸ”™ Back", callback_data="menu:back_main")],
    ])

    plan_lines = "\n\n".join(
        f"<b>{p['name']}</b> â€” â‚¹{p['price']}/week\n"
        f"   â€¢ {p['account_limit']} accounts | {p['duration_days']} days"
        for p in plans.values()
    )
    upi_note = f"\nðŸ’³ Pay via UPI: <code>{html.escape(upi_id)}</code>" if upi_enabled == "1" else "\nâš ï¸ UPI payments temporarily disabled."
    text = (
        f"ðŸ’Ž <b>Choose Your Plan</b>\n"
        f"{upi_note}\n\n"
        f"{plan_lines}"
    )
    if query:
        await _safe_edit(query, text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await target.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cmd_subscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if await _maintenance_block(update):
        return ConversationHandler.END
    await _show_plans(update.message)  # type: ignore[union-attr]
    return SUB_CHOOSE_PLAN


async def cb_subplan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()  # type: ignore[union-attr]
    key: str = query.data.split(":", 1)[1]  # type: ignore[union-attr]

    if key == "cancel":
        await query.edit_message_text("âŒ Cancelled.")  # type: ignore[union-attr]
        return ConversationHandler.END

    plan = await _get_plan(key)
    if not plan:
        return ConversationHandler.END

    upi_enabled = await db.get_setting("upi_enabled", "1")
    if upi_enabled != "1":
        await query.edit_message_text("âš ï¸ UPI payments are temporarily disabled. Please try again later.")  # type: ignore[union-attr]
        return ConversationHandler.END

    upi_id  = await db.get_setting("upi_id", config.DEFAULT_UPI_ID)
    qr_id   = await db.get_setting("payment_qr_file_id", "")
    ctx.user_data["sub_plan_key"] = key  # type: ignore[index]

    caption = (
        f"âœ… Selected: <b>{plan['label']}</b>\n\n"
        f"ðŸ’³ Pay <b>â‚¹{plan['price']}</b> to UPI:\n"
        f"<code>{html.escape(upi_id)}</code>\n\n"
        f"ðŸ“¸ Scan the QR code above OR pay manually to UPI ID.\n\n"
        f"After paying, send your <b>payment screenshot</b> here.\n"
        f"âœ… Subscription activates after admin approval.\n\n"
        f"/cancel to abort."
    ) if qr_id else (
        f"âœ… Selected: <b>{plan['label']}</b>\n\n"
        f"ðŸ’³ Pay <b>â‚¹{plan['price']}</b> to UPI:\n"
        f"<code>{html.escape(upi_id)}</code>\n\n"
        f"After paying, send your <b>payment screenshot</b> here.\n"
        f"âœ… Subscription activates after admin approval.\n\n"
        f"/cancel to abort."
    )

    if qr_id:
        await query.message.reply_photo(  # type: ignore[union-attr]
            photo=qr_id,
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
    else:
        await query.edit_message_text(caption, parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
    return SUB_AWAIT_RECEIPT


async def sub_receive_receipt(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    photo = update.message.photo  # type: ignore[union-attr]
    if not photo:
        await update.message.reply_text("âš ï¸ Send a <b>screenshot/photo</b> of the payment.\n/cancel to abort.", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
        return SUB_AWAIT_RECEIPT

    plan_key  = ctx.user_data.get("sub_plan_key", "basic")  # type: ignore[union-attr]
    plan      = await _get_plan(plan_key) or dict(config.PLANS.get(plan_key, {}))
    file_id   = photo[-1].file_id
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    username  = f"@{user.username}" if user.username else f"ID:{user.id}"

    sub_id = await db.create_subscription(
        user_id=user.id, username=username, full_name=full_name,
        plan_key=plan_key, plan_name=plan["name"],
        price=plan["price"], account_limit=plan["account_limit"],
        payment_file_id=file_id,
    )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("âœ… Approve", callback_data=f"subapprove:{sub_id}"),
        InlineKeyboardButton("âŒ Reject",  callback_data=f"subreject:{sub_id}"),
    ]])
    admin_caption = (
        f"ðŸ’³ <b>New Payment Order #{sub_id}</b>\n"
        f"â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n"
        f"ðŸ‘¤ {html.escape(full_name)} ({html.escape(username)})\n"
        f"ðŸ†” User ID: <code>{user.id}</code>\n"
        f"ðŸ“¦ Plan: {html.escape(plan['name'])} â€” â‚¹{plan['price']}/week\n"
        f"ðŸ‘¥ Account limit: {plan['account_limit']}\n"
        f"ðŸ“… Duration: {plan.get('duration_days', 7)} days\n\n"
        f"Tap âœ… Approve or âŒ Reject below."
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await update.get_bot().send_photo(
                chat_id=admin_id, photo=file_id,
                caption=admin_caption,
                parse_mode=ParseMode.HTML, reply_markup=kb,
            )
        except Exception as exc:
            logger.warning("Admin notify failed: {}", exc)
            # Fallback: send as text if photo fails
            try:
                await update.get_bot().send_message(
                    chat_id=admin_id, text=admin_caption,
                    parse_mode=ParseMode.HTML, reply_markup=kb,
                )
            except Exception as exc2:
                logger.warning("Admin text notify also failed: {}", exc2)

    await update.message.reply_text(  # type: ignore[union-attr]
        "âœ… <b>Screenshot received!</b>\n\nPending admin approval â€” you'll get a notification.\n"
        f"<i>Request ID: #{sub_id}</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=_back_main_kb(),
    )
    ctx.user_data["sub_plan_key"] = None  # type: ignore[index]
    return ConversationHandler.END


# â”€â”€â”€ Approve / reject subscription â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def cb_sub_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user  = update.effective_user
    if not user or user.id not in config.ADMIN_IDS:
        await query.answer("âŒ Admins only.", show_alert=True)  # type: ignore[union-attr]
        return
    sub_id = int(query.data.split(":")[1])  # type: ignore[union-attr]
    # Fetch plan to get duration_days override
    pending = await db.get_subscriptions("pending")
    pending_sub = next((s for s in pending if s["id"] == sub_id), None)
    days = 7
    if pending_sub:
        plan = await _get_plan(pending_sub["plan_key"])
        days = plan.get("duration_days", 7)
    sub = await db.approve_subscription(sub_id, duration_days=days)
    if not sub:
        await query.answer("âš ï¸ Not found or already processed.", show_alert=True)  # type: ignore[union-attr]
        return
    await query.answer("âœ… Approved! User notified.", show_alert=True)  # type: ignore[union-attr]
    approved_text = (
        f"âœ… <b>APPROVED â€” Order #{sub_id}</b>\n"
        f"ðŸ‘¤ {html.escape(sub['full_name'])} ({sub.get('username','N/A')})\n"
        f"ðŸ“¦ {html.escape(sub['plan_name'])} â€” â‚¹{sub['price']}/week\n"
        f"ðŸ“… Expires: {sub.get('expires_at','')[:10]}"
    )
    # Try to update the original message
    try:
        await query.edit_message_caption(approved_text, parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
    except Exception:
        try:
            await query.edit_message_text(approved_text, parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
        except Exception:
            await query.message.reply_text(approved_text, parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
    # Notify the user
    try:
        await update.get_bot().send_message(
            sub["user_id"],
            f"ðŸŽ‰ <b>Subscription Activated!</b>\n\n"
            f"Plan: <b>{html.escape(sub['plan_name'])}</b>\n"
            f"Accounts: up to <b>{sub['account_limit']}</b>\n"
            f"Expires: <b>{sub.get('expires_at','')[:10]}</b>\n\n"
            "Tap /start to unlock â–¶ï¸ Start Broadcast and all premium features!",
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        logger.warning("User notify failed: {}", exc)


async def cb_sub_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user  = update.effective_user
    if not user or user.id not in config.ADMIN_IDS:
        await query.answer("âŒ Admins only.", show_alert=True)  # type: ignore[union-attr]
        return
    sub_id = int(query.data.split(":")[1])  # type: ignore[union-attr]
    sub    = await db.reject_subscription(sub_id)
    if not sub:
        await query.answer("âš ï¸ Not found or already processed.", show_alert=True)  # type: ignore[union-attr]
        return
    await query.answer("âŒ Rejected. User notified.", show_alert=True)  # type: ignore[union-attr]
    rejected_text = (
        f"âŒ <b>REJECTED â€” Order #{sub_id}</b>\n"
        f"ðŸ‘¤ {html.escape(sub['full_name'])} ({sub.get('username','N/A')})\n"
        f"ðŸ“¦ {html.escape(sub['plan_name'])} â€” â‚¹{sub['price']}/week"
    )
    try:
        await query.edit_message_caption(rejected_text, parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
    except Exception:
        try:
            await query.edit_message_text(rejected_text, parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
        except Exception:
            await query.message.reply_text(rejected_text, parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
    try:
        await update.get_bot().send_message(
            sub["user_id"],
            "âŒ <b>Payment Not Verified</b>\n\nYour payment screenshot was rejected.\n"
            "Please use /subscribe to try again or contact support.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        logger.warning("User notify failed: {}", exc)


# â”€â”€â”€ Speed menu â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _show_speed_menu(target, user_id: int, query=None) -> None:
    current   = await db.get_user_broadcast_setting(user_id, "speed_preset", "medium")
    custom_b  = await db.get_user_broadcast_setting(user_id, "custom_batch_size",  "5")
    custom_bd = await db.get_user_broadcast_setting(user_id, "custom_batch_delay", "5")
    custom_cd = await db.get_user_broadcast_setting(user_id, "custom_cycle_delay", "30")
    keyboard  = []
    for key, p in config.SPEED_PRESETS.items():
        tick = "âœ… " if key == current else ""
        keyboard.append([InlineKeyboardButton(f"{tick}{p['label']} {p['risk']}", callback_data=f"speed:{key}")])
    keyboard.append([InlineKeyboardButton("ðŸ“˜ How It Works", callback_data="speed:howto")])
    keyboard.append([InlineKeyboardButton("ðŸ”™ Back",          callback_data="menu:back_main")])
    text = (
        "⚡ <b>Select Speed</b>\n\n"
        "🟢 Safe - slower, lowest risk\n"
        "🟡 Medium - balanced default\n"
        "🔴 Aggressive - faster, higher risk\n"
        "⚙ Custom - your settings\n\n"
        f"⚙ <b>Custom:</b> Batch {custom_b} | BD {custom_bd}s | CD {custom_cd}s"
    )
    kb = InlineKeyboardMarkup(keyboard)
    if query:
        await _safe_edit(query, text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await target.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cmd_speed(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or await db.is_banned(user.id):
        return
    await _show_speed_menu(update.message, user.id)  # type: ignore[union-attr]


async def cb_speed(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    logger.warning("========== CB_SPEED ENTERED ==========")
    logger.warning(f"Callback Data: {update.callback_query.data if update.callback_query else 'NONE'}")
    logger.warning(f"User: {update.effective_user.id if update.effective_user else 'NONE'}")

    query = update.callback_query
    await query.answer()  # type: ignore[union-attr]

    user = update.effective_user
    if not user:
        return
    key = query.data.split(":")[1]  # type: ignore[union-attr]
    if key == "howto":
        await query.answer(
            "1ï¸âƒ£ Add messages\n2ï¸âƒ£ Select accounts\n3ï¸âƒ£ Choose speed\n4ï¸âƒ£ Start!\n"
            "Groups shuffled each cycle. Dead accounts ðŸ’€ auto-detected.",
            show_alert=True,
        )
        return
    if key == "back_wizard":
        # Cancel custom wizard and go back to speed menu
        ctx.user_data["custom_step"] = None  # type: ignore[index]
        await _show_speed_menu(query.message, user.id, query=query)  # type: ignore[union-attr]
        return
    if key not in config.SPEED_PRESETS:
        return
    await db.set_user_broadcast_setting(user.id, "speed_preset", key)
    preset = config.SPEED_PRESETS[key]
    if key == "custom":
        ctx.user_data["custom_step"] = "batch_size"  # type: ignore[index]
        await query.edit_message_text(  # type: ignore[union-attr]
            "ðŸŽ›ï¸ <b>Custom Speed â€” Step 1/3</b>\n\n"
            "How many groups per batch?\n"
            "<code>0</code> = send to all groups at once (no batching)\n\n"
            "Send a number or tap ðŸ”™ Back to cancel.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ðŸ”™ Back", callback_data="speed:back_wizard")]]),
        )
        return
    await query.edit_message_text(  # type: ignore[union-attr]
        f"âœ… Speed: <b>{preset['label']} {preset['risk']}</b>\n<i>{preset['desc']}</i>\n\n"
        f"Tap /start to return to the menu.",
        parse_mode=ParseMode.HTML,
    )


_WIZARD_BACK_KB = InlineKeyboardMarkup([[InlineKeyboardButton("ðŸ”™ Back to Speed Menu", callback_data="speed:back_wizard")]])


async def handle_custom_wizard(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    step = ctx.user_data.get("custom_step")  # type: ignore[union-attr]
    if not step:
        return
    text = (update.message.text or "").strip()  # type: ignore[union-attr]

    if step == "batch_size":
        try:
            val = int(text)
        except ValueError:
            await update.message.reply_text(  # type: ignore[union-attr]
                "âš ï¸ Please enter a whole number. Example: <code>5</code>\n<code>0</code> = no batching.",
                parse_mode=ParseMode.HTML,
                reply_markup=_WIZARD_BACK_KB,
            )
            return
        await db.set_user_broadcast_setting(user.id, "custom_batch_size", str(max(val, 0)))
        ctx.user_data["custom_step"] = "batch_delay"  # type: ignore[index]
        await update.message.reply_text(  # type: ignore[union-attr]
            "ðŸŽ›ï¸ <b>Custom Speed â€” Step 2/3</b>\n\n"
            "Batch delay â€” pause between batches (seconds)?\n"
            "Example: <code>5</code>\n\n"
            "Send a number or tap ðŸ”™ Back to cancel.",
            parse_mode=ParseMode.HTML,
            reply_markup=_WIZARD_BACK_KB,
        )

    elif step == "batch_delay":
        try:
            val = float(text)
        except ValueError:
            await update.message.reply_text(  # type: ignore[union-attr]
                "âš ï¸ Please enter a number. Example: <code>5</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=_WIZARD_BACK_KB,
            )
            return
        await db.set_user_broadcast_setting(user.id, "custom_batch_delay", str(val))
        ctx.user_data["custom_step"] = "cycle_delay"  # type: ignore[index]
        await update.message.reply_text(  # type: ignore[union-attr]
            "ðŸŽ›ï¸ <b>Custom Speed â€” Step 3/3</b>\n\n"
            "Cycle delay â€” pause between full cycles (seconds)?\n"
            "Example: <code>30</code>\n\n"
            "Send a number or tap ðŸ”™ Back to cancel.",
            parse_mode=ParseMode.HTML,
            reply_markup=_WIZARD_BACK_KB,
        )

    elif step == "cycle_delay":
        try:
            val = float(text)
        except ValueError:
            await update.message.reply_text(  # type: ignore[union-attr]
                "âš ï¸ Please enter a number. Example: <code>30</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=_WIZARD_BACK_KB,
            )
            return
        await db.set_user_broadcast_setting(user.id, "custom_cycle_delay", str(val))
        ctx.user_data["custom_step"] = None  # type: ignore[index]
        b  = await db.get_user_broadcast_setting(user.id, "custom_batch_size",  "5")
        bd = await db.get_user_broadcast_setting(user.id, "custom_batch_delay", "5")
        cd = await db.get_user_broadcast_setting(user.id, "custom_cycle_delay", "30")
        await update.message.reply_text(  # type: ignore[union-attr]
            f"âœ… <b>Custom speed saved!</b>\n\n"
            f"ðŸ“¦ Batch size: <b>{b}</b> groups\n"
            f"â± Batch delay: <b>{bd}s</b>\n"
            f"ðŸ”„ Cycle delay: <b>{cd}s</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=_back_main_kb(),
        )


# â”€â”€â”€ Select accounts â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _show_select_accounts(target, user_id: int, query=None) -> None:
    accounts = await db.get_accounts(user_id)
    if not accounts:
        text = "No accounts. Use /add first."
        if query:
            await _safe_edit(query, text, reply_markup=_back_main_kb())
        else:
            await target.reply_text(text, reply_markup=_back_main_kb())
        return
    raw_sel  = await db.get_user_broadcast_setting(user_id, "selected_accounts", "all")
    sel_ids  = {a["id"] for a in accounts} if raw_sel in ("all", None) else {int(x) for x in raw_sel.split(",") if x}
    keyboard = []
    for acc in accounts:
        icon = "âœ…" if acc["id"] in sel_ids else "â˜‘ï¸"
        st   = {"active": "ðŸŸ¢", "offline": "ðŸ”´", "dead": "ðŸ’€"}.get(acc["status"], "âšª")
        keyboard.append([InlineKeyboardButton(f"{icon} {st} {acc['name']} â€” {acc['phone']}", callback_data=f"selacct:{acc['id']}")])
    keyboard += [
        [
            InlineKeyboardButton("âœ… All",  callback_data="selacct:all"),
            InlineKeyboardButton("â¬œ None", callback_data="selacct:none"),
        ],
        [InlineKeyboardButton("ðŸ’¾ Done",  callback_data="selacct:done")],
        [InlineKeyboardButton("ðŸ”™ Back",  callback_data="menu:back_main")],
    ]
    text = "ðŸ‘¥ <b>Select Accounts</b>\nTap to toggle:"
    kb   = InlineKeyboardMarkup(keyboard)
    if query:
        await _safe_edit(query, text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await target.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cb_select_accounts(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # type: ignore[union-attr]
    user = update.effective_user
    if not user:
        return
    data: str  = query.data  # type: ignore[union-attr]
    accounts   = await db.get_accounts(user.id)
    owned_ids   = {a["id"] for a in accounts}
    raw_sel    = await db.get_user_broadcast_setting(user.id, "selected_accounts", "all")
    sel_ids    = owned_ids if raw_sel in ("all", None) else {int(x) for x in raw_sel.split(",") if x}
    sel_ids   &= owned_ids

    if data == "selacct:all":
        sel_ids = set(owned_ids)
    elif data == "selacct:none":
        sel_ids = set()
    elif data == "selacct:done":
        await db.set_user_broadcast_setting(user.id, "selected_accounts", "all" if len(sel_ids) == len(accounts) else ",".join(str(i) for i in sel_ids))
        await query.edit_message_text(  # type: ignore[union-attr]
            f"âœ… {len(sel_ids)} account(s) selected.",
            reply_markup=_back_main_kb(),
        )
        return
    else:
        aid = int(data.split(":")[1])
        if aid not in owned_ids:
            await query.answer("Account not found.", show_alert=True)
            return
        sel_ids.discard(aid) if aid in sel_ids else sel_ids.add(aid)

    # Rebuild keyboard with updated ticks
    keyboard = []
    for acc in accounts:
        icon = "âœ…" if acc["id"] in sel_ids else "â˜‘ï¸"
        st   = {"active": "ðŸŸ¢", "offline": "ðŸ”´", "dead": "ðŸ’€"}.get(acc["status"], "âšª")
        keyboard.append([InlineKeyboardButton(f"{icon} {st} {acc['name']} â€” {acc['phone']}", callback_data=f"selacct:{acc['id']}")])
    keyboard += [
        [
            InlineKeyboardButton("âœ… All",  callback_data="selacct:all"),
            InlineKeyboardButton("â¬œ None", callback_data="selacct:none"),
        ],
        [InlineKeyboardButton("ðŸ’¾ Done",  callback_data="selacct:done")],
        [InlineKeyboardButton("ðŸ”™ Back",  callback_data="menu:back_main")],
    ]
    try:
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))  # type: ignore[union-attr]
    except Exception:
        pass


# â”€â”€â”€ Accounts: /add /rm /list â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    if await db.is_banned(user.id):
        return
    accounts = await db.get_accounts(user.id)
    if not accounts:
        await update.message.reply_text("ðŸ“‹ No accounts added yet.")  # type: ignore[union-attr]
        return
    lines = ["ðŸ“‹ <b>Accounts</b>\n"]
    for i, acc in enumerate(accounts, 1):
        icon = {"active": "ðŸŸ¢", "offline": "ðŸ”´", "dead": "ðŸ’€"}.get(acc["status"], "âšª")
        lines.append(f"{i}. <b>{html.escape(acc['name'])}</b>\n   ðŸ“± {html.escape(acc['phone'])}  {icon} {acc['status'].capitalize()}")
    lines.append(f"\n<i>Total: {len(accounts)}</i>")
    await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.HTML)  # type: ignore[union-attr]


async def cmd_rm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or await db.is_banned(user.id):
        return
    accounts = await db.get_accounts(user.id)
    if not accounts:
        await update.message.reply_text("No accounts to remove.")  # type: ignore[union-attr]
        return
    keyboard = [[InlineKeyboardButton(
        f"{'ðŸŸ¢' if a['status']=='active' else 'ðŸ”´'} {a['name']} â€” {a['phone']}",
        callback_data=f"rm:{a['id']}"
    )] for a in accounts]
    keyboard.append([InlineKeyboardButton("âŒ Cancel", callback_data="rm:cancel")])
    await update.message.reply_text("ðŸ—‘ Select account to remove:", reply_markup=InlineKeyboardMarkup(keyboard))  # type: ignore[union-attr]


async def cb_rm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # type: ignore[union-attr]
    user = update.effective_user
    if not user:
        return
    data: str = query.data  # type: ignore[union-attr]
    if data == "rm:cancel":
        await query.edit_message_text("Cancelled.")  # type: ignore[union-attr]
        return
    account_id = int(data.split(":")[1])
    accounts   = await db.get_accounts(user.id)
    acc        = next((a for a in accounts if a["id"] == account_id), None)
    if not acc:
        await query.edit_message_text("Not found.")  # type: ignore[union-attr]
        return
    keyboard = [[
        InlineKeyboardButton("âœ… Confirm", callback_data=f"rmconfirm:{account_id}"),
        InlineKeyboardButton("âŒ Cancel",  callback_data="rm:cancel"),
    ]]
    await query.edit_message_text(  # type: ignore[union-attr]
        f"âš ï¸ Remove <b>{html.escape(acc['name'])}</b> ({html.escape(acc['phone'])})?\nSession deleted permanently.",
        parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cb_rm_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # type: ignore[union-attr]
    user = update.effective_user
    if not user:
        return
    account_id = int(query.data.split(":")[1])  # type: ignore[union-attr]
    removed    = await am.remove_account(user.id, account_id)
    if removed:
        await query.edit_message_text(  # type: ignore[union-attr]
            f"âœ… <b>{html.escape(removed['name'])}</b> removed.",
            parse_mode=ParseMode.HTML,
            reply_markup=_back_main_kb(),
        )
    else:
        await query.edit_message_text("âš ï¸ Not found.")  # type: ignore[union-attr]


# â”€â”€â”€ Message pool â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _show_msgs_menu(target, user_id: int, query=None) -> None:
    messages = await db.get_messages(user_id)
    if not messages:
        text = "ðŸ’¬ Pool is empty. Use /addmsg."
        if query:
            await _safe_edit(query, text, reply_markup=_back_ads_kb())
        else:
            await target.reply_text(text, reply_markup=_back_ads_kb())
        return
    keyboard = [[InlineKeyboardButton(
        f"ðŸ—‘ #{m['id']}: {m['text'][:38].replace(chr(10),' ')}{'â€¦' if len(m['text'])>38 else ''}",
        callback_data=f"delmsg:{m['id']}"
    )] for m in messages]
    keyboard.append([InlineKeyboardButton("ðŸ”™ Back", callback_data="menu:ads_manager")])
    lines = [f"ðŸ’¬ <b>Message Pool ({len(messages)})</b>\n"]
    for m in messages:
        preview = html.escape(m["text"][:200]) + ("â€¦" if len(m["text"]) > 200 else "")
        lines.append(f"<b>#{m['id']}</b>: {preview}")
    text = "\n\n".join(lines)
    kb   = InlineKeyboardMarkup(keyboard)
    if query:
        await _safe_edit(query, text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await target.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cmd_addmsg(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user or await db.is_banned(user.id):
        return ConversationHandler.END
    await update.message.reply_text("ðŸ’¬ Send your broadcast message text.\n/cancel to abort.")  # type: ignore[union-attr]
    return AWAITING_MSG


async def receive_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user:
        return ConversationHandler.END
    text = (update.message.text or "").strip()  # type: ignore[union-attr]
    if not text:
        await update.message.reply_text("âš ï¸ Empty â€” try again or /cancel.")  # type: ignore[union-attr]
        return AWAITING_MSG
    msg_id = await db.add_message(user.id, text)
    count  = len(await db.get_messages(user.id))
    preview = html.escape(text[:200]) + ("â€¦" if len(text) > 200 else "")
    await update.message.reply_text(  # type: ignore[union-attr]
        f"âœ… <b>Message #{msg_id} added!</b>\n\n{preview}\n\n<i>Pool: {count} msgs</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=_back_ads_kb(),
    )
    return ConversationHandler.END


async def handle_inline_addmsg(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not ctx.user_data.get("awaiting_addmsg"):  # type: ignore[union-attr]
        return
    ctx.user_data["awaiting_addmsg"] = False  # type: ignore[index]
    text = (update.message.text or "").strip()  # type: ignore[union-attr]
    if not text:
        return
    msg_id = await db.add_message(user.id, text)
    count  = len(await db.get_messages(user.id))
    await update.message.reply_text(
        f"âœ… <b>Message #{msg_id} added!</b> Pool: {count} msg(s).",
        parse_mode=ParseMode.HTML,
        reply_markup=_back_ads_kb(),
    )  # type: ignore[union-attr]


async def cb_delmsg(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # type: ignore[union-attr]
    user = update.effective_user
    if not user:
        return
    data: str = query.data  # type: ignore[union-attr]
    if data == "delmsg:close":
        await query.edit_message_reply_markup(reply_markup=None)  # type: ignore[union-attr]
        return
    msg_id   = int(data.split(":")[1])
    deleted  = await db.remove_message(user.id, msg_id)
    messages = await db.get_messages(user.id)
    if deleted and not messages:
        await query.edit_message_text(  # type: ignore[union-attr]
            "ðŸ—‘ Pool is now empty.",
            reply_markup=_back_ads_kb(),
        )
        return
    if deleted:
        keyboard = [[InlineKeyboardButton(
            f"ðŸ—‘ #{m['id']}: {m['text'][:38].replace(chr(10),' ')}",
            callback_data=f"delmsg:{m['id']}"
        )] for m in messages]
        keyboard.append([InlineKeyboardButton("ðŸ”™ Back", callback_data="menu:ads_manager")])
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))  # type: ignore[union-attr]


# â”€â”€â”€ /status â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or await db.is_banned(user.id):
        return
    accounts   = await db.get_accounts(user.id)
    messages   = await db.get_messages(user.id)
    preset_key = await db.get_user_broadcast_setting(user.id, "speed_preset", "medium")
    preset     = config.SPEED_PRESETS.get(preset_key or "medium", config.SPEED_PRESETS["medium"])
    bc_status  = "▶ Running" if broadcaster.is_running(user.id) else "⏹ Stopped"
    s          = broadcaster.get_stats(user.id)
    lifetime   = await db.get_owner_statistics(user.id)
    is_admin   = user.id in config.ADMIN_IDS
    await update.message.reply_text(  # type: ignore[union-attr]
        f"ðŸ“Š <b>Status</b>\n\n"
        f"ðŸ‘¥ Accounts: {len(accounts)}\n"
        f"ðŸ’¬ Messages: {len(messages)}\n"
        f"âš¡ Speed: {preset['label']} {preset['risk']}\n"
        f"ðŸ“¡ Broadcasting: {bc_status}\n"
        f"ðŸ” Cycles: {s['cycles']} | âœ… {s['sent']} | âŒ {s['failed']} | ðŸ’€ {s['dead_accounts']}\n"
        f"Lifetime sent: {lifetime['total_sent']} | Today: {lifetime['today_sent']} | Success: {lifetime['success_rate']}%",
        parse_mode=ParseMode.HTML, reply_markup=_main_keyboard(is_admin),
    )


async def cmd_messages(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or await db.is_banned(user.id):
        return
    await _show_msgs_menu(update.message, user.id)  # type: ignore[union-attr]


async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or await db.is_banned(user.id):
        return
    await _show_speed_menu(update.message, user.id)  # type: ignore[union-attr]


async def cmd_autoreply(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🤖 Auto Reply settings will stay here when enabled.", reply_markup=_back_main_kb())  # type: ignore[union-attr]


# â”€â”€â”€ /cancel â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["custom_step"]    = None  # type: ignore[index]
    ctx.user_data["awaiting_addmsg"] = False  # type: ignore[index]
    ctx.user_data["admin_action"]    = None  # type: ignore[index]
    if update.effective_chat:
        await am.cancel_add(update.effective_chat.id)
    await update.message.reply_text("âŒ Cancelled.", reply_markup=_back_main_kb())  # type: ignore[union-attr]
    return ConversationHandler.END


# â”€â”€â”€ /add ConversationHandler â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user or await db.is_banned(user.id):
        return ConversationHandler.END
    if await _maintenance_block(update):
        return ConversationHandler.END
    sub = await db.get_user_subscription(user.id)
    if user.id not in config.ADMIN_IDS and not sub:
        await update.message.reply_text("💎 Subscribe before adding accounts. Use /subscribe.")  # type: ignore[union-attr]
        return ConversationHandler.END
    current_count = await db.count_user_accounts(user.id)
    account_limit = int(sub["account_limit"]) if sub else 999999
    if current_count >= account_limit:
        await update.message.reply_text(
            f"Account limit reached for your plan ({account_limit} accounts). Remove an account or upgrade your plan.",
        )  # type: ignore[union-attr]
        return ConversationHandler.END
    keyboard = [
        [InlineKeyboardButton("ðŸ“± Phone + OTP",    callback_data="add:phone")],
        [InlineKeyboardButton("ðŸ”‘ Session String", callback_data="add:session")],
        [InlineKeyboardButton("âŒ Cancel",          callback_data="add:cancel")],
    ]
    await update.message.reply_text("âž• <b>Add Account</b>\n\nChoose login method:",  # type: ignore[union-attr]
        parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
    return ADD_CHOOSE_METHOD


async def add_choose_method(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()  # type: ignore[union-attr]
    data: str = query.data  # type: ignore[union-attr]
    if data == "add:cancel":
        await query.edit_message_text("âŒ Cancelled.")  # type: ignore[union-attr]
        return ConversationHandler.END
    if data == "add:phone":
        await query.edit_message_text("ðŸ“± Enter phone (international):\nExample: <code>+91XXXXXXXXXX</code>\n\n/cancel to abort.", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
        return ADD_PHONE
    if data == "add:session":
        await query.edit_message_text("ðŸ”‘ Paste your Telethon <b>session string</b>:\n\n/cancel to abort.", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
        return ADD_SESSION_STRING
    return ConversationHandler.END


async def add_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user:
        return ConversationHandler.END
    phone = (update.message.text or "").strip()  # type: ignore[union-attr]
    if not phone.startswith("+"):
        await update.message.reply_text("âš ï¸ Must start with '+'. Try again or /cancel.")  # type: ignore[union-attr]
        return ADD_PHONE
    try:
        await am.begin_phone_login(update.effective_chat.id, user.id, phone)  # type: ignore[union-attr]
    except Exception as exc:
        await update.message.reply_text(f"âŒ {exc}\n/cancel to abort.")  # type: ignore[union-attr]
        return ADD_PHONE
    await update.message.reply_text(f"âœ… OTP sent to <code>{html.escape(phone)}</code>.\nEnter code:\n\n/cancel to abort.", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
    return ADD_OTP


async def add_otp(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    otp = (update.message.text or "").strip()  # type: ignore[union-attr]
    try:
        result = await am.submit_otp(update.effective_chat.id, otp)  # type: ignore[union-attr]
    except ValueError as exc:
        await update.message.reply_text(f"âŒ {exc}\nTry again or /cancel.")  # type: ignore[union-attr]
        return ADD_OTP
    except Exception as exc:
        await update.message.reply_text(f"âŒ {exc}")  # type: ignore[union-attr]
        return ConversationHandler.END
    if result is None:
        await update.message.reply_text("ðŸ” 2FA required. Enter your password:\n\n/cancel to abort.")  # type: ignore[union-attr]
        return ADD_PASSWORD
    await _finish_add(update, result)
    return ConversationHandler.END


async def add_password(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    password = (update.message.text or "").strip()  # type: ignore[union-attr]
    try:
        result = await am.submit_password(update.effective_chat.id, password)  # type: ignore[union-attr]
    except Exception as exc:
        await update.message.reply_text(f"âŒ {exc}\n/cancel to abort.")  # type: ignore[union-attr]
        return ADD_PASSWORD
    await _finish_add(update, result)
    return ConversationHandler.END


async def add_session_string(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user:
        return ConversationHandler.END
    session_str = (update.message.text or "").strip()  # type: ignore[union-attr]
    try:
        result = await am.import_session_string(user.id, session_str)
    except Exception as exc:
        await update.message.reply_text(f"âŒ {exc}\nTry again or /cancel.")  # type: ignore[union-attr]
        return ADD_SESSION_STRING
    await _finish_add(update, result)
    return ConversationHandler.END


async def _finish_add(update: Update, info: dict) -> None:
    await update.message.reply_text(  # type: ignore[union-attr]
        f"âœ… <b>Account Added</b>\n\nðŸ‘¤ <b>{html.escape(info['name'])}</b>\nðŸ“± {html.escape(info['phone'])}\nðŸŸ¢ Active",
        parse_mode=ParseMode.HTML,
        reply_markup=_back_main_kb(),
    )


# â”€â”€â”€ /startauto /stopauto commands â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def cmd_startauto(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or await db.is_banned(user.id):
        return
    lvl = await _level(user.id)
    if lvl == "guest":
        await update.message.reply_text("ðŸ’Ž Subscribe to unlock broadcasting!\nUse /subscribe.")  # type: ignore[union-attr]
        return
    if broadcaster.is_running(user.id):
        await update.message.reply_text("ðŸ“¡ Already running.")  # type: ignore[union-attr]
        return
    messages = await db.get_messages(user.id)
    if not messages:
        await update.message.reply_text("âŒ No messages. Use /addmsg first.")  # type: ignore[union-attr]
        return
    accounts = await db.get_accounts(user.id)
    if not accounts:
        await update.message.reply_text("âŒ No accounts. Use /add first.")  # type: ignore[union-attr]
        return
    
    await broadcaster.start_broadcast(user.id)
    await update.message.reply_text("â–¶ï¸ <b>Broadcasting started!</b>", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]


async def cmd_stopauto(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or await db.is_banned(user.id):
        return
    lvl = await _level(user.id)
    if lvl == "guest":
        await update.message.reply_text("ðŸ’Ž Subscribe to unlock broadcasting.")  # type: ignore[union-attr]
        return
    stopped = await broadcaster.stop_broadcast(user.id)
    s = broadcaster.get_stats(user.id)
    if stopped:
        await update.message.reply_text(f"â¹ <b>Stopped.</b> Cycles {s['cycles']} | âœ… {s['sent']} | âŒ {s['failed']}", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
    else:
        await update.message.reply_text("Not running.")  # type: ignore[union-attr]


# â”€â”€â”€ /admin command â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@admin_only_deco
async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    counts = await db.get_sub_counts()
    users  = await db.get_user_count()
    maint  = await db.get_setting("maintenance_mode", "0")
    upi_on = await db.get_setting("upi_enabled", "1")
    await update.message.reply_text(  # type: ignore[union-attr]
        f"ðŸ”§ <b>Admin Panel</b>\n\n"
        f"Orders â€” Total: {counts['total']} | Pending: {counts['pending']} | "
        f"Approved: {counts['approved']} | Rejected: {counts['rejected']}\n"
        f"Users: {users}\n"
        f"Maintenance: {'ON ðŸ”´' if maint == '1' else 'OFF ðŸŸ¢'}\n"
        f"UPI: {'ON ðŸŸ¢' if upi_on == '1' else 'OFF ðŸ”´'}",
        parse_mode=ParseMode.HTML,
        reply_markup=_admin_panel_keyboard(),
    )


# â”€â”€â”€ Handler registry â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def build_add_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler(["add", "addaccount"], add_start)],
        states={
            ADD_CHOOSE_METHOD:  [CallbackQueryHandler(add_choose_method, pattern="^add:")],
            ADD_PHONE:          [MessageHandler(filters.TEXT & ~filters.COMMAND, add_phone)],
            ADD_OTP:            [MessageHandler(filters.TEXT & ~filters.COMMAND, add_otp)],
            ADD_PASSWORD:       [MessageHandler(filters.TEXT & ~filters.COMMAND, add_password)],
            ADD_SESSION_STRING: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_session_string)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
        per_message=False,
    )


def build_addmsg_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler(["addmsg", "addmessage"], cmd_addmsg)],
        states={AWAITING_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_msg)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
    )


def build_subscribe_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("subscribe", cmd_subscribe)],
        states={
            SUB_CHOOSE_PLAN:   [CallbackQueryHandler(cb_subplan, pattern="^subplan:")],
            SUB_AWAIT_RECEIPT: [MessageHandler(filters.PHOTO, sub_receive_receipt)],
        },
        per_message=False,
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
    )


def register_all(app) -> None:
    app.add_handler(CommandHandler("start",          cmd_start))
    app.add_handler(CommandHandler("help",           cmd_help))
    app.add_handler(CommandHandler("status",         cmd_status))
    app.add_handler(CommandHandler("dashboard",      cmd_status))
    app.add_handler(CommandHandler(["list", "accounts"], cmd_list))
    app.add_handler(CommandHandler(["rm", "removeaccount"], cmd_rm))
    app.add_handler(CommandHandler("messages",       cmd_messages))
    app.add_handler(CommandHandler("settings",       cmd_settings))
    app.add_handler(CommandHandler("autoreply",      cmd_autoreply))
    app.add_handler(CommandHandler("speed",          cmd_speed))
    app.add_handler(CommandHandler(["startauto", "startbroadcast"], cmd_startauto))
    app.add_handler(CommandHandler(["stopauto", "stopbroadcast"], cmd_stopauto))
    app.add_handler(CommandHandler("admin",          cmd_admin))
    app.add_handler(CommandHandler("cancel",         cmd_cancel))

    app.add_handler(build_add_conversation())
    app.add_handler(build_addmsg_conversation())
    app.add_handler(build_subscribe_conversation())

    app.add_handler(CallbackQueryHandler(cb_menu,            pattern="^menu:"))
    app.add_handler(CallbackQueryHandler(cb_admin,           pattern="^admin:"))
    app.add_handler(CallbackQueryHandler(cb_admin_settings,  pattern="^admsettings:"))
    app.add_handler(CallbackQueryHandler(cb_admin_pkg,       pattern="^admin_pkg:"))
    app.add_handler(CallbackQueryHandler(cb_rm,              pattern="^rm:\\d+$"))
    app.add_handler(CallbackQueryHandler(cb_rm_confirm,      pattern="^rmconfirm:\\d+$"))
    app.add_handler(CallbackQueryHandler(cb_rm,              pattern="^rm:cancel$"))
    app.add_handler(CallbackQueryHandler(cb_select_accounts, pattern="^selacct:"))
    app.add_handler(CallbackQueryHandler(cb_speed,           pattern="^speed:"))
    app.add_handler(CallbackQueryHandler(cb_delmsg,          pattern="^delmsg:"))
    app.add_handler(CallbackQueryHandler(cb_subplan,         pattern="^subplan:"))
    app.add_handler(CallbackQueryHandler(cb_sub_approve,     pattern="^subapprove:\\d+$"))
    app.add_handler(CallbackQueryHandler(cb_sub_reject,      pattern="^subreject:\\d+$"))

    # Low-priority free-text handlers (group=1 runs first)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_wizard),  group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_inline_addmsg),  group=2)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_actions),  group=3)
    # Photo handlers â€” run after conversation handlers (groups are independent)
    app.add_handler(MessageHandler(filters.PHOTO, handle_sub_receipt_fallback), group=5)
    app.add_handler(MessageHandler(filters.PHOTO, handle_admin_photo),          group=10)

