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
# MATCH NAME
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


# =========================================================
# SCORE
# =========================================================

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

    # These are statistical/demo confidence values.
    # They are NOT guaranteed winning probabilities.

    if market == "over15":

        confidence = 74

        if home + away >= 2:
            result = "WIN"
        else:
            result = None

        name = "Over 1.5"

    elif market == "btts":

        confidence = 68

        if home >= 1 and away >= 1:
            result = "WIN"
        else:
            result = None

        name = "BTTS"

    elif market == "double":

        confidence = 77

        # Demo Double Chance: 1X
        if home >= away:
            result = "WIN"
        else:
            result = None

        name = "Double Chance 1X"

    elif market == "best":

        confidence = 80

        # Demo best tip = Over 1.5
        if home + away >= 2:
            result = "WIN"
        else:
            result = None

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

    status_data = fixture_data.get(
        "status",
        {}
    )

    status = status_data.get(
        "short",
        ""
    )

    match_name = get_match_name(
        fixture
    )

    home, away = get_score(
        fixture
    )

    name, confidence, result = (
        market_data(
            fixture,
            market
        )
    )

    # -------------------------
    # LIVE
    # -------------------------

    if is_live(fixture):

        minute = status_data.get(
            "elapsed"
        )

        if minute is None:
            minute = "?"

        return (
            f"🔴 {match_name} | "
            f"{minute}' | "
            f"{home}-{away} | "
            f"{name} | "
            f"{confidence}% | "
            f"LIVE"
        )

    # -------------------------
    # FINISHED
    # -------------------------

    if is_finished(fixture):

        result_text = (
            "✅ WIN"
            if result == "WIN"
            else "❌ LOSS"
        )

        return (
            f"🏁 {match_name} | "
            f"{home}-{away} | "
            f"{name} | "
            f"{confidence}% | "
            f"{result_text}"
        )

    # -------------------------
    # UPCOMING
    # -------------------------

    date_string = fixture_data.get(
        "date"
    )

    match_time = api_time_to_bd(
        date_string
    )

    if match_time:

        time_text = match_time.strftime(
            "%H:%M"
        )

        remaining = format_remaining(
            match_time
        )

    else:

        time_text = "--:--"
        remaining = "--:--:--"

    return (
        f"🕐 {match_name} | "
        f"{time_text} | "
        f"{name} | "
        f"{confidence}% | "
        f"{remaining}"
    )


# =========================================================
# FILTER NEXT 12 HOURS
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

        date_string = (
            fixture
            .get("fixture", {})
            .get("date")
        )

        match_time = api_time_to_bd(
            date_string
        )

        if not match_time:
            continue

        if now <= match_time <= end:

            matches.append(fixture)

    return matches


# =========================================================
# LIVE MATCHES
# =========================================================

def filter_live(fixtures):

    return [
        fixture
        for fixture in fixtures
        if is_live(fixture)
    ]


# =========================================================
# RECENT RESULTS
# =========================================================

async def get_recent_results():

    now = datetime.now(BD_TZ)

    dates = [
        now.date(),
        (now - timedelta(days=1)).date()
    ]

    results = []

    for date in dates:

        data = await api_get(
            "fixtures",
            {
                "date": date.strftime(
                    "%Y-%m-%d"
                ),
                "timezone": "Asia/Dhaka"
            }
        )

        if not data:
            continue

        for fixture in data.get(
            "response",
            []
        ):

            if not is_finished(
                fixture
            ):
                continue

            date_string = (
                fixture
                .get("fixture", {})
                .get("date")
            )

            match_time = api_time_to_bd(
                date_string
            )

            if not match_time:
                continue

            # Last 12 hours
            if (
                now - timedelta(hours=12)
                <= match_time
                <= now
            ):
                results.append(
                    fixture
                )

    return results


# =========================================================
# KEYBOARD
# =========================================================

def main_keyboard():

    keyboard = [

        [
            InlineKeyboardButton(
                "⚽ Over 1.5",
                callback_data="market_over15"
            ),

            InlineKeyboardButton(
                "🎯 BTTS",
                callback_data="market_btts"
            )
        ],

        [
            InlineKeyboardButton(
                "🔄 Double Chance",
                callback_data="market_double"
            ),

            InlineKeyboardButton(
                "⭐ Best Tip",
                callback_data="market_best"
            )
        ],

        [
            InlineKeyboardButton(
                "🔴 Live",
                callback_data="live"
            ),

            InlineKeyboardButton(
                "🕐 Next 12 Hours",
                callback_data="next12"
            )
        ],

        [
            InlineKeyboardButton(
                "📊 Results",
                callback_data="results"
            )
        ]

    ]

    return InlineKeyboardMarkup(
        keyboard
    )


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    add_user(
        user.id,
        user.username,
        user.first_name
    )

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
# MARKET
# =========================================================

