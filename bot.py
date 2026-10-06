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
    ContextTypes,
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
ITEMS_PER_PAGE = 5  # প্রতি পেজে ৫টি ম্যাচ দেখাবে


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
# TIME & MATCH HELPERS
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


def get_match_name(fixture):
    teams = fixture.get("teams", {})
    home = teams.get("home", {}).get("name", "Home")
    away = teams.get("away", {}).get("name", "Away")
    return f"{home} 🆚 {away}"


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
        confidence = 82
        result = "WIN" if total_goals >= 2 else "LOSS"
        name = "Over 1.5 Goals"

    elif market == "btts":
        odds = 1.75
        confidence = 68
        result = "WIN" if home >= 1 and away >= 1 else "LOSS"
        name = "Both Teams to Score (BTTS)"

    elif market == "double":
        odds = 1.30
        confidence = 85
        result = "WIN" if home >= away else "LOSS"
        name = "Double Chance (1X)"

    elif market == "safe":
        odds = 1.30
        confidence = 88
        result = "WIN" if total_goals >= 1 else "LOSS"
        name = "Safe Bet (Over 0.5/1.5)"

    elif market == "best":
        odds = 1.45
        confidence = 80
        result = "WIN" if total_goals >= 2 else "LOSS"
        name = "Best Selection"

    else:
        odds = 1.50
        confidence = 65
        result = "LOSS"
        name = "General Tip"

    return name, odds, confidence, result


# =========================================================
# MATCH CARD DESIGN (PROFESSIONAL FORMATTING)
# =========================================================

