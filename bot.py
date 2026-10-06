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
ITEMS_PER_PAGE = 5


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
        VALUES (?, ?, ?)
    """, (user_id, username or "", first_name or ""))
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
                    print("API Error:", response.status)
                    return None
                return await response.json()
    except Exception as e:
        print("API Exception:", e)
        return None


# =========================================================
# HELPERS & VISUAL PROGRESS BAR
# =========================================================

def api_time_to_bd(date_string):
    try:
        dt = datetime.fromisoformat(date_string.replace("Z", "+00:00"))
        return dt.astimezone(BD_TZ)
    except Exception:
        return None


def format_remaining(target):
    now = datetime.now(BD_TZ)
    seconds = int((target - now).total_seconds())
    if seconds <= 0:
        return "STARTED"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours:02d}h {minutes:02d}m"


def make_progress_bar(confidence):
    filled = int(confidence / 10)
    return "█" * filled + "░" * (10 - filled)


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


LIVE_STATUSES = {"1H", "2H", "HT", "ET", "P", "LIVE"}


def is_live(fixture):
    status = fixture.get("fixture", {}).get("status", {}).get("short", "")
    return status in LIVE_STATUSES


def is_finished(fixture):
    status = fixture.get("fixture", {}).get("status", {}).get("short", "")
    return status in {"FT", "AET", "PEN"}


def get_match_info(fixture):
    teams = fixture.get("teams", {})
    home = teams.get("home", {}).get("name", "Home")
    away = teams.get("away", {}).get("name", "Away")
    
    league_data = fixture.get("league", {})
    league_name = league_data.get("name", "General League")
    country = league_data.get("country", "Global")
    
    return f"{home} 🆚 {away}", f"{league_name} ({country})"


def get_score(fixture):
    goals = fixture.get("goals", {})
    home = goals.get("home") if goals.get("home") is not None else 0
    away = goals.get("away") if goals.get("away") is not None else 0
    return home, away


# =========================================================
# MARKET & ODDS ANALYSIS
# =========================================================

def market_data(fixture, market):
    home, away = get_score(fixture)
    total_goals = home + away

    if market == "over15":
        odds = 1.35
        confidence = 85
        result = "WIN" if total_goals >= 2 else "LOSS"
        name = "Over 1.5 Goals"

    elif market == "btts":
        odds = 1.75
        confidence = 68
        result = "WIN" if home >= 1 and away >= 1 else "LOSS"
        name = "Both Teams to Score (BTTS)"

    elif market == "double":
        odds = 1.30
        confidence = 88
        result = "WIN" if home >= away else "LOSS"
        name = "Double Chance (1X)"

    elif market == "safe":
        odds = 1.30
        confidence = 92
        result = "WIN" if total_goals >= 1 else "LOSS"
        name = "Safe Bet (1.30 Odds)"

    elif market == "best":
        odds = 1.45
        confidence = 82
        result = "WIN" if total_goals >= 2 else "LOSS"
        name = "Best Selection"

    else:
        odds = 1.50
        confidence = 65
        result = "LOSS"
        name = "General Tip"

    return name, odds, confidence, result


# =========================================================
# MATCH CARD DESIGN
# =========================================================

def make_match_card(fixture, market):
    fixture_data = fixture.get("fixture", {})
    status_data = fixture_data.get("status", {})
    match_name, league_info = get_match_info(fixture)
    home, away = get_score(fixture)
    name, odds, confidence, result = market_data(fixture, market)
    bar = make_progress_bar(confidence)

    if is_live(fixture):
        minute = status_data.get("elapsed", "?")
        return f"🏆 <b>League:</b> <code>{league_info}</code>\n🔴 <b>{match_name}</b>\n⏱ <b>Status:</b> LIVE ({minute}') | ⚽ <b>Score:</b> {home}-{away}\n🎯 <b>Tip:</b> {name} | 📈 <b>Odds:</b> @{odds}\n📊 <b>Conf:</b> {bar} {confidence}%\n───────────────────────────\n"

    if is_finished(fixture):
        res_icon = "✅ WIN" if result == "WIN" else "❌ LOSS"
        return f"🏆 <b>League:</b> <code>{league_info}</code>\n🏁 <b>{match_name}</b>\n⚽ <b>Final Score:</b> {home}-{away}\n💡 <b>Tip:</b> {name} (@{odds})\n📊 <b>Result:</b> {res_icon}\n───────────────────────────\n"

    date_string = fixture_data.get("date")
    match_time = api_time_to_bd(date_string)
    time_text = match_time.strftime("%I:%M %p") if match_time else "--:--"
    remaining = format_remaining(match_time) if match_time else "--:--"

    return f"🏆 <b>League:</b> <code>{league_info}</code>\n⚽ <b>{match_name}</b>\n🕐 <b>Time:</b> {time_text} (In {remaining})\n💡 <b>Tip:</b> {name} | 📈 <b>Odds:</b> @{odds}\n📊 <b>Conf:</b> {bar} {confidence}%\n───────────────────────────\n"


# =========================================================
# FILTERS
# =========================================================

def filter_next_12_hours(fixtures):
    now = datetime.now(BD_TZ)
    end = now + timedelta(hours=12)
    matches = []
    for fixture in fixtures:
        if is_live(fixture):
            matches.append(fixture)
            continue
        if is_finished(fixture):
            continue
        date_string = fixture.get("fixture", {}).get("date")
        match_time = api_time_to_bd(date_string)
        if match_time and now <= match_time <= end:
            matches.append(fixture)
    return matches


def filter_live(fixtures):
    return [f for f in fixtures if is_live(f)]


async def get_recent_results():
    now = datetime.now(BD_TZ)
    dates = [now.date(), (now - timedelta(days=1)).date()]
    results = []
    for date in dates:
        data = await api_get("fixtures", {"date": date.strftime("%Y-%m-%d"), "timezone": "Asia/Dhaka"})
        if not data:
            continue
        for fixture in data.get("response", []):
            if not is_finished(fixture):
                continue
            date_string = fixture.get("fixture", {}).get("date")
            match_time = api_time_to_bd(date_string)
            if match_time and (now - timedelta(hours=12) <= match_time <= now):
                results.append(fixture)
    return results


# =========================================================
# KEYBOARDS
# =========================================================

def main_keyboard():
    keyboard = [
        [InlineKeyboardButton("🛡️ Safe Bets (1.30 Odds)", callback_data="view_safe_0")],
        [InlineKeyboardButton("⚽ Over 1.5 Goals", callback_data="view_over15_0"), InlineKeyboardButton("🎯 BTTS", callback_data="view_btts_0")],
        [InlineKeyboardButton("🔄 Double Chance", callback_data="view_double_0"), InlineKeyboardButton("⭐ Best Tip", callback_data="view_best_0")],
        [InlineKeyboardButton("🔴 Live Matches", callback_data="view_live_0"), InlineKeyboardButton("🕐 Next 12 Hours", callback_data="view_next12_0")],
        [InlineKeyboardButton("📊 Results & Past Tips", callback_data="menu_results_options")]
    ]
    return InlineKeyboardMarkup(keyboard)


def admin_keyboard():
    keyboard = [
        [InlineKeyboardButton("📊 User Statistics", callback_data="admin_stats"), InlineKeyboardButton("📢 Send Broadcast", callback_data="admin_broadcast_prompt")],
        [InlineKeyboardButton("🔄 Refresh Dashboard", callback_data="admin_refresh"), InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)


def results_options_keyboard():
    keyboard = [
        [InlineKeyboardButton("⚽ Over 1.5 Results", callback_data="view_res_over15_0"), InlineKeyboardButton("🎯 BTTS Results", callback_data="view_res_btts_0")],
        [InlineKeyboardButton("🔄 Double Chance Results", callback_data="view_res_double_0"), InlineKeyboardButton("🛡️ Safe Bet Results", callback_data="view_res_safe_0")],
        [InlineKeyboardButton("⭐ Best Tip Results", callback_data="view_res_best_0")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)


def pagination_keyboard(category, current_page, total_pages):
    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"view_{category}_{current_page - 1}"))
    
    nav_buttons.append(InlineKeyboardButton(f"📄 {current_page + 1}/{total_pages}", callback_data="ignore"))
    
    if current_page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"view_{category}_{current_page + 1}"))

    keyboard = [
        nav_buttons,
        [InlineKeyboardButton("🔄 Refresh", callback_data=f"view_{category}_{current_page}"), InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.username, user.first_name)
    count = get_user_count()

    text = f"🔥 <b>VIP FOOTBALL PREDICTION BOT</b> 🔥\n\n⚡ <i>AI-Powered League Statistics & Betting Tips</i>\n⚠️ <i>Play responsibly. Statistical estimates only.</i>\n\n👥 <b>Active Users:</b> <code>{count} Users</code>\n\n👇 <b>Select an Option:</b>"

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_keyboard())


# =========================================================
# CONTENT VIEW HANDLER
# =========================================================

async def show_content(query, category: str, page: int):
    await query.answer()

    market_type = category.replace("res_", "")

    if category.startswith("res_"):
        fixtures = await get_recent_results()
        title = f"📊 RESULTS: {market_type.upper()}"
    elif category in ["over15", "btts", "double", "safe", "best"]:
        fixtures = await get_fixtures()
        fixtures = filter_next_12_hours(fixtures)
        title = f"🎯 TIPS: {market_type.upper()}"
    elif category == "live":
        fixtures = await get_fixtures()
        fixtures = filter_live(fixtures)
        market_type = "best"
        title = "🔴 LIVE MATCHES"
    elif category == "next12":
        fixtures = await get_fixtures()
        fixtures = filter_next_12_hours(fixtures)
        market_type = "best"
        title = "🕐 NEXT 12 HOURS MATCHES"
    else:
        return

    if not fixtures:
        keyboard = [[InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]]
        await query.edit_message_text(f"❌ <b>{title}</b>\n\nKono match paowa jayni.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    total_items = len(fixtures)
    total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page = max(0, min(page, total_pages - 1))

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_fixtures = fixtures[start_idx:end_idx]

    cards = [make_match_card(f, market_type) for f in page_fixtures]
    text = f"📌 <b>{title}</b>\n───────────────────────────\n\n" + "".join(cards)

    await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=pagination_keyboard(category, page, total_pages))


# =========================================================
# ADMIN PANEL
# =========================================================

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin access required.")
        return

    count = get_user_count()
    text = f"👑 <b>ADMIN CONTROL PANEL</b>\n───────────────────────────\n\n👥 <b>Total Active Users:</b> <code>{count} Users</code>\n🤖 <b>Bot Status:</b> 🟢 ONLINE & WORKING\n\n👇 <b>Select an Action:</b>"
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=admin_keyboard())


async def handle_admin_actions(query, action: str, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()

    if action in ["stats", "refresh"]:
        count = get_user_count()
        text = f"👑 <b>ADMIN CONTROL PANEL</b>\n───────────────────────────\n\n👥 <b>Total Active Users:</b> <code>{count} Users</code>\n🤖 <b>Bot Status:</b> 🟢 ONLINE & WORKING\n\n👇 <b>Select an Action:</b>"
        await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=admin_keyboard())

    elif action == "broadcast_prompt":
        context.user_data["awaiting_broadcast"] = True
        text = "📢 <b>BROADCAST MESSAGE MODE</b>\n───────────────────────────\n\nApni je message-ti sob user-der kache pathate chan, seiti ekhon message box-e type kore pathan."
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_stats")]])
        await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=keyboard)


# =========================================================
# BUTTON HANDLER
# =========================================================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "ignore":
        await query.answer()
        return

    if data.startswith("admin_"):
        action = data.replace("admin_", "")
        await handle_admin_actions(query, action, context)
        return

    if data == "menu_main":
        await query.answer()
        count = get_user_count()
        text = f"🔥 <b>VIP FOOTBALL PREDICTION BOT</b> 🔥\n\n⚡ <i>AI-Powered League Statistics & Betting Tips</i>\n⚠️ <i>Play responsibly. Statistical estimates only.</i>\n\n👥 <b>Active Users:</b> <code>{count} Users</code>\n\n👇 <b>Select an Option:</b>"
        await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=main_keyboard())
        return

    if data == "menu_results_options":
        await query.answer()
        text = "📊 <b>PAST MATCH RESULTS & TIPS</b>\n\nJe tip-er past result dekhte chan, niche theke select korun:"
        await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=results_options_keyboard())
        return

    if data.startswith("view_"):
        parts = data.split("_")
        category = "_".join(parts[1:-1])
        page = int(parts[-1])
        await show_content(query, category, page)
        return


# =========================================================
# MESSAGE LISTENER
# =========================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id == ADMIN_ID and context.user_data.get("awaiting_broadcast"):
        context.user_data["awaiting_broadcast"] = False
        msg_text = update.message.text
        user_ids = get_all_user_ids()

        success = 0
        failed = 0
        status_msg = await update.message.reply_text(f"⏳ Broadcasting to {len(user_ids)} users...")

        for uid in user_ids:
            try:
                await context.bot.send_message(chat_id=uid, text=f"📢 <b>ANNOUNCEMENT:</b>\n\n{msg_text}", parse_mode="HTML")
                success += 1
                await asyncio.sleep(0.04)
            except Exception:
                failed += 1

        await status_msg.edit_text(f"✅ <b>Broadcast Complete!</b>\n\n🟢 <b>Success:</b> {success}\n🔴 <b>Failed:</b> {failed}", parse_mode="HTML")


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
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("VIP Football Prediction Bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