async def show_market(
    query,
    market
):

    await query.answer()

    fixtures = await get_fixtures()

    fixtures = filter_next_12_hours(
        fixtures
    )

    if not fixtures:

        await query.edit_message_text(
            "❌ এই market-এর জন্য "
            "পরবর্তী ১২ ঘণ্টায় কোনো ম্যাচ পাওয়া যায়নি।",
            reply_markup=main_keyboard()
        )

        return

    lines = []

    for fixture in fixtures[:15]:

        lines.append(
            make_match_line(
                fixture,
                market
            )
        )

    text = (
        f"📊 {market_data(fixtures[0], market)[0]}\n\n"
        + "\n".join(lines)
        + "\n\n⚠️ Statistical estimate only."
    )

    await query.edit_message_text(
        text,
        reply_markup=main_keyboard()
    )


# =========================================================
# LIVE
# =========================================================

async def show_live(query):

    await query.answer()

    fixtures = await get_fixtures()

    live_matches = filter_live(
        fixtures
    )

    if not live_matches:

        await query.edit_message_text(
            "🔴 বর্তমানে কোনো live match পাওয়া যায়নি।",
            reply_markup=main_keyboard()
        )

        return

    lines = []

    for fixture in live_matches[:20]:

        lines.append(
            make_match_line(
                fixture,
                "best"
            )
        )

    text = (
        "🔴 LIVE MATCHES\n\n"
        + "\n".join(lines)
    )

    await query.edit_message_text(
        text,
        reply_markup=main_keyboard()
    )


# =========================================================
# NEXT 12 HOURS
# =========================================================

async def show_next12(query):

    await query.answer()

    fixtures = await get_fixtures()

    matches = filter_next_12_hours(
        fixtures
    )

    if not matches:

        await query.edit_message_text(
            "🕐 পরবর্তী ১২ ঘণ্টায় কোনো match পাওয়া যায়নি।",
            reply_markup=main_keyboard()
        )

        return

    lines = []

    for fixture in matches[:20]:

        lines.append(
            make_match_line(
                fixture,
                "best"
            )
        )

    text = (
        "🕐 NEXT 12 HOURS\n\n"
        + "\n".join(lines)
        + "\n\n⚠️ Tips are estimates."
    )

    await query.edit_message_text(
        text,
        reply_markup=main_keyboard()
    )


# =========================================================
# RESULTS
# =========================================================

async def show_results(query):

    await query.answer()

    fixtures = await get_recent_results()

    if not fixtures:

        await query.edit_message_text(
            "📊 গত ১২ ঘণ্টায় কোনো finished match পাওয়া যায়নি।",
            reply_markup=main_keyboard()
        )

        return

    lines = []

    for fixture in fixtures[:20]:

        lines.append(
            make_match_line(
                fixture,
                "best"
            )
        )

    text = (
        "📊 LAST 12 HOURS RESULTS\n\n"
        + "\n".join(lines)
    )

    await query.edit_message_text(
        text,
        reply_markup=main_keyboard()
    )


# =========================================================
# BUTTON HANDLER
# =========================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    data = query.data

    if data.startswith(
        "market_"
    ):

        market = data.replace(
            "market_",
            ""
        )

        await show_market(
            query,
            market
        )

        return

    if data == "live":

        await show_live(query)

        return

    if data == "next12":

        await show_next12(query)

        return

    if data == "results":

        await show_results(query)

        return


# =========================================================
# ADMIN
# =========================================================

async def admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if user.id != ADMIN_ID:

        await update.message.reply_text(
            "❌ Admin only."
        )

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

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    print(
        "ERROR:",
        context.error
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN environment variable missing."
        )

    if (
        not API_FOOTBALL_KEY
        or API_FOOTBALL_KEY
        == "PASTE_API_KEY_HERE"
    ):

        raise RuntimeError(
            "API_FOOTBALL_KEY is not configured."
        )

    init_db()

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "admin",
            admin
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    app.add_error_handler(
        error_handler
    )

    print(
        "Football Analysis Bot started..."
    )

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":

    main()
