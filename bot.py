"""
KGF CYBER TOOL — Telegram Bot
Refactored from CLI tool for Railway deployment.
"""
import os
import re
import time
import random
import logging
import asyncio
from datetime import datetime

import requests
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, ContextTypes, filters,
)
from dotenv import load_dotenv

from database import (
    register_user, save_account, get_accounts,
    get_account, delete_account, log_action,
)
import fb_api

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN missing in environment")

# ---------- Conversation states ----------
(
    MENU,
    ADD_COOKIE,
    SELECT_ACCOUNT,
    TOOL_MENU,
    COMMENT_LINK,
    COMMENT_TEXT,
    COMMENT_LIMIT,
    COMMENT_RUN,
    SHARE_LINK,
    SHARE_LIMIT,
    SHARE_RUN,
    REACT_LINK,
    REACT_TYPE,
    REACT_RUN,
) = range(14)

TEMP = {}  # chat_id -> {data}


# ---------- Helper ----------
def user_temp(chat_id: int) -> dict:
    return TEMP.setdefault(chat_id, {})


async def edit_or_send(update: Update, text: str, keyboard=None, parse_mode="Markdown"):
    """Edit message if callback, else send new."""
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text=text, reply_markup=keyboard, parse_mode=parse_mode,
            )
            return
        except Exception:
            pass
    await update.effective_chat.send_message(
        text=text, reply_markup=keyboard, parse_mode=parse_mode,
    )


def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Facebook Account", callback_data="add_acc")],
        [InlineKeyboardButton("📂 My Accounts", callback_data="list_acc")],
        [InlineKeyboardButton("❌ Remove Account", callback_data="remove_acc")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help")],
    ])


def tool_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Auto Comment", callback_data="tool_comment")],
        [InlineKeyboardButton("📤 Auto Group Share", callback_data="tool_share")],
        [InlineKeyboardButton("❤️ Auto React", callback_data="tool_react")],
        [InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")],
    ])


# ---------- /start ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    u = update.effective_user
    register_user(update.effective_chat.id, u.username or "", u.first_name or "")

    text = (
        "🔥 *KGF CYBER TOOL X KALYAN KING* 🔥\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👋 স্বাগতম!\n"
        "নিচের মেনু থেকে কাজ শুরু করুন।\n\n"
        "⚠️ *নোট:* Datacenter IP দিয়ে Facebook cookie use করলে "
        "account checkpoint/ban এর risk আছে।"
    )
    await update.message.reply_text(text, reply_markup=main_menu_kb(), parse_mode="Markdown")
    return MENU


# ---------- Main menu router ----------
async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    data = q.data
    chat_id = update.effective_chat.id

    if data == "main_menu":
        await edit_or_send(update, "🏠 *Main Menu*", main_menu_kb())
        return MENU

    if data == "help":
        text = (
            "📖 *Help*\n"
            "1️⃣ *Add Account* — Facebook cookie paste করুন\n"
            "2️⃣ *My Accounts* — saved account বেছে নিন\n"
            "3️⃣ *Auto Comment* — post link + comment count দিন\n"
            "4️⃣ *Auto Share* — group এ share করুন\n"
            "5️⃣ *Auto React* — LIKE/LOVE/etc reaction দিন\n\n"
            "⚠️ Cookie expire হলে আবার Add করুন।"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]])
        await edit_or_send(update, text, kb)
        return MENU

    if data == "add_acc":
        await edit_or_send(
            update,
            "🍪 *Facebook Cookie পাঠান*\n\n"
            "Cookie কীভাবে বের করবেন:\n"
            "1. Chrome এ Facebook login করুন\n"
            "2. Cookie Editor extension open করুন\n"
            "3. `Export` → JSON copy করুন\n"
            "4. এখানে paste করে পাঠান\n\n"
            "❌ Cancel: /cancel",
        )
        return ADD_COOKIE

    if data == "list_acc":
        accounts = get_accounts(chat_id)
        if not accounts:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("➕ Add Account", callback_data="add_acc")],
                                       [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]])
            await edit_or_send(update, "📭 কোনো account save করা নেই।", kb)
            return MENU

        kb_rows = []
        for a in accounts:
            kb_rows.append([InlineKeyboardButton(
                f"👤 {a['name']} ({a['fb_uid']})",
                callback_data=f"acc_{a['fb_uid']}",
            )])
        kb_rows.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
        await edit_or_send(update, "📂 *আপনার Accounts:*", InlineKeyboardMarkup(kb_rows))
        return SELECT_ACCOUNT

    if data == "remove_acc":
        accounts = get_accounts(chat_id)
        if not accounts:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]])
            await edit_or_send(update, "📭 মুছে ফেলার মতো account নেই।", kb)
            return MENU

        kb_rows = []
        for a in accounts:
            kb_rows.append([InlineKeyboardButton(
                f"🗑 {a['name']} ({a['fb_uid']})",
                callback_data=f"del_{a['fb_uid']}",
            )])
        kb_rows.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
        await edit_or_send(update, "🗑 *কোন account মুছবেন?*", InlineKeyboardMarkup(kb_rows))
        return SELECT_ACCOUNT

    return MENU