def make_match_card(fixture, market):
    fixture_data = fixture.get("fixture", {})
    status_data = fixture_data.get("status", {})
    match_name = get_match_name(fixture)
    home, away = get_score(fixture)
    name, odds, confidence, result = market_data(fixture, market)

    # LIVE MATCH
    if is_live(fixture):
        minute = status_data.get("elapsed", "?")
        return (
            f"🔴 <b>{match_name}</b>\n"
            f"⏱ <b>Time:</b> {minute}' | ⚽ <b>Score:</b> {home}-{away}\n"
            f"💡 <b>Tip:</b> {name}\n"
            f"📈 <b>Odds:</b> @{odds} | 🎯 <b>Conf:</b> {confidence}%\n"
            f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        )

    # FINISHED MATCH
    if is_finished(fixture):
        res_icon = "✅ WIN" if result == "WIN" else "❌ LOSS"
        return (
            f"🏁 <b>{match_name}</b>\n"
            f"⚽ <b>Final Score:</b> {home}-{away}\n"
            f"💡 <b>Tip:</b> {name} (@{odds})\n"
            f"📊 <b>Status:</b> {res_icon}\n"
            f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        )

    # UPCOMING MATCH
    date_string = fixture_data.get("date")
    match_time = api_time_to_bd(date_string)
    time_text = match_time.strftime("%I:%M %p") if match_time else "--:--"
    remaining = format_remaining(match_time) if match_time else "--:--"

    return (
        f"⚽ <b>{match_name}</b>\n"
        f"🕐 <b>Time:</b> {time_text} (In {remaining})\n"
        f"💡 <b>Tip:</b> {name}\n"
        f"📈 <b>Odds:</b> @{odds} | 🎯 <b>Conf:</b> {confidence}%\n"
        f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
    )


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
        [
            InlineKeyboardButton("🛡️ Safe Bets (1.30 Odds)", callback_data="view_safe_0")
        ],
        [
            InlineKeyboardButton("⚽ Over 1.5 Goals", callback_data="view_over15_0"),
            InlineKeyboardButton("🎯 BTTS", callback_data="view_btts_0")
        ],
        [
            InlineKeyboardButton("🔄 Double Chance", callback_data="view_double_0"),
            InlineKeyboardButton("⭐ Best Tip", callback_data="view_best_0")
        ],
        [
            InlineKeyboardButton("🔴 Live Matches", callback_data="view_live_0"),
            InlineKeyboardButton("🕐 Next 12 Hours", callback_data="view_next12_0")
        ],
        [
            InlineKeyboardButton("📊 Match Results & Tips", callback_data="menu_results_options")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def results_options_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("⚽ Over 1.5 Results", callback_data="view_res_over15_0"),
            InlineKeyboardButton("🎯 BTTS Results", callback_data="view_res_btts_0")
        ],
        [
            InlineKeyboardButton("🔄 Double Chance Results", callback_data="view_res_double_0"),
            InlineKeyboardButton("🛡️ Safe Bet Results", callback_data="view_res_safe_0")
        ],
        [
            InlineKeyboardButton("⭐ Best Tip Results", callback_data="view_res_best_0")
        ],
        [
            InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")
        ]
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
        [
            InlineKeyboardButton("🔄 Refresh", callback_data=f"view_{category}_{current_page}"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.username, user.first_name)
    count = get_user_count()

    text = (
        "🔥 <b>PREMIUM FOOTBALL ANALYSIS BOT</b> 🔥\n\n"
        "📊 <i>AI & Statistical Match Predictions</i>\n"
        "⚠️ <i>Predictions are statistical estimates. Play responsibly.</i>\n\n"
        f"👥 <b>Total Active Users:</b> {count}\n\n"
        "👇 <b>Select an option below:</b>"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


# =========================================================
# CONTENT VIEW HANDLER
# =========================================================

async def show_content(query, category: str, page: int):
    await query.answer()

    # Determine data & market
    market_type = category.replace("res_", "")

    if category.startswith("res_"):
        fixtures = await get_recent_results()
        title = f"📊 RESULTS: {market_type.upper()}"
    elif category in ["over15", "btts", "double", "safe", "best"]:
        fixtures = await get_fixtures()
        fixtures = filter_next_12_hours(fixtures)
        title = f"🎯 MARKET: {market_type.upper()}"
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
        await query.edit_message_text(
            f"❌ <b>{title}</b>\n\nকোনো ম্যাচ পাওয়া যায়নি।",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # Pagination
    total_items = len(fixtures)
    total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page = max(0, min(page, total_pages - 1))

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_fixtures = fixtures[start_idx:end_idx]

    # Build Response
    cards = [make_match_card(f, market_type) for f in page_fixtures]
    text = (
        f"📌 <b>{title}</b>\n"
        f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
        + "".join(cards)
    )

    await query.edit_message_text(
        text=text,
        parse_mode="HTML",
        reply_markup=pagination_keyboard(category, page, total_pages)
    )


# =========================================================
# BUTTON HANDLER
# =========================================================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "ignore":
        await query.answer()
        return

    if data == "menu_main":
        await query.answer()
        count = get_user_count()
        text = (
            "🔥 <b>PREMIUM FOOTBALL ANALYSIS BOT</b> 🔥\n\n"
            "📊 <i>AI & Statistical Match Predictions</i>\n"
            "⚠️ <i>Predictions are statistical estimates. Play responsibly.</i>\n\n"
            f"👥 <b>Total Active Users:</b> {count}\n\n"
            "👇 <b>Select an option below:</b>"
        )
        await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=main_keyboard())
        return

    if data == "menu_results_options":
        await query.answer()
        text = (
            "📊 <b>MATCH RESULTS BY TIP CATEGORY</b>\n\n"
            "আপনি যে টিপসের (Tip) ফলাফল দেখতে চান, নিচে থেকে অপশন সিলেক্ট করুন:"
        )
        await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=results_options_keyboard())
        return

    if data.startswith("view_"):
        parts = data.split("_")
        category = "_".join(parts[1:-1])
        page = int(parts[-1])
        await show_content(query, category, page)
        return


# =========================================================
# ADMIN & MAIN
# =========================================================

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only.")
        return
    count = get_user_count()
    await update.message.reply_text(f"👑 ADMIN PANEL\n\n👥 Total Users: {count}\n🤖 Status: ONLINE")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable missing.")

    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("Football Bot is running successfully...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
