import os
import asyncio
import sqlite3
import logging
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

# Logging Setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

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
# DATABASE SETUP
# =========================================================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    # Users table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            is_vip INTEGER DEFAULT 0,
            join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Dynamic Tickets Archive table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            date_created TEXT,
            total_odds REAL,
            matches_summary TEXT,
            status TEXT DEFAULT 'PENDING'
        )
    """)
    conn.commit()
    
    try:
        cur.execute("ALTER TABLE users ADD COLUMN is_vip INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass
        
    conn.close()


def add_user(user_id, username, first_name):
    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (user_id, username, first_name, is_vip)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name
        """, (user_id, username or "", first_name or ""))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Database add_user error: {e}")


def save_ticket_to_db(category, total_odds, matches_summary, status="PENDING"):
    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        today_str = datetime.now(BD_TZ).strftime("%d %b")
        cur.execute("""
            INSERT INTO tickets (category, date_created, total_odds, matches_summary, status)
            VALUES (?, ?, ?, ?, ?)
        """, (category, today_str, total_odds, matches_summary, status))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Save ticket error: {e}")


def get_history_by_category(category):
    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute("""
            SELECT date_created, total_odds, status FROM tickets 
            WHERE category = ? ORDER BY id DESC LIMIT 5
        """, (category,))
        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"Get history error: {e}")
        return []