# ---------- Add cookie ----------
async def add_cookie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cookie = update.message.text.strip()
    chat_id = update.effective_chat.id

    msg = await update.message.reply_text("⏳ Cookie verify করা হচ্ছে...")

    result = fb_api.login_with_cookie(cookie)
    if not result["success"]:
        await msg.edit_text(
            f"❌ *Cookie Invalid!*\n`{result['error'][:200]}`\n\nআবার /start দিন।",
            parse_mode="Markdown",
        )
        return MENU

    save_account(chat_id, result["uid"], result["name"], cookie, result["token"])
    log_action(chat_id, "add_account", result["uid"])

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔧 Tool Menu", callback_data=f"use_{result['uid']}")],
                               [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]])
    await msg.edit_text(
        f"✅ *Login Success!*\n"
        f"👤 Name: `{result['name']}`\n"
        f"🆔 ID: `{result['uid']}`\n\n"
        f"এখন tool use করতে পারেন।",
        reply_markup=kb, parse_mode="Markdown",
    )
    return MENU


# ---------- Select account / delete ----------
async def select_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    data = q.data
    chat_id = update.effective_chat.id

    if data.startswith("acc_"):
        fb_uid = data[4:]
        acc = get_account(chat_id, fb_uid)
        if not acc:
            await edit_or_send(update, "❌ Account পাওয়া যায়নি।",
                               InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]))
            return MENU
        user_temp(chat_id)["active_acc"] = acc
        await edit_or_send(
            update,
            f"✅ *Active:* `{acc['name']}` ({acc['fb_uid']})\n\nকী করতে চান?",
            tool_menu_kb(),
        )
        return TOOL_MENU

    if data.startswith("del_"):
        fb_uid = data[4:]
        delete_account(chat_id, fb_uid)
        await edit_or_send(
            update,
            "🗑 *Account deleted successfully!*",
            InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]),
        )
        return MENU

    return SELECT_ACCOUNT


# ---------- Tool menu router ----------
async def tool_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    data = q.data
    chat_id = update.effective_chat.id

    if data == "main_menu":
        await edit_or_send(update, "🏠 *Main Menu*", main_menu_kb())
        return MENU

    if data.startswith("use_"):
        fb_uid = data[4:]
        acc = get_account(chat_id, fb_uid)
        if acc:
            user_temp(chat_id)["active_acc"] = acc
            await edit_or_send(update, f"✅ Active: `{acc['name']}`", tool_menu_kb())
            return TOOL_MENU

    if data == "tool_comment":
        await edit_or_send(update, "💬 *Post link পাঠান:*\n\n❌ Cancel: /cancel")
        return COMMENT_LINK

    if data == "tool_share":
        await edit_or_send(update, "📤 *যে post link share করবেন সেটা পাঠান:*\n\n❌ Cancel: /cancel")
        return SHARE_LINK

    if data == "tool_react":
        await edit_or_send(update, "❤️ *Post link পাঠান:*\n\n❌ Cancel: /cancel")
        return REACT_LINK

    return TOOL_MENU


# ---------- AUTO COMMENT flow ----------
async def comment_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    link = update.message.text.strip()
    pid = fb_api.get_post_id(link)
    if not pid:
        await update.message.reply_text("❌ Post ID বের করা যায়নি। আবার পাঠান:")
        return COMMENT_LINK
    user_temp(update.effective_chat.id)["comment_pid"] = pid

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Default Comments", callback_data="cmt_default")],
        [InlineKeyboardButton("✏️ Custom Comment", callback_data="cmt_custom")],
        [InlineKeyboardButton("🔙 Back", callback_data="tool_menu_back")],
    ])
    await update.message.reply_text("📝 *Comment type বেছে নিন:*", reply_markup=kb, parse_mode="Markdown")
    return COMMENT_TEXT


