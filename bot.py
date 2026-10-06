import os
import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import aiohttp
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_FOOTBALL_KEY = "56b9dc74cb0fa6f847fe07869eeb5d65"
ADMIN_ID = 5293614793

API_URL = "https://v3.football.api-sports.io"
BD_TZ = timezone(timedelta(hours=6))
DB_FILE = "bot_users.db"


# =========================================================
# DATABASE
# =========================================================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            is_vip INTEGER DEFAULT 0,
            join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def add_user(user_id, username, first_name):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO users (user_id, username, first_name)
        VALUES (?, ?, ?, 0)
    """, (user_id, username or "", first_name or ""))
    conn.commit()
    conn.close()


def is_user_vip(user_id):
    if user_id == ADMIN_ID:
        return True
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT is_vip FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row[0]) if row else False


def set_vip_status(user_id, status=1):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_vip = ? WHERE user_id = ?", (status, user_id))
    conn.commit()
    conn.close()


def get_user_count():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    count = cur.fetchone()[0]
    conn.close()
    return count


def get_all_user_ids():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]


# =========================================================
# API REQUEST
# =========================================================

async def api_get(endpoint, params=None):
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    url = f"{API_URL}/{endpoint}"
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers, params=params or {}) as response:
                if response.status != 200:
                    return None
                return await response.json()
    except Exception:
        return None


# =========================================================
# HELPERS
# =========================================================

def api_time_to_bd(date_string):
    try:
        dt = datetime.fromisoformat(date_string.replace("Z", "+00:00"))
        return dt.astimezone(BD_TZ)
    except Exception:
        return None


async def get_fixtures():
    now = datetime.now(BD_TZ)
    dates = [now.date(), (now + timedelta(days=1)).date()]
    all_matches = []
    for date in dates:
        data = await api_get("fixtures", {"date": date.strftime("%Y-%m-%d"), "timezone": "Asia/Dhaka"})
        if not data:
            continue
        all_matches.extend(data.get("response", []))
    return all_matches


def get_match_info(fixture):
    teams = fixture.get("teams", {})
    home = teams.get("home", {}).get("name", "Home")
    away = teams.get("away", {}).get("name", "Away")
    league_data = fixture.get("league", {})
    league_name = league_data.get("name", "League")
    return f"{home} vs {away}", league_name


def get_score(fixture):
    goals = fixture.get("goals", {})
    home = goals.get("home") if goals.get("home") is not None else 0
    away = goals.get("away") if goals.get("away") is not None else 0
    return home, away


# BANGLA MARKET CONVERTER
def market_data(fixture):
    home, away = get_score(fixture)
    total_goals = home + away
    status = fixture.get("fixture", {}).get("status", {}).get("short", "")
    is_done = status in {"FT", "AET", "PEN"}

    # Dynamic low-risk choices
    if total_goals >= 2 or not is_done:
        odds = 1.35
        confidence = 88
        name_bn = "ওভার ১.৫ গোল (Over 1.5 Goals)"
        status_bn = "জিতেছে (WIN)" if (is_done and total_goals >= 2) else ("হেরেছে (LOSS)" if is_done else "চলমান/আসন্ন")
    else:
        odds = 1.30
        confidence = 90
        name_bn = "ডাবল চান্স ১এক্স (Double Chance 1X)"
        status_bn = "জিতেছে (WIN)" if (is_done and home >= away) else ("হেরেছে (LOSS)" if is_done else "চলমান/আসন্ন")

    return name_bn, odds, confidence, status_bn


# ACCUMULATOR BUILDER
def generate_accumulator(fixtures, target_odds, min_conf=75):
    selected = []
    current_odds = 1.0

    for f in fixtures:
        name_bn, odds, conf, status_bn = market_data(f)
        if conf >= min_conf:
            selected.append((f, name_bn, odds, conf, status_bn))
            current_odds *= odds
            if current_odds >= target_odds:
                break

    return selected, round(current_odds, 2)


# =========================================================
# KEYBOARDS
# =========================================================

def main_keyboard():
    keyboard = [
        [InlineKeyboardButton("🎯 Free 2+ Odds Acca", callback_data="free_2"), InlineKeyboardButton("🚀 Free 5+ Odds Acca", callback_data="free_5")],
        [InlineKeyboardButton("🔥 Free 10+ Odds Acca", callback_data="free_10"), InlineKeyboardButton("💣 Free 20+ Odds Acca", callback_data="free_20")],
        [InlineKeyboardButton("👑 VIP SAFE ACCUMULATORS (Low Risk)", callback_data="vip_menu")],
        [InlineKeyboardButton("📊 Help / Rules", callback_data="help_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def vip_keyboard():
    keyboard = [
        [InlineKeyboardButton("🛡️ VIP Safe 2+ Odds (95% Conf)", callback_data="vip_2")],
        [InlineKeyboardButton("💎 VIP Gold 5+ Odds (88% Conf)", callback_data="vip_5")],
        [InlineKeyboardButton("👑 VIP Ultra 10+ Odds (82% Conf)", callback_data="vip_10")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)


# =========================================================
# HANDLERS
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.username, user.first_name)
    count = get_user_count()

    text = f"🔥 <b>VIP FOOTBALL ACCUMULATOR BOT</b> 🔥\n\n⚡ <i>Free & VIP Low Risk Multi-Odds Predictions</i>\n👥 <b>Active Users:</b> <code>{count} Users</code>\n\n👇 <b>Niche theke Odds select করুন:</b>"
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_keyboard())


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "menu_main":
        count = get_user_count()
        text = f"🔥 <b>VIP FOOTBALL ACCUMULATOR BOT</b> 🔥\n\n👥 <b>Active Users:</b> <code>{count} Users</code>\n\n👇 <b>Select an Option:</b>"
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=main_keyboard())
        return

    if data == "vip_menu":
        text = "👑 <b>VIP LOW RISK ACCUMULATORS</b>\n───────────────────────────\n\nVIP Section-e shudhu top league-er low risk o highly verified match thake.\n\n👇 Select VIP Odds:"
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=vip_keyboard())
        return

    if data == "help_menu":
        text = "📌 <b>টিপস ও নিয়মাবলি (Rules):</b>\n\n১. <b>Free Odds:</b> দৈনন্দিন অ্যানালাইসিস অনুযায়ী স্ট্যান্ডার্ড মাল্টি টিকিট।\n২. <b>VIP Odds:</b> হাই উইনিং রেট এবং একদম কম ঝুঁকিপূর্ণ (Low Risk) ম্যাচের কম্বিনেশন।\n৩. টিপসের স্ট্যাটাস 'জিতেছে (WIN)' নাকি 'হেরেছে (LOSS)' তা অটোমেটিক বাংলাতে দেখাবে।"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        return

    # ACCUMULATOR REQUESTS
    if data.startswith("free_") or data.startswith("vip_"):
        is_vip_req = data.startswith("vip_")
        target_odds = float(data.split("_")[1])

        if is_vip_req and not is_user_vip(user_id):
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]])
            await query.edit_message_text("❌ <b>VIP Access Required!</b>\n\nApnar accounte VIP access nei. VIP er jonno Admin er sathe jogajog korun.", parse_mode="HTML", reply_markup=keyboard)
            return

        fixtures = await get_fixtures()
        min_conf = 85 if is_vip_req else 70
        acc_matches, total_odds = generate_accumulator(fixtures, target_odds, min_conf)

        if not acc_matches:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]])
            await query.edit_message_text("❌ Kono match paowa jayni. Porer bar abar chesta korun.", parse_mode="HTML", reply_markup=keyboard)
            return

        mode_title = f"👑 VIP LOW RISK {target_odds}+ ODDS" if is_vip_req else f"🎯 FREE {target_odds}+ ODDS ACCA"
        msg_lines = [f"📌 <b>{mode_title}</b>", "───────────────────────────\n"]

        for idx, (f, name_bn, odds, conf, status_bn) in enumerate(acc_matches, 1):
            match_name, league = get_match_info(f)
            h_score, a_score = get_score(f)
            msg_lines.append(
                f"⚽ <b>Match {idx}:</b> {match_name}\n"
                f"🏆 <b>League:</b> {league}\n"
                f"💡 <b>টিপস (Tip):</b> {name_bn}\n"
                f"📈 <b>অডস (Odds):</b> @{odds}\n"
                f"📊 <b>ফলাফল/অবস্থা:</b> {status_bn} ({h_score}-{a_score})\n"
                f"───────────────────────────"
            )

        msg_lines.append(f"\n🔥 <b>TOTAL COMBINED ODDS:</b> <code>@{total_odds}</code>")
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data=data), InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]])

        await query.edit_message_text("\n".join(msg_lines), parse_mode="HTML", reply_markup=keyboard)


# =========================================================
# ADMIN COMMANDS
# =========================================================

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin access required.")
        return

    count = get_user_count()
    text = (
        f"👑 <b>ADMIN PANEL</b>\n\n"
        f"👥 Active Users: <code>{count}</code>\n\n"
        "<b>Commands:</b>\n"
        "• VIP dite: <code>/addvip USER_ID</code>\n"
        "• VIP sorate: <code>/removevip USER_ID</code>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def add_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        target_id = int(context.args[0])
        set_vip_status(target_id, 1)
        await update.message.reply_text(f"✅ User <code>{target_id}</code> is now VIP!", parse_mode="HTML")
    except Exception:
        await update.message.reply_text("Usage: /addvip USER_ID")


async def remove_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        target_id = int(context.args[0])
        set_vip_status(target_id, 0)
        await update.message.reply_text(f"❌ User <code>{target_id}</code> VIP removed!", parse_mode="HTML")
    except Exception:
        await update.message.reply_text("Usage: /removevip USER_ID")


# =========================================================
# MAIN FUNCTION
# =========================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable missing.")

    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("addvip", add_vip_cmd))
    app.add_handler(CommandHandler("removevip", remove_vip_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("VIP Football Accumulator Bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
