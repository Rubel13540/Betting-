import os
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)

# Secrets are read from Railway Variables. Never commit API keys or bot tokens.
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "5293614793"))
BKASH_PERSONAL = os.getenv("BKASH_PERSONAL", "01911198221").strip()
NAGAD_PERSONAL = os.getenv("NAGAD_PERSONAL", "01911198221").strip()
WHATSAPP_SUPPORT = os.getenv("WHATSAPP_SUPPORT", "https://wa.me/message/ZFPUNOUHWSWRI1").strip()
TIMEZONE = os.getenv("TIMEZONE", "Asia/Dhaka")
DB_PATH = os.getenv("DB_PATH", "bot.sqlite3")
API_BASE = "https://v3.football.api-sports.io"
TZ = ZoneInfo(TIMEZONE)

if not BOT_TOKEN:
    raise RuntimeError("Missing Railway Variable BOT_TOKEN")
if not API_FOOTBALL_KEY:
    raise RuntimeError("Missing Railway Variable API_FOOTBALL_KEY")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("football-vip-bot")

PACKAGES = {
    "1m": {"name": "1 Month", "days": 30, "price": 2000},
    "2m": {"name": "2 Months", "days": 60, "price": 3000},
    "6m": {"name": "6 Months", "days": 180, "price": 4000},
    "1y": {"name": "1 Year", "days": 365, "price": 5000},
}
LIVE_CODES = {"1H", "HT", "2H", "ET", "P", "LIVE", "BT", "SUSP", "INT"}
DONE_CODES = {"FT", "AET", "PEN"}
UPCOMING_CODES = {"NS", "TBD"}