async def comment_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # callback query
    if update.callback_query:
        q = update.callback_query
        await q.answer()
        chat_id = update.effective_chat.id

        if q.data == "tool_menu_back":
            await edit_or_send(update, "🔧 *Tool Menu*", tool_menu_kb())
            return TOOL_MENU

        if q.data == "cmt_default":
            user_temp(chat_id)["comment_list"] = fb_api.DEFAULT_COMMENTS
            await edit_or_send(update, "🔢 কতটা comment করবেন? (সংখ্যা পাঠান)")
            return COMMENT_LIMIT

        if q.data == "cmt_custom":
            await edit_or_send(update, "✏️ *আপনার comment পাঠান* (একাধিক হলে `,` দিয়ে আলাদা করুন):")
            return COMMENT_LIMIT

    # message text = custom comment list
    text = update.message.text.strip()
    clist = [x.strip() for x in text.split(",") if x.strip()]
    if not clist:
        clist = fb_api.DEFAULT_COMMENTS
    user_temp(update.effective_chat.id)["comment_list"] = clist
    await update.message.reply_text("🔢 কতটা comment করবেন? (সংখ্যা পাঠান)")
    return COMMENT_LIMIT


async def comment_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    try:
        n = int(update.message.text.strip())
        n = max(1, min(n, 100))
    except Exception:
        await update.message.reply_text("❌ সংখ্যা দিন। আবার:")
        return COMMENT_LIMIT

    t = user_temp(chat_id)
    acc = t.get("active_acc")
    clist = t.get("comment_list", fb_api.DEFAULT_COMMENTS)
    pid = t.get("comment_pid")

    if not acc or not pid:
        await update.message.reply_text("❌ Session নেই। /start দিন।")
        return MENU

    msg = await update.message.reply_text(f"🚀 Comment শুরু হচ্ছে... (0/{n})")
    success = 0
    fail = 0

    for i in range(1, n + 1):
        kom = random.choice(clist)
        r = fb_api.post_comment(pid, acc["token"], acc["cookie"], kom)
        if r["success"]:
            success += 1
        else:
            fail += 1

        if i % 3 == 0 or i == n:
            try:
                await msg.edit_text(f"⏳ Progress: {i}/{n}\n✅ {success}  ❌ {fail}")
            except Exception:
                pass

        await asyncio.sleep(random.randint(15, 22))

    log_action(chat_id, "auto_comment", f"pid={pid} n={n} ok={success}")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔧 Tool Menu", callback_data="tool_menu_back")]])
    await msg.edit_text(
        f"✅ *Comment Complete!*\n\nSuccess: `{success}`\nFailed: `{fail}`",
        reply_markup=kb, parse_mode="Markdown",
    )
    return TOOL_MENU


# ---------- AUTO SHARE flow ----------
async def share_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    link = update.message.text.strip()
    chat_id = update.effective_chat.id
    user_temp(chat_id)["share_link"] = link

    acc = user_temp(chat_id).get("active_acc")
    if not acc:
        await update.message.reply_text("❌ Account নেই। /start দিন।")
        return MENU

    msg = await update.message.reply_text("⏳ Group list আনা হচ্ছে...")
    groups = fb_api.get_groups(acc["token"], acc["cookie"], limit=500)
    if not groups:
        await msg.edit_text("❌ কোনো group পাওয়া যায়নি অথবা token invalid।")
        return TOOL_MENU

    user_temp(chat_id)["groups"] = groups
    await msg.edit_text(
        f"✅ মোট *{len(groups)}* টা group পাওয়া গেছে।\n"
        f"কতটাতে share করবেন? (max {len(groups)})",
        parse_mode="Markdown",
    )
    return SHARE_LIMIT