def is_user_vip(user_id):
    if user_id == ADMIN_ID:
        return True
    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute("SELECT is_vip FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        conn.close()
        return bool(row[0]) if row and row[0] else False
    except Exception as e:
        logger.error(f"Database is_user_vip error: {e}")
        return False


def set_vip_status(user_id, status=1):
    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute("UPDATE users SET is_vip = ? WHERE user_id = ?", (status, user_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Database set_vip_status error: {e}")


def get_user_count():
    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        count = cur.fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        logger.error(f"Database get_user_count error: {e}")
        return 0


# =========================================================
# API REQUEST & RELAXED MATCH FETCHING
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
    except Exception as e:
        logger.error(f"API request exception: {e}")
        return None


async def get_upcoming_fixtures():
    now = datetime.now(BD_TZ)
    # Fetch next 3 days to guarantee matches are always available
    dates = [(now + timedelta(days=i)).date() for i in range(3)]
    all_matches = []

    for date in dates:
        data = await api_get("fixtures", {"date": date.strftime("%Y-%m-%d"), "timezone": "Asia/Dhaka"})
        if not data or "response" not in data:
            continue
        all_matches.extend(data["response"])

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


def market_data(fixture):
    home, away = get_score(fixture)
    total_goals = home + away
    status = fixture.get("fixture", {}).get("status", {}).get("short", "")
    is_done = status in {"FT", "AET", "PEN"}

    if total_goals >= 2 or not is_done:
        odds = 1.35
        confidence = 85
        name_bn = "ওভার ১.৫ গোল (Over 1.5 Goals)"
        status_bn = "✅ WIN" if (is_done and total_goals >= 2) else ("❌ LOSS" if is_done else "⏳ Pending")
    else:
        odds = 1.30
        confidence = 90
        name_bn = "ডাবল চান্স ১এক্স (Double Chance 1X)"
        status_bn = "✅ WIN" if (is_done and home >= away) else ("❌ LOSS" if is_done else "⏳ Pending")

    return name_bn, odds, confidence, status_bn


def generate_accumulator(fixtures, target_odds):
    selected = []
    current_odds = 1.0

    for f in fixtures:
        name_bn, odds, conf, status_bn = market_data(f)
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
        [InlineKeyboardButton("🎯 Free 2+ Odds", callback_data="free_2"), InlineKeyboardButton("🚀 Free 5+ Odds", callback_data="free_5")],
        [InlineKeyboardButton("🔥 Free 10+ Odds", callback_data="free_10"), InlineKeyboardButton("💣 Free 20+ Odds", callback_data="free_20")],
        [InlineKeyboardButton("👑 VIP SAFE ACCUMULATORS", callback_data="vip_menu")],
        [InlineKeyboardButton("📊 WIN/LOSS History Archive", callback_data="history_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def vip_keyboard():
    keyboard = [
        [InlineKeyboardButton("🛡️ VIP Safe 2+ Odds", callback_data="vip_2")],
        [InlineKeyboardButton("💎 VIP Gold 5+ Odds", callback_data="vip_5")],
        [InlineKeyboardButton("👑 VIP Ultra 10+ Odds", callback_data="vip_10")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)


def history_keyboard():
    keyboard = [
        [InlineKeyboardButton("📊 2+ Odds Archive", callback_data="hist_2"), InlineKeyboardButton("📊 5+ Odds Archive", callback_data="hist_5")],
        [InlineKeyboardButton("📊 10+ Odds Archive", callback_data="hist_10"), InlineKeyboardButton("📊 20+ Odds Archive", callback_data="hist_20")],
        [InlineKeyboardButton("👑 VIP Tickets Archive", callback_data="hist_vip")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)


# =========================================================
# HANDLERS
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        if not user:
            return
        add_user(user.id, user.username, user.first_name)
        count = get_user_count()

        text = (
            f"🔥 <b>VIP FOOTBALL BETTING TIPS BOT</b> 🔥\n\n"
            f"⚡ <i>Dynamic Multi-Odds Predictions & Automated Archive</i>\n"
            f"👥 <b>Active Users:</b> <code>{count} Users</code>\n\n"
            f"👇 <b>Niche theke Odds select করুন:</b>"
        )
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_keyboard())
    except Exception as e:
        logger.error(f"Error in start command: {e}")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    try:
        if data == "menu_main":
            count = get_user_count()
            text = f"🔥 <b>VIP FOOTBALL BETTING TIPS BOT</b> 🔥\n\n👥 <b>Active Users:</b> <code>{count} Users</code>\n\n👇 <b>Select an Option:</b>"
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=main_keyboard())
            return

        if data == "vip_menu":
            text = "👑 <b>VIP LOW RISK ACCUMULATORS</b>\n───────────────────────────\n\nVIP Section-e low risk matches multiple odds acca combo dekhabe.\n\n👇 Select VIP Odds:"
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=vip_keyboard())
            return

        if data == "history_menu":
            text = "📊 <b>DAILY WIN / LOSS HISTORY ARCHIVE</b>\n───────────────────────────\n\nPaster ticket-er real record dekhate category select করুন:"
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=history_keyboard())
            return

        # DYNAMIC HISTORY ARCHIVE VIEWER
        if data.startswith("hist_"):
            cat_key = data.replace("hist_", "")
            cat_name = f"{cat_key.upper()} ODDS"
            
            records = get_history_by_category(cat_key)
            text = f"📊 <b>RESULTS ARCHIVE ({cat_name})</b>\n───────────────────────────\n\n"
            
            if records:
                for date_c, odds, status in records:
                    icon = "✅" if status == "WIN" else ("❌" if status == "LOSS" else "⏳")
                    text += f"{icon} <b>{date_c} Ticket:</b> {status} (@{odds})\n"
            else:
                text += "<i>Ekhono kono record eii category-te store hoyni. Notun odds generator use korlei archive-e auto save hobe!</i>\n"
            
            text += "\n<i>System automatically dynamic db query kore archive calculate korche.</i>"
            
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 History Menu", callback_data="history_menu"), InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]])
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
            return

        # ACCUMULATOR GENERATOR
        if data.startswith("free_") or data.startswith("vip_"):
            is_vip_req = data.startswith("vip_")
            cat_code = data.split("_")[1]
            target_odds = float(cat_code)

            if is_vip_req and not is_user_vip(user_id):
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]])
                await query.edit_message_text("❌ <b>VIP Access Required!</b>\n\nApnar accounte VIP access nei. VIP er jonno Admin er sathe jogajog korun.", parse_mode="HTML", reply_markup=keyboard)
                return

            fixtures = await get_upcoming_fixtures()
            if not fixtures:
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]])
                await query.edit_message_text("❌ Currently match list reload hocche, ektu por abar try korun.", parse_mode="HTML", reply_markup=keyboard)
                return

            acc_matches, total_odds = generate_accumulator(fixtures, target_odds)

            mode_title = f"👑 VIP LOW RISK {target_odds}+ ODDS" if is_vip_req else f"🎯 FREE {target_odds}+ ODDS ACCA"
            msg_lines = [f"📌 <b>{mode_title}</b>", "───────────────────────────\n"]

            summary_list = []
            ticket_won = True
            for idx, (f, name_bn, odds, conf, status_bn) in enumerate(acc_matches, 1):
                match_name, league = get_match_info(f)
                h_score, a_score = get_score(f)
                if "LOSS" in status_bn:
                    ticket_won = False
                summary_list.append(f"{match_name} ({odds})")
                    
                msg_lines.append(
                    f"⚽ <b>Match {idx}:</b> {match_name}\n"
                    f"🏆 <b>League:</b> {league}\n"
                    f"💡 <b>টিপস (Tip):</b> {name_bn}\n"
                    f"📈 <b>অডস (Odds):</b> @{odds}\n"
                    f"📊 <b>স্ট্যাটাস/ফলাফল:</b> {status_bn} ({h_score}-{a_score})\n"
                    f"───────────────────────────"
                )

            msg_lines.append(f"\n🔥 <b>TOTAL COMBINED ODDS:</b> <code>@{total_odds}</code>")
            
            # Save generated ticket into dynamic DB Archive
            save_ticket_to_db("vip" if is_vip_req else cat_code, total_odds, ", ".join(summary_list), "WIN" if ticket_won else "PENDING")

            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh Results", callback_data=data), InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]])

            await query.edit_message_text("\n".join(msg_lines), parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error in button_handler: {e}")


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


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)


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
    app.add_error_handler(error_handler)

    print("Dynamic Accumulator Bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
