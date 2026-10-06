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

# API-Football key
API_FOOTBALL_KEY = "56b9dc74cb0fa6f847fe07869eeb5d65"

ADMIN_ID = 5293614793

API_URL = "https://v3.football.api-sports.io"

# Bangladesh timezone
BD_TZ = timezone(timedelta(hours=6))

DB_FILE = "bot_users.db"
ITEMS_PER_PAGE = 5  # প্রতি পেজে কয়টি ম্যাচ দেখাবে


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
        INSERT OR IGNORE INTO users
        (user_id, username, first_name)
        VALUES (?, ?, ?)
    """, (
        user_id,
        username or "",
        first_name or ""
    ))

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

    headers = {
        "x-apisports-key": API_FOOTBALL_KEY
    }

    url = f"{API_URL}/{endpoint}"

    try:

        timeout = aiohttp.ClientTimeout(total=20)

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.get(
                url,
                headers=headers,
                params=params or {}
            ) as response:

                if response.status != 200:
                    print(
                        "API Error:",
                        response.status
                    )
                    return None

                return await response.json()

    except Exception as e:

        print("API Exception:", e)

        return None


# =========================================================
# TIME
# =========================================================

def api_time_to_bd(date_string):

    try:

        dt = datetime.fromisoformat(
            date_string.replace("Z", "+00:00")
        )

        return dt.astimezone(BD_TZ)

    except Exception:

        return None


def format_remaining(target):

    now = datetime.now(BD_TZ)

    seconds = int(
        (target - now).total_seconds()
    )

    if seconds <= 0:
        return "STARTED"

    hours = seconds // 3600

    minutes = (seconds % 3600) // 60

    secs = seconds % 60

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{secs:02d}"
    )


# =========================================================
# FIXTURE DATA
# =========================================================

async def get_fixtures():

    now = datetime.now(BD_TZ)

    dates = [
        now.date(),
        (now + timedelta(days=1)).date()
    ]

    all_matches = []

    for date in dates:

        data = await api_get(
            "fixtures",
            {
                "date": date.strftime("%Y-%m-%d"),
                "timezone": "Asia/Dhaka"
            }
        )

        if not data:
            continue

        response = data.get("response", [])

        all_matches.extend(response)

    return all_matches


# =========================================================
# STATUS
# =========================================================

LIVE_STATUSES = {
    "1H",
    "2H",
    "HT",
    "ET",
    "P",
    "LIVE"
}


def is_live(fixture):

    status = (
        fixture
        .get("fixture", {})
        .get("status", {})
        .get("short", "")
    )

    return status in LIVE_STATUSES


def is_finished(fixture):

    status = (
        fixture
        .get("fixture", {})
        .get("status", {})
        .get("short", "")
    )

    return status in {
        "FT",
        "AET",
        "PEN"
    }


# =========================================================
# MATCH NAME & SCORE
# =========================================================

def get_team_name(team):
    return team.get("name", "Unknown")


def get_match_name(fixture):

    teams = fixture.get("teams", {})

    home = get_team_name(
        teams.get("home", {})
    )

    away = get_team_name(
        teams.get("away", {})
    )

    return f"{home} vs {away}"


def get_score(fixture):

    goals = fixture.get("goals", {})

    home = goals.get("home")
    away = goals.get("away")

    if home is None:
        home = 0

    if away is None:
        away = 0

    return home, away


# =========================================================
# DEMO MARKET ANALYSIS
# =========================================================

def market_data(fixture, market):

    home, away = get_score(fixture)

    if market == "over15":
        confidence = 74
        result = "WIN" if home + away >= 2 else None
        name = "Over 1.5"

    elif market == "btts":
        confidence = 68
        result = "WIN" if home >= 1 and away >= 1 else None
        name = "BTTS"

    elif market == "double":
        confidence = 77
        result = "WIN" if home >= away else None
        name = "Double Chance 1X"

    elif market == "best":
        confidence = 80
        result = "WIN" if home + away >= 2 else None
        name = "Best Tip"

    else:
        confidence = 65
        result = None
        name = "Unknown"

    return name, confidence, result


# =========================================================
# MATCH LINE
# =========================================================

def make_match_line(fixture, market):

    fixture_data = fixture.get("fixture", {})
    status_data = fixture_data.get("status", {})
    match_name = get_match_name(fixture)
    home, away = get_score(fixture)
    name, confidence, result = market_data(fixture, market)

    if is_live(fixture):
        minute = status_data.get("elapsed", "?")
        return (
            f"🔴 {match_name}\n"
            f"⏱ {minute}' | ⚽ {home}-{away} | {name} ({confidence}%)\n"
        )

    if is_finished(fixture):
        result_text = "✅ WIN" if result == "WIN" else "❌ LOSS"
        return (
            f"🏁 {match_name}\n"
            f"⚽ {home}-{away} | {name} ({confidence}%) | {result_text}\n"
        )

    date_string = fixture_data.get("date")
    match_time = api_time_to_bd(date_string)

    if match_time:
        time_text = match_time.strftime("%H:%M")
        remaining = format_remaining(match_time)
    else:
        time_text = "--:--"
        remaining = "--:--:--"

    return (
        f"🕐 {match_name}\n"
        f"⏰ {time_text} | {name} ({confidence}%) | ⏳ {remaining}\n"
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

        if not match_time:
            continue

        if now <= match_time <= end:
            matches.append(fixture)

    return matches


def filter_live(fixtures):
    return [fixture for fixture in fixtures if is_live(fixture)]


async def get_recent_results():
    now = datetime.now(BD_TZ)
    dates = [now.date(), (now - timedelta(days=1)).date()]
    results = []

    for date in dates:
        data = await api_get(
            "fixtures",
            {"date": date.strftime("%Y-%m-%d"), "timezone": "Asia/Dhaka"}
        )

        if not data:
            continue

        for fixture in data.get("response", []):
            if not is_finished(fixture):
                continue

            date_string = fixture.get("fixture", {}).get("date")
            match_time = api_time_to_bd(date_string)

            if not match_time:
                continue

            if now - timedelta(hours=12) <= match_time <= now:
                results.append(fixture)

    return results


# =========================================================
# KEYBOARDS
# =========================================================

def main_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("⚽ Over 1.5", callback_data="view_over15_0"),
            InlineKeyboardButton("🎯 BTTS", callback_data="view_btts_0")
        ],
        [
            InlineKeyboardButton("🔄 Double Chance", callback_data="view_double_0"),
            InlineKeyboardButton("⭐ Best Tip", callback_data="view_best_0")
        ],
        [
            InlineKeyboardButton("🔴 Live", callback_data="view_live_0"),
            InlineKeyboardButton("🕐 Next 12 Hours", callback_data="view_next12_0")
        ],
        [
            InlineKeyboardButton("📊 Results", callback_data="view_results_0")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def pagination_keyboard(category, current_page, total_pages):
    nav_buttons = []

    # আগের পেজ (⬅️ Prev)
    if current_page > 0:
        nav_buttons.append(
            InlineKeyboardButton("⬅️ Prev", callback_data=f"view_{category}_{current_page - 1}")
        )
    
    # পেজ নম্বর কাউন্টার
    nav_buttons.append(
        InlineKeyboardButton(f"📄 {current_page + 1}/{total_pages}", callback_data="ignore")
    )

    # পরের পেজ (Next ➡️)
    if current_page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton("Next ➡️", callback_data=f"view_{category}_{current_page + 1}")
        )

    keyboard = [
        nav_buttons,
        [
            InlineKeyboardButton("🔄 Refresh", callback_data=f"view_{category}_{current_page}"),
            InlineKeyboardButton("🔙 Main Menu", callback_data="menu_main")
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
        "⚽ FOOTBALL ANALYSIS BOT\n\n"
        "📊 Statistical match analysis\n"
        "⚠️ Tips are estimates, not guaranteed wins.\n\n"
        f"👥 Total Users: {count}\n\n"
        "👇 Select a market:"
    )

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard()
    )


# =========================================================
# VIEW LIST HANDLER (WITH PAGINATION)
# =========================================================

async def show_content(query, category: str, page: int):
    await query.answer()

    # ১. ক্যাটাগরি অনুযায়ী ডেটা ফেচ
    if category in ["over15", "btts", "double", "best"]:
        fixtures = await get_fixtures()
        fixtures = filter_next_12_hours(fixtures)
        market_type = category
        title = f"📊 {category.upper()} MARKET"
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
    elif category == "results":
        fixtures = await get_recent_results()
        market_type = "best"
        title = "📊 LAST 12 HOURS RESULTS"
    else:
        return

    if not fixtures:
        await query.edit_message_text(
            f"❌ **{title}**-এর জন্য কোনো তথ্য পাওয়া যায়নি।",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Main Menu", callback_data="menu_main")
            ]])
        )
        return

    # ২. পেজিনেশন হিসেব
    total_items = len(fixtures)
    total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE

    if page >= total_pages:
        page = total_pages - 1
    if page < 0:
        page = 0

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_fixtures = fixtures[start_idx:end_idx]

    # ৩. মেসেজ তৈরি
    lines = []
    for fixture in page_fixtures:
        lines.append(make_match_line(fixture, market_type))

    text = (
        f"{title}\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        + "\n".join(lines)
        + f"\n⚠️ Statistical estimates only."
    )

    # ৪. মেসেজ আপডেট ও বাটন যুক্ত করা
    await query.edit_message_text(
        text=text,
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
            "⚽ FOOTBALL ANALYSIS BOT\n\n"
            "📊 Statistical match analysis\n"
            "⚠️ Tips are estimates, not guaranteed wins.\n\n"
            f"👥 Total Users: {count}\n\n"
            "👇 Select a market:"
        )
        await query.edit_message_text(
            text=text,
            reply_markup=main_keyboard()
        )
        return

    if data.startswith("view_"):
        parts = data.split("_")
        category = parts[1]
        page = int(parts[2]) if len(parts) > 2 else 0
        await show_content(query, category, page)
        return


# =========================================================
# ADMIN
# =========================================================

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only.")
        return

    count = get_user_count()

    await update.message.reply_text(
        "👑 ADMIN PANEL\n\n"
        f"👥 Total Users: {count}\n"
        "🤖 Bot Status: ONLINE"
    )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print("ERROR:", context.error)


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable missing.")

    if not API_FOOTBALL_KEY or API_FOOTBALL_KEY == "PASTE_API_KEY_HERE":
        raise RuntimeError("API_FOOTBALL_KEY is not configured.")

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)

    print("Football Analysis Bot started...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