async def share_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    try:
        n = int(update.message.text.strip())
    except Exception:
        await update.message.reply_text("❌ সংখ্যা দিন:")
        return SHARE_LIMIT

    t = user_temp(chat_id)
    groups = t.get("groups", [])[:n]
    acc = t.get("active_acc")
    post_link = t.get("share_link")

    if not groups or not acc:
        await update.message.reply_text("❌ Session নেই। /start দিন।")
        return MENU

    msg = await update.message.reply_text(f"🚀 Share শুরু... (0/{len(groups)})")
    ok = 0
    bad = 0

    for i, g in enumerate(groups, 1):
        r = fb_api.share_to_group(g["id"], acc["token"], acc["cookie"], post_link)
        if r["success"]:
            ok += 1
        else:
            bad += 1

        if i % 2 == 0 or i == len(groups):
            try:
                await msg.edit_text(f"⏳ Progress: {i}/{len(groups)}\n✅ {ok}  ❌ {bad}")
            except Exception:
                pass

        await asyncio.sleep(random.randint(20, 30))

    log_action(chat_id, "auto_share", f"groups={len(groups)} ok={ok}")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔧 Tool Menu", callback_data="tool_menu_back")]])
    await msg.edit_text(
        f"✅ *Share Complete!*\n\nSuccess: `{ok}`\nFailed: `{bad}`",
        reply_markup=kb, parse_mode="Markdown",
    )
    return TOOL_MENU


# ---------- AUTO REACT flow ----------
async def react_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    link = update.message.text.strip()
    pid = fb_api.get_post_id(link)
    if not pid:
        await update.message.reply_text("❌ Post ID পাওয়া যায়নি। আবার:")
        return REACT_LINK
    user_temp(update.effective_chat.id)["react_pid"] = pid

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👍 LIKE", callback_data="react_LIKE"),
         InlineKeyboardButton("❤️ LOVE", callback_data="react_LOVE")],
        [InlineKeyboardButton("😆 HAHA", callback_data="react_HAHA"),
         InlineKeyboardButton("😮 WOW", callback_data="react_WOW")],
        [InlineKeyboardButton("😢 SAD", callback_data="react_SAD"),
         InlineKeyboardButton("😡 ANGRY", callback_data="react_ANGRY")],
        [InlineKeyboardButton("🔙 Back", callback_data="tool_menu_back")],
    ])
    await update.message.reply_text("❤️ *Reaction type বেছে নিন:*", reply_markup=kb, parse_mode="Markdown")
    return REACT_TYPE


async def react_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    chat_id = update.effective_chat.id
    data = q.data

    if data == "tool_menu_back":
        await edit_or_send(update, "🔧 *Tool Menu*", tool_menu_kb())
        return TOOL_MENU

    if not data.startswith("react_"):
        return REACT_TYPE

    react_type_str = data[6:]
    t = user_temp(chat_id)
    acc = t.get("active_acc")
    pid = t.get("react_pid")

    if not acc or not pid:
        await edit_or_send(update, "❌ Session নেই। /start দিন।")
        return MENU

    msg = await edit_or_send(update, f"⏳ {react_type_str} reaction পাঠানো হচ্ছে...")

    r = fb_api.react_to_post(pid, acc["token"], acc["cookie"], react_type_str)
    log_action(chat_id, "auto_react", f"pid={pid} type={react_type_str} ok={r['success']}")

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔧 Tool Menu", callback_data="tool_menu_back")]])
    if r["success"]:
        await edit_or_send(update, f"✅ *{react_type_str}* reaction সফলভাবে দেওয়া হয়েছে!", kb)
    else:
        await edit_or_send(update, f"❌ Failed: `{r.get('error', 'unknown')[:150]}`", kb)
    return TOOL_MENU


# ---------- /cancel ----------
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Canceled. /start দিয়ে আবার শুরু করুন।")
    return ConversationHandler.END


# ---------- Main ----------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU: [
                CallbackQueryHandler(menu_router),
                MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router),
            ],
            ADD_COOKIE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_cookie)],
            SELECT_ACCOUNT: [CallbackQueryHandler(select_account)],
            TOOL_MENU: [CallbackQueryHandler(tool_router)],
            COMMENT_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, comment_link)],
            COMMENT_TEXT: [
                CallbackQueryHandler(comment_text),
                MessageHandler(filters.TEXT & ~filters.COMMAND, comment_text),
            ],
            COMMENT_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, comment_limit)],
            SHARE_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, share_link)],
            SHARE_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, share_limit)],
            REACT_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, react_link)],
            REACT_TYPE: [CallbackQueryHandler(react_type)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("cancel", cancel))

    logger.info("🤖 Bot starting with long polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