def connect_db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with connect_db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined_at TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS tips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fixture_id INTEGER NOT NULL,
            home TEXT NOT NULL,
            away TEXT NOT NULL,
            category TEXT NOT NULL,
            selection TEXT NOT NULL,
            odds TEXT DEFAULT '',
            note TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            result TEXT DEFAULT 'PENDING'
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT DEFAULT '',
            package_key TEXT NOT NULL,
            amount INTEGER NOT NULL,
            method TEXT NOT NULL,
            trx_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            created_at TEXT NOT NULL,
            reviewed_at TEXT DEFAULT '',
            reviewed_by INTEGER DEFAULT 0
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS vip_members (
            user_id INTEGER PRIMARY KEY,
            expires_at TEXT NOT NULL,
            payment_id INTEGER NOT NULL
        )""")

def now_local():
    return datetime.now(TZ)

def iso_now():
    return now_local().isoformat()

def register_user(user):
    with connect_db() as con:
        con.execute(
            "INSERT INTO users(user_id, username, first_name, joined_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name",
            (user.id, user.username or "", user.first_name or "", iso_now())
        )

async def api_get(path, params=None):
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(API_BASE + path, params=params or {}) as response:
            payload = await response.json(content_type=None)
            if response.status >= 400:
                raise RuntimeError(f"API HTTP {response.status}: {str(payload)[:400]}")
            if isinstance(payload, dict) and payload.get("errors"):
                raise RuntimeError(f"API returned errors: {str(payload['errors'])[:400]}")
            return payload if isinstance(payload, dict) else {}

def parse_kickoff(fixture):
    value = fixture.get("fixture", {}).get("date")
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TZ)
    except Exception:
        return None

def fixture_display(f):
    fx = f.get("fixture", {})
    teams = f.get("teams", {})
    league = f.get("league", {})
    home = teams.get("home", {}).get("name", "Home")
    away = teams.get("away", {}).get("name", "Away")
    status = fx.get("status", {}).get("short", "NS")
    date = parse_kickoff(f)
    time_text = date.strftime("%d %b • %H:%M") if date else "Time N/A"
    goals = f.get("goals", {})
    if status in LIVE_CODES or status in DONE_CODES:
        time_text = f"{status} • {goals.get('home', '-')} - {goals.get('away', '-')}"
    return home, away, league.get("name", "Football"), time_text, status

def main_menu(user_id):
    rows = [
        [InlineKeyboardButton("📅 Daily Sure Tips", callback_data="auto:all"),
         InlineKeyboardButton("⚽ Football Tips", callback_data="fixtures")],
        [InlineKeyboardButton("⚽ Over-Under", callback_data="auto:over"),
         InlineKeyboardButton("🤝 Both Teams Score", callback_data="auto:btts")],
        [InlineKeyboardButton("🎯 Single Tips", callback_data="auto:1x2"),
         InlineKeyboardButton("📊 Odds", callback_data="odds_fixtures")],
        [InlineKeyboardButton("🔴 Live Matches", callback_data="live"),
         InlineKeyboardButton("📚 View History", callback_data="history")],
        [InlineKeyboardButton("👑 VIP Packages", callback_data="vip"),
         InlineKeyboardButton("💬 WhatsApp Support", url=WHATSAPP_SUPPORT)],
    ]
    if user_id == ADMIN_ID:
        rows.append([InlineKeyboardButton("🛠 Admin Panel", callback_data="admin")])
    return InlineKeyboardMarkup(rows)

def back_menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Home", callback_data="home")]])

def package_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1 Month • ৳2,000", callback_data="pkg:1m"),
         InlineKeyboardButton("2 Months • ৳3,000", callback_data="pkg:2m")],
        [InlineKeyboardButton("6 Months • ৳4,000", callback_data="pkg:6m"),
         InlineKeyboardButton("1 Year • ৳5,000", callback_data="pkg:1y")],
        [InlineKeyboardButton("⬅️ Home", callback_data="home")],
    ])

def payment_method_keyboard(pkg):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("bKash Personal", callback_data=f"pay:{pkg}:bkash"),
         InlineKeyboardButton("Nagad Personal", callback_data=f"pay:{pkg}:nagad")],
        [InlineKeyboardButton("⬅️ Packages", callback_data="vip")],
    ])

def admin_review_keyboard(payment_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"review:{payment_id}:APPROVED"),
        InlineKeyboardButton("❌ Reject", callback_data=f"review:{payment_id}:REJECTED"),
    ]])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)
    await update.effective_message.reply_text(
        "⚽ FOOTBALL TIPS\n\n"
        "ম্যাচ, API-ভিত্তিক prediction, live score, odds, history ও VIP প্যাকেজ বেছে নিন।\n"
        "⚠️ Tips অনুমানভিত্তিক; কোনো জয়ের নিশ্চয়তা নেই।",
        reply_markup=main_menu(user.id)
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "ব্যবহার:\n/start — Home menu\n/viptips — VIP সদস্যদের টিপস\n"
        "/tip FIXTURE_ID | CATEGORY | SELECTION | ODDS | NOTE — Admin tip যোগ\n"
        "/settle TIP_ID WIN/LOSS/VOID — Admin ফলাফল আপডেট\n/mytips — Admin tips list\n\n"
        "Payment দিলে সঠিক Transaction ID পাঠান। Admin যাচাই করে approve/reject করবেন.",
        reply_markup=main_menu(update.effective_user.id)
    )

async def get_fixtures(from_date=None, to_date=None, live=False):
    params = {"timezone": TIMEZONE}
    if live:
        params["live"] = "all"
    else:
        params["from"] = from_date
        params["to"] = to_date
    payload = await api_get("/fixtures", params)
    return payload.get("response", [])

async def upcoming_12h():
    now = now_local()
    fixtures = await get_fixtures(now.date().isoformat(), (now.date() + timedelta(days=1)).isoformat())
    end = now + timedelta(hours=12)
    result = []
    for f in fixtures:
        dt = parse_kickoff(f)
        if f.get("fixture", {}).get("status", {}).get("short") in UPCOMING_CODES and dt and now <= dt <= end:
            result.append(f)
    return sorted(result, key=lambda f: parse_kickoff(f) or now)

async def recent_finished():
    now = now_local()
    fixtures = await get_fixtures((now.date() - timedelta(days=2)).isoformat(), now.date().isoformat())
    result = [f for f in fixtures if f.get("fixture", {}).get("status", {}).get("short") in DONE_CODES]
    return sorted(result, key=lambda f: parse_kickoff(f) or now, reverse=True)

async def fixture_picker(query, title, fixtures, category="all", limit=15):
    if not fixtures:
        await query.edit_message_text(title + "\n\nএখন কোনো ম্যাচ পাওয়া যায়নি।", reply_markup=back_menu())
        return
    rows = []
    for f in fixtures[:limit]:
        home, away, league, time_text, status = fixture_display(f)
        fid = f.get("fixture", {}).get("id")
        if fid:
            rows.append([InlineKeyboardButton(f"{home} vs {away} • {time_text}"[:60], callback_data=f"match:{fid}:{category}")])
    rows.append([InlineKeyboardButton("⬅️ Home", callback_data="home")])
    await query.edit_message_text(title + "\nম্যাচ নির্বাচন করুন:", reply_markup=InlineKeyboardMarkup(rows))

async def fetch_prediction(fixture_id):
    data = await api_get("/predictions", {"fixture": fixture_id})
    items = data.get("response", [])
    return items[0] if items else None

async def fetch_odds(fixture_id):
    data = await api_get("/odds", {"fixture": fixture_id})
    return data.get("response", [])

def odds_lines(items):
    output = []
    for item in items:
        for book in item.get("bookmakers", []):
            for bet in book.get("bets", []):
                name = bet.get("name", "")
                if any(x in name.lower() for x in ("match winner", "both teams score", "over/under")):
                    values = [f"{v.get('value')}: {v.get('odd')}" for v in bet.get("values", []) if v.get("odd")]
                    if values:
                        output.append(f"• {name} — {book.get('name','Bookmaker')}\n  " + " | ".join(values[:8]))
        if len(output) >= 8:
            break
    return output[:8]

def prediction_message(pred, category):
    if not pred:
        return "এই ম্যাচের জন্য API-Football prediction পাওয়া যায়নি। অন্য ম্যাচ বেছে নিন।"
    teams = pred.get("teams", {})
    home = teams.get("home", {}).get("name", "Home")
    away = teams.get("away", {}).get("name", "Away")
    p = pred.get("predictions", {})
    pct = p.get("percent", {}) or {}
    winner = p.get("winner") or {}
    winner_name = winner.get("name") if isinstance(winner, dict) else None
    advice = p.get("advice") or "API নির্দিষ্ট advice দেয়নি।"
    under_over = p.get("under_over") or "Not available"
    goal_data = p.get("goals", {}) or {}
    lines = [f"⚽ {home} vs {away}", ""]
    if category in ("all", "1x2", "safe"):
        lines.append("🏆 1X2 prediction: " + (winner_name or "Not available"))
        if pct:
            lines.append(f"📊 Home {pct.get('home','-')} | Draw {pct.get('draw','-')} | Away {pct.get('away','-')}")
    if category in ("all", "over"):
        lines.append("⚽ Goals advice: " + str(under_over))
        if goal_data:
            lines.append(f"Expected goals: {home} {goal_data.get('home','-')} | {away} {goal_data.get('away','-')}")
        lines.append("Over 1.5/2.5: API prediction data যাচাই করুন; আলাদা নির্ভরযোগ্য তথ্য না থাকলে নিশ্চিত selection দেখানো হয় না।")
    if category in ("all", "btts"):
        btts = p.get("btts")
        lines.append("🤝 BTTS: " + (str(btts) if btts not in (None, "") else "API-তে আলাদা তথ্য নেই"))
    lines += ["", "📝 API advice: " + advice, "", "⚠️ এটি পরিসংখ্যানভিত্তিক অনুমান, নিশ্চিত ফল নয়।"]
    return "\n".join(lines)

def is_vip(user_id):
    with connect_db() as con:
        row = con.execute("SELECT expires_at FROM vip_members WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return False
    try:
        return datetime.fromisoformat(row["expires_at"]) > now_local()
    except Exception:
        return False

async def vip_menu(query):
    await query.edit_message_text(
        "👑 VIP Packages\n\n"
        "১ মাস — ৳২,০০০\n২ মাস — ৳৩,০০০\n৬ মাস — ৳৪,০০০\n১ বছর — ৳৫,০০০\n\n"
        "প্যাকেজ নির্বাচন করুন। পেমেন্ট যাচাইয়ের পর Admin VIP চালু করবেন।",
        reply_markup=package_keyboard()
    )

async def send_payment_request(update, context, package_key, method):
    pkg = PACKAGES[package_key]
    number = BKASH_PERSONAL if method == "bkash" else NAGAD_PERSONAL
    context.user_data["awaiting_payment"] = {"package": package_key, "method": method}
    await update.callback_query.edit_message_text(
        f"💳 VIP Payment\n\nPackage: {pkg['name']}\nAmount: ৳{pkg['price']}\n"
        f"Method: {method.title()} Personal\nSend money to:\n`{number}`\n\n"
        "টাকা পাঠানোর পর পরের মেসেজে Transaction ID (TrxID) লিখে পাঠান।\n"
        "Admin যাচাই করে Approve বা Reject করবেন।",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Packages", callback_data="vip")]])
    )

async def payment_trx_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending = context.user_data.get("awaiting_payment")
    if not pending:
        return
    trx = (update.effective_message.text or "").strip()
    if len(trx) < 5 or len(trx) > 100 or " " in trx:
        await update.effective_message.reply_text("সঠিক Transaction ID লিখুন।")
        return
    pkg = PACKAGES[pending["package"]]
    user = update.effective_user
    with connect_db() as con:
        cur = con.execute(
            "INSERT INTO payments(user_id,username,package_key,amount,method,trx_id,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (user.id, user.username or "", pending["package"], pkg["price"], pending["method"], trx, "PENDING", iso_now())
        )
        payment_id = cur.lastrowid
    context.user_data.pop("awaiting_payment", None)
    await update.effective_message.reply_text(
        f"✅ Payment request জমা হয়েছে।\nRequest ID: #{payment_id}\n"
        "Admin টাকা যাচাই করে সিদ্ধান্ত জানাবেন।",
        reply_markup=main_menu(user.id)
    )
    admin_text = (
        f"🔔 VIP Payment Review #{payment_id}\n\n"
        f"User ID: {user.id}\nUsername: @{user.username or 'none'}\n"
        f"Package: {pkg['name']}\nAmount: ৳{pkg['price']}\n"
        f"Method: {pending['method'].title()}\nTrxID: {trx}\n"
        f"সময়: {iso_now()}\n\nটাকা সত্যিই এসেছে কি না যাচাই করুন।"
    )
    try:
        await context.bot.send_message(ADMIN_ID, admin_text, reply_markup=admin_review_keyboard(payment_id))
    except Exception:
        log.exception("Unable to notify admin for payment %s", payment_id)
        await update.effective_message.reply_text("Admin notification পাঠানো যায়নি। Support-এ যোগাযোগ করুন।")

async def admin_panel(query):
    await query.edit_message_text(
        "🛠 Admin Panel\n\nCommands:\n/tip FIXTURE_ID | CATEGORY | SELECTION | ODDS | NOTE\n"
        "/mytips — recent manual tips\n/settle TIP_ID WIN/LOSS/VOID\n"
        "/pending — pending payment requests\n\nPayment approve/reject বাটন admin notification-এ আসবে।",
        reply_markup=back_menu()
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    try:
        if data == "home":
            await q.edit_message_text("⚽ FOOTBALL TIPS — Main Menu", reply_markup=main_menu(q.from_user.id))
        elif data == "vip":
            await vip_menu(q)
        elif data.startswith("pkg:"):
            key = data.split(":")[1]
            if key not in PACKAGES:
                return
            await q.edit_message_text(f"{PACKAGES[key]['name']} — ৳{PACKAGES[key]['price']}\nPayment method নির্বাচন করুন:",
                reply_markup=payment_method_keyboard(key))
        elif data.startswith("pay:"):
            _, key, method = data.split(":")
            if key not in PACKAGES or method not in ("bkash", "nagad"):
                return
            await send_payment_request(update, context, key, method)
        elif data == "fixtures":
            await q.edit_message_text("ম্যাচ খোঁজা হচ্ছে…")
            await fixture_picker(q, "⚽ আগামী ১২ ঘণ্টার ম্যাচ", await upcoming_12h(), "all")
        elif data.startswith("auto:"):
            category = data.split(":")[1]
            await q.edit_message_text("API prediction-এর জন্য ম্যাচ খোঁজা হচ্ছে…")
            await fixture_picker(q, "🎯 API Football Tips", await upcoming_12h(), category)
        elif data == "odds_fixtures":
            await q.edit_message_text("Odds-এর জন্য ম্যাচ খোঁজা হচ্ছে…")
            await fixture_picker(q, "📊 Available Odds", await upcoming_12h(), "odds")
        elif data == "live":
            await q.edit_message_text("Live matches লোড হচ্ছে…")
            fixtures = await get_fixtures(live=True)
            await fixture_picker(q, "🔴 Live Matches", fixtures, "live")
        elif data == "history":
            with connect_db() as con:
                rows = con.execute("SELECT * FROM tips ORDER BY id DESC LIMIT 15").fetchall()
            if not rows:
                await q.edit_message_text("📚 History-তে এখনো Admin-published tip নেই।", reply_markup=back_menu())
            else:
                lines = ["📚 Tips History (latest 15)"]
                for r in rows:
                    lines.append(f"#{r['id']} {r['home']} vs {r['away']}\n{r['category']}: {r['selection']} | Odds {r['odds']} | {r['result']}")
                await q.edit_message_text("\n\n".join(lines)[:3900], reply_markup=back_menu())
        elif data == "admin":
            if q.from_user.id != ADMIN_ID:
                await q.edit_message_text("Admin only.", reply_markup=back_menu())
            else:
                await admin_panel(q)
        elif data.startswith("match:"):
            _, fid, category = data.split(":", 2)
            fixture_id = int(fid)
            if category == "live":
                payload = await api_get("/fixtures", {"id": fixture_id, "timezone": TIMEZONE})
                fs = payload.get("response", [])
                if fs:
                    h, a, league, time_text, status = fixture_display(fs[0])
                    await q.edit_message_text(f"🔴 {league}\n{h} vs {a}\n{time_text}", reply_markup=back_menu())
                else:
                    await q.edit_message_text("ম্যাচের তথ্য পাওয়া যায়নি।", reply_markup=back_menu())
            elif category == "odds":
                items = await fetch_odds(fixture_id)
                lines = odds_lines(items)
                await q.edit_message_text("📊 Match Odds\n\n" + ("\n\n".join(lines) if lines else "এই ম্যাচের odds API-তে পাওয়া যায়নি।"),
                    reply_markup=back_menu())
            else:
                pred = await fetch_prediction(fixture_id)
                await q.edit_message_text(prediction_message(pred, category), reply_markup=back_menu())
        elif data.startswith("review:"):
            if q.from_user.id != ADMIN_ID:
                await q.edit_message_text("এই action শুধু Admin করতে পারবেন।")
                return
            _, payment_id_text, decision = data.split(":")
            payment_id = int(payment_id_text)
            with connect_db() as con:
                payment = con.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
                if not payment:
                    await q.edit_message_text("Payment request পাওয়া যায়নি.")
                    return
                if payment["status"] != "PENDING":
                    await q.edit_message_text(f"এই request আগেই {payment['status']} করা হয়েছে।")
                    return
                if decision == "APPROVED":
                    pkg = PACKAGES[payment["package_key"]]
                    current = con.execute("SELECT expires_at FROM vip_members WHERE user_id=?", (payment["user_id"],)).fetchone()
                    base = now_local()
                    if current:
                        try:
                            old_expiry = datetime.fromisoformat(current["expires_at"])
                            if old_expiry > base:
                                base = old_expiry
                        except Exception:
                            pass
                    expiry = base + timedelta(days=pkg["days"])
                    con.execute("INSERT INTO vip_members(user_id,expires_at,payment_id) VALUES(?,?,?) "
                                "ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at,payment_id=excluded.payment_id",
                                (payment["user_id"], expiry.isoformat(), payment_id))
                con.execute("UPDATE payments SET status=?, reviewed_at=?, reviewed_by=? WHERE id=?",
                            (decision, iso_now(), q.from_user.id, payment_id))
            await q.edit_message_text(f"Payment #{payment_id} {decision}.")
            try:
                if decision == "APPROVED":
                    await context.bot.send_message(payment["user_id"], f"✅ VIP Approved!\nআপনার VIP মেয়াদ: {expiry.strftime('%d %b %Y, %H:%M')} পর্যন্ত।")
                else:
                    await context.bot.send_message(payment["user_id"], f"❌ VIP Payment #{payment_id} Reject করা হয়েছে। টাকা/TrxID যাচাই করে Support-এ যোগাযোগ করুন: {WHATSAPP_SUPPORT}")
            except Exception:
                log.exception("Could not notify user after payment review")
    except Exception as e:
        log.exception("Callback error")
        try:
            await q.edit_message_text(f"সমস্যা হয়েছে: {str(e)[:500]}", reply_markup=back_menu())
        except Exception:
            pass

async def vip_tips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_vip(user_id):
        await update.effective_message.reply_text("এই অংশ শুধু VIP সদস্যদের জন্য। /start থেকে VIP প্যাকেজ নিন।")
        return
    await update.effective_message.reply_text("👑 VIP active. VIP tips-এর জন্য Admin প্রকাশিত tips দেখুন /mytips।")

async def admin_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("Admin only.")
        return
    raw = update.effective_message.text.partition(" ")[2].strip()
    parts = [x.strip() for x in raw.split("|")]
    if len(parts) < 4:
        await update.effective_message.reply_text("Format: /tip FIXTURE_ID | CATEGORY | SELECTION | ODDS | NOTE")
        return
    try:
        fid = int(parts[0])
        payload = await api_get("/fixtures", {"id": fid, "timezone": TIMEZONE})
        fs = payload.get("response", [])
        if not fs:
            await update.effective_message.reply_text("Real API fixture ID দিন।")
            return
        f = fs[0]
        home = f.get("teams", {}).get("home", {}).get("name", "Home")
        away = f.get("teams", {}).get("away", {}).get("name", "Away")
        with connect_db() as con:
            con.execute("INSERT INTO tips(fixture_id,home,away,category,selection,odds,note,created_at,result) VALUES(?,?,?,?,?,?,?,?,?)",
                        (fid, home, away, parts[1], parts[2], parts[3], parts[4] if len(parts)>4 else "", iso_now(), "PENDING"))
        await update.effective_message.reply_text(f"✅ Tip saved: {home} vs {away}\n{parts[1]} — {parts[2]} | Odds {parts[3]}")
    except Exception as e:
        await update.effective_message.reply_text(f"Tip save error: {str(e)[:300]}")

async def settle_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("Admin only.")
        return
    if len(context.args) != 2 or context.args[1].upper() not in {"WIN", "LOSS", "VOID"}:
        await update.effective_message.reply_text("Format: /settle TIP_ID WIN|LOSS|VOID")
        return
    tip_id, result = int(context.args[0]), context.args[1].upper()
    with connect_db() as con:
        cur = con.execute("UPDATE tips SET result=? WHERE id=?", (result, tip_id))
    await update.effective_message.reply_text("Updated." if cur.rowcount else "Tip ID পাওয়া যায়নি।")

async def list_tips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("Admin only.")
        return
    with connect_db() as con:
        rows = con.execute("SELECT * FROM tips ORDER BY id DESC LIMIT 20").fetchall()
    if not rows:
        await update.effective_message.reply_text("কোনো manual tip নেই।")
        return
    lines = [f"#{r['id']} {r['home']} vs {r['away']} | {r['category']}: {r['selection']} | {r['odds']} | {r['result']}" for r in rows]
    await update.effective_message.reply_text("\n".join(lines)[:3900])

async def pending_payments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("Admin only.")
        return
    with connect_db() as con:
        rows = con.execute("SELECT * FROM payments WHERE status='PENDING' ORDER BY id DESC LIMIT 20").fetchall()
    if not rows:
        await update.effective_message.reply_text("Pending payment নেই।")
        return
    for p in rows:
        pkg = PACKAGES[p["package_key"]]
        text = (f"🔔 Payment #{p['id']}\nUser: {p['user_id']} (@{p['username'] or 'none'})\n"
                f"{pkg['name']} — ৳{p['amount']}\nMethod: {p['method']}\nTrxID: {p['trx_id']}")
        await update.effective_message.reply_text(text, reply_markup=admin_review_keyboard(p["id"]))

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("viptips", vip_tips))
    app.add_handler(CommandHandler("tip", admin_tip))
    app.add_handler(CommandHandler("settle", settle_tip))
    app.add_handler(CommandHandler("mytips", list_tips))
    app.add_handler(CommandHandler("pending", pending_payments))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, payment_trx_handler))
    log.info("Football VIP bot starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    init_db()
    main()
