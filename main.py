
import os
import json
import math
import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes
)

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "5293614793"))
TIMEZONE = os.getenv("TIMEZONE", "Asia/Dhaka")
API_BASE = "https://v3.football.api-sports.io"
API_DAILY_BUDGET = int(os.getenv("API_DAILY_BUDGET", "85"))
DB_PATH = os.getenv("DB_PATH", "football_bot.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("FootballAIPro")

# Simple in-memory cache and daily request counter
CACHE = {}
API_CALLS = 0
API_DAY = datetime.now(timezone.utc).date()


# ================= DATABASE =================
def db():
    return sqlite3.connect(DB_PATH)


def init_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                is_vip INTEGER DEFAULT 0,
                joined_at TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS vip_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                details TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fixture_id INTEGER,
                match_name TEXT,
                prediction TEXT,
                created_at TEXT
            )
        """)


def register_user(user):
    with db() as con:
        con.execute("""
            INSERT INTO users(user_id, username, joined_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username
        """, (
            user.id, user.username,
            datetime.now(timezone.utc).isoformat()
        ))


def is_vip(user_id):
    if user_id == ADMIN_ID:
        return True
    with db() as con:
        row = con.execute(
            "SELECT is_vip FROM users WHERE user_id=?",
            (user_id,)
        ).fetchone()
    return bool(row and row[0])


def add_vip_request(user_id, details):
    with db() as con:
        cur = con.execute("""
            INSERT INTO vip_requests(user_id, details, created_at)
            VALUES (?, ?, ?)
        """, (
            user_id, details,
            datetime.now(timezone.utc).isoformat()
        ))
        return cur.lastrowid


def pending_requests():
    with db() as con:
        return con.execute("""
            SELECT id, user_id, details
            FROM vip_requests WHERE status='pending'
            ORDER BY id ASC LIMIT 20
        """).fetchall()


def approve_request(request_id):
    with db() as con:
        row = con.execute("""
            SELECT user_id FROM vip_requests
            WHERE id=? AND status='pending'
        """, (request_id,)).fetchone()
        if not row:
            return None
        user_id = row[0]
        con.execute(
            "UPDATE users SET is_vip=1 WHERE user_id=?",
            (user_id,)
        )
        con.execute(
            "UPDATE vip_requests SET status='approved' WHERE id=?",
            (request_id,)
        )
        return user_id


def reject_request(request_id):
    with db() as con:
        row = con.execute("""
            SELECT user_id FROM vip_requests
            WHERE id=? AND status='pending'
        """, (request_id,)).fetchone()
        if not row:
            return None
        con.execute(
            "UPDATE vip_requests SET status='rejected' WHERE id=?",
            (request_id,)
        )
        return row[0]


def save_prediction(fixture_id, match_name, prediction):
    with db() as con:
        con.execute("""
            INSERT INTO predictions
            (fixture_id, match_name, prediction, created_at)
            VALUES (?, ?, ?, ?)
        """, (
            fixture_id, match_name, prediction,
            datetime.now(timezone.utc).isoformat()
        ))


def get_history():
    with db() as con:
        return con.execute("""
            SELECT match_name, prediction, created_at
            FROM predictions ORDER BY id DESC LIMIT 10
        """).fetchall()


# ================= FOOTBALL API =================
async def api_get(endpoint, params=None, cache_seconds=0):
    global API_CALLS, API_DAY

    if not FOOTBALL_API_KEY:
        raise RuntimeError("FOOTBALL_API_KEY সেট করা হয়নি।")

    today = datetime.now(timezone.utc).date()
    if today != API_DAY:
        API_DAY = today
        API_CALLS = 0

    cache_key = (endpoint, json.dumps(params or {}, sort_keys=True))
    if cache_seconds and cache_key in CACHE:
        saved_at, saved_data = CACHE[cache_key]
        if datetime.now(timezone.utc).timestamp() - saved_at < cache_seconds:
            return saved_data

    if API_CALLS >= API_DAILY_BUDGET:
        raise RuntimeError("বটের দৈনিক API বাজেট শেষ। পরে আবার চেষ্টা করো।")

    headers = {"x-apisports-key": FOOTBALL_API_KEY}
    timeout = aiohttp.ClientTimeout(total=20)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            f"{API_BASE}/{endpoint}",
            headers=headers,
            params=params or {}
        ) as response:
            API_CALLS += 1
            if response.status != 200:
                raise RuntimeError(f"Football API HTTP {response.status}")
            data = await response.json()

    errors = data.get("errors")
    if errors:
        raise RuntimeError(f"Football API error: {errors}")

    CACHE[cache_key] = (
        datetime.now(timezone.utc).timestamp(), data
    )
    return data


async def upcoming_matches():
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    end = now + timedelta(hours=12)

    # API-Football fixtures are requested by local date.
    dates = {now.date().isoformat(), end.date().isoformat()}
    matches = {}

    for date in sorted(dates):
        data = await api_get(
            "fixtures",
            {"date": date, "timezone": TIMEZONE},
            cache_seconds=300
        )
        for item in data.get("response", []):
            fixture = item.get("fixture", {})
            status = fixture.get("status", {}).get("short", "")
            if status not in ("NS", "TBD"):
                continue

            try:
                kickoff = datetime.fromisoformat(
                    fixture["date"].replace("Z", "+00:00")
                ).astimezone(tz)
            except (KeyError, ValueError):
                continue

            if now <= kickoff <= end:
                matches[fixture["id"]] = item

    return sorted(
        matches.values(),
        key=lambda m: m["fixture"]["date"]
    )


async def team_form(team_id):
    data = await api_get(
        "fixtures",
        {"team": team_id, "last": 5, "timezone": TIMEZONE},
        cache_seconds=3600
    )
    goals_for, goals_against, count = 0, 0, 0

    for item in data.get("response", []):
        status = item.get("fixture", {}).get("status", {}).get("short")
        if status not in ("FT", "AET", "PEN"):
            continue

        teams = item.get("teams", {})
        goals = item.get("goals", {})
        home_id = teams.get("home", {}).get("id")
        team_goals = goals.get("home") if home_id == team_id else goals.get("away")
        opponent_goals = goals.get("away") if home_id == team_id else goals.get("home")

        if team_goals is None or opponent_goals is None:
            continue

        goals_for += team_goals
        goals_against += opponent_goals
        count += 1

    if count < 3:
        return None

    return {
        "scored": goals_for / count,
        "conceded": goals_against / count,
        "matches": count
    }


# ================= PREDICTIONS =================
def poisson_probability(lam, goals):
    return (math.exp(-lam) * lam ** goals) / math.factorial(goals)


def make_prediction(home_form, away_form):
    # A simple recent-form baseline, not a trained prediction model.
    home_xg = max(
        0.2, min(4.0, (home_form["scored"] + away_form["conceded"]) / 2)
    )
    away_xg = max(
        0.2, min(4.0, (away_form["scored"] + home_form["conceded"]) / 2)
    )

    home_probs = [poisson_probability(home_xg, g) for g in range(8)]
    away_probs = [poisson_probability(away_xg, g) for g in range(8)]

    home_win = draw = away_win = over25 = btts = 0.0

    for h, hp in enumerate(home_probs):
        for a, ap in enumerate(away_probs):
            p = hp * ap
            if h > a:
                home_win += p
            elif h == a:
                draw += p
            else:
                away_win += p
            if h + a >= 3:
                over25 += p
            if h > 0 and a > 0:
                btts += p

    result = max(
        [("1", home_win), ("X", draw), ("2", away_win)],
        key=lambda x: x[1]
    )[0]

    double_chance = {
        "1": ("1X", home_win + draw),
        "X": ("12", home_win + away_win),
        "2": ("X2", draw + away_win)
    }[result][0]

    return {
        "home_xg": home_xg,
        "away_xg": away_xg,
        "1x2": result,
        "1x2_probs": (home_win, draw, away_win),
        "over25": over25,
        "under25": 1 - over25,
        "btts": btts,
        "double_chance": double_chance
    }


def pct(value):
    return f"{value * 100:.1f}%"


async def analyze_match(item):
    fixture = item["fixture"]
    teams = item["teams"]
    home, away = teams["home"], teams["away"]

    hf, af = await asyncio.gather(
        team_form(home["id"]), team_form(away["id"])
    )
    if not hf or not af:
        return None

    prediction = make_prediction(hf, af)
    match_name = f'{home["name"]} vs {away["name"]}'

    save_prediction(
        fixture["id"], match_name,
        json.dumps(prediction, ensure_ascii=False)
    )

    return {
        "id": fixture["id"],
        "name": match_name,
        "kickoff": fixture["date"],
        "league": item.get("league", {}).get("name", "Unknown league"),
        "prediction": prediction
    }


def format_analysis(match):
    p = match["prediction"]
    h, d, a = p["1x2_probs"]
    return (
        f"⚽ {match['name']}\n"
        f"🏆 League: {match['league']}\n"
        f"🕒 Kickoff: {match['kickoff']}\n\n"
        f"📊 Recent-form model\n"
        f"Expected goals: {p['home_xg']:.2f} - {p['away_xg']:.2f}\n\n"
        f"1X2:\n"
        f"Home win: {pct(h)}\n"
        f"Draw: {pct(d)}\n"
        f"Away win: {pct(a)}\n\n"
        f"Over 2.5: {pct(p['over25'])}\n"
        f"Under 2.5: {pct(p['under25'])}\n"
        f"BTTS Yes: {pct(p['btts'])}\n"
        f"Double Chance suggestion: {p['double_chance']}\n\n"
        f"⚠️ এটি সাম্প্রতিক ফলভিত্তিক আনুমানিক মডেল; "
        f"নিশ্চিত ফল বা লাভের গ্যারান্টি নয়।"
    )


# ================= TELEGRAM UI =================
def home_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Next 12 Hours", callback_data="matches")],
        [InlineKeyboardButton("📊 AI Tips", callback_data="tips")],
        [InlineKeyboardButton("👑 VIP", callback_data="vip")],
        [InlineKeyboardButton("📜 History", callback_data="history")],
        [InlineKeyboardButton("ℹ️ About", callback_data="about")]
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    await update.message.reply_text(
        "⚽ Welcome to Football AI Pro Bot!\n\n"
        "ম্যাচের তথ্য ও সাম্প্রতিক ফর্মভিত্তিক বিশ্লেষণ পেতে নিচের বাটন চাপো।",
        reply_markup=home_keyboard()
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Your Telegram ID: {update.effective_user.id}"
    )


async def tips_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_tips(update, context, update.effective_user.id)


async def show_tips(update, context, user_id):
    if not is_vip(user_id):
        msg = (
            "🔒 AI Tips দেখতে VIP approval প্রয়োজন।\n"
            "VIP আবেদন করতে /vip ব্যবহার করো।"
        )
        if update.callback_query:
            await update.callback_query.message.reply_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    target = update.callback_query.message if update.callback_query else update.message
    await target.reply_text("🔎 আগামী ১২ ঘণ্টার ম্যাচ বিশ্লেষণ করছি...")

    try:
        matches = await upcoming_matches()
        if not matches:
            await target.reply_text("আগামী ১২ ঘণ্টায় কোনো upcoming match পাওয়া যায়নি।")
            return

        # Keep API usage controlled on the free plan.
        analysed = 0
        for item in matches[:6]:
            try:
                result = await analyze_match(item)
                if result:
                    await target.reply_text(format_analysis(result))
                    analysed += 1
            except Exception as exc:
                log.warning("Match analysis skipped: %s", exc)

        if not analysed:
            await target.reply_text(
                "যথেষ্ট সাম্প্রতিক ফল পাওয়া যায়নি। পরে আবার চেষ্টা করো।"
            )

    except Exception as exc:
        log.exception("Tips error")
        await target.reply_text(f"⚠️ তথ্য আনতে সমস্যা হয়েছে: {exc}")


async def vip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    await update.message.reply_text(
        "👑 VIP access-এর জন্য আবেদন করতে নিচে তোমার payment details "
        "এবং transaction ID লিখে /vip এর পরে পাঠাও।\n\n"
        "উদাহরণ:\n/vip bKash personal, TrxID ABC123\n\n"
        "Admin যাচাই করে অনুমোদন দেবেন।"
    )


async def vip_submit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    details = " ".join(context.args).strip()
    if not details:
        await update.message.reply_text(
            "ব্যবহার: /vip bKash, TrxID ABC123"
        )
        return

    request_id = add_vip_request(update.effective_user.id, details)
    await update.message.reply_text(
        f"✅ VIP আবেদন জমা হয়েছে। Request ID: {request_id}\n"
        "Admin যাচাই করার পর সিদ্ধান্ত জানানো হবে।"
    )
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"👑 New VIP request #{request_id}\n"
            f"User ID: {update.effective_user.id}\n"
            f"Details: {details}\n\n"
            f"Approve: /approve {request_id}\n"
            f"Reject: /reject {request_id}"
        )
    except Exception:
        log.exception("Could not notify admin")


async def admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Admin only.")
        return
    rows = pending_requests()
    if not rows:
        await update.message.reply_text("Pending VIP request নেই।")
        return
    for req_id, user_id, details in rows:
        await update.message.reply_text(
            f"Request #{req_id}\nUser: {user_id}\nDetails: {details}\n"
            f"/approve {req_id} অথবা /reject {req_id}"
        )


async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Admin only.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("ব্যবহার: /approve REQUEST_ID")
        return

    user_id = approve_request(int(context.args[0]))
    if user_id is None:
        await update.message.reply_text("Request পাওয়া যায়নি বা আগেই resolve হয়েছে।")
        return

    await update.message.reply_text(f"✅ User {user_id} approved as VIP.")
    try:
        await context.bot.send_message(user_id, "🎉 তোমার VIP access অনুমোদিত হয়েছে!")
    except Exception:
        log.warning("Could not message approved user")


async def admin_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Admin only.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("ব্যবহার: /reject REQUEST_ID")
        return

    user_id = reject_request(int(context.args[0]))
    if user_id is None:
        await update.message.reply_text("Request পাওয়া যায়নি বা আগেই resolve হয়েছে।")
        return

    await update.message.reply_text(f"❌ Request rejected for user {user_id}.")
    try:
        await context.bot.send_message(
            user_id, "তোমার VIP আবেদন অনুমোদিত হয়নি। Admin-এর সঙ্গে যোগাযোগ করো।"
        )
    except Exception:
        pass


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_history()
    if not rows:
        await update.message.reply_text("এখনো prediction history নেই।")
        return
    output = ["📜 Recent analysis history:"]
    for name, prediction, created in rows:
        try:
            p = json.loads(prediction)
            output.append(f"• {name}: {p['1x2']} | Over 2.5: {pct(p['over25'])}")
        except Exception:
            output.append(f"• {name}")
    await update.message.reply_text("\n".join(output))


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data

    if action == "matches":
        try:
            matches = await upcoming_matches()
            if not matches:
                await query.message.reply_text("আগামী ১২ ঘণ্টায় ম্যাচ পাওয়া যায়নি।")
                return
            lines = ["⚽ Upcoming matches (next 12 hours):"]
            for item in matches[:20]:
                home = item["teams"]["home"]["name"]
                away = item["teams"]["away"]["name"]
                kickoff = item["fixture"]["date"]
                league = item.get("league", {}).get("name", "")
                lines.append(f"\n{home} vs {away}\n{league} | {kickoff}")
            await query.message.reply_text("\n".join(lines))
        except Exception as exc:
            await query.message.reply_text(f"API error: {exc}")

    elif action == "tips":
        await show_tips(update, context, query.from_user.id)

    elif action == "vip":
        await query.message.reply_text(
            "VIP আবেদন করতে /vip লিখো।\n"
            "এরপর payment details ও transaction ID-সহ আবেদন পাঠাও।"
        )

    elif action == "history":
        rows = get_history()
        if not rows:
            await query.message.reply_text("এখনো history নেই।")
        else:
            text = ["📜 Recent analysis:"]
            for name, prediction, _ in rows:
                p = json.loads(prediction)
                text.append(f"{name}: {p['1x2']} | O2.5 {pct(p['over25'])}")
            await query.message.reply_text("\n".join(text))

    elif action == "about":
        await query.message.reply_text(
            "Football AI Pro Bot\n"
            "• API-Football data\n"
            "• Recent-form estimates\n"
            "• 1X2, Over/Under, BTTS, Double Chance\n\n"
            "Prediction নিশ্চিত নয়। Bet করার আগে নিজে যাচাই করো।"
        )


async def error_handler(update, context):
    log.exception("Unhandled Telegram error", exc_info=context.error)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable missing")
    if not FOOTBALL_API_KEY:
        raise RuntimeError("FOOTBALL_API_KEY environment variable missing")

    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("tips", tips_command))
    app.add_handler(CommandHandler("vip", vip_submit))
    app.add_handler(CommandHandler("pending", admin_pending))
    app.add_handler(CommandHandler("approve", admin_approve))
    app.add_handler(CommandHandler("reject", admin_reject))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)

    log.info("Football AI Pro Bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
