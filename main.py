import os
import json
import uuid
import sqlite3
import logging
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters,
)

# All secrets/settings are configured in Railway Variables.
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_KEY = os.getenv("API_KEY", "").strip()
API_BASE_URL = os.getenv("API_BASE_URL", "https://buildspare.site").rstrip("/")
ADMIN_ID = int(os.getenv("ADMIN_ID", "5293614793"))
BKASH_NUMBER = os.getenv("BKASH_NUMBER", "01911198221").strip()
NAGAD_NUMBER = os.getenv("NAGAD_NUMBER", "01911198221").strip()
WHATSAPP_SUPPORT = os.getenv("WHATSAPP_SUPPORT", "https://wa.me/message/ZFPUNOUHWSWRI1").strip()
BOT_LINK = os.getenv("BOT_LINK", "https://t.me/trafficsimulatorbot").strip()
MARKUP_PERCENT = Decimal(os.getenv("MARKUP_PERCENT", "5"))
API_TIMEOUT = int(os.getenv("API_TIMEOUT", "25"))
DB_PATH = os.getenv("DB_PATH", "bot_data.sqlite3")

# Default catalog transcribed from the user's FF RESELLER BD SERVER catalog.
# These are the reseller prices the user pasted, not independently live-verified rates.
# Railway PACKAGES_JSON can override this list whenever supplier prices/tags change.
DEFAULT_PACKAGES = [
    {"key": "d25", "name": "25 Diamond", "tag_line": "25", "base_cost": "19.50"},
    {"key": "d50", "name": "50 Diamond", "tag_line": "50", "base_cost": "33.80"},
    {"key": "lv6", "name": "Level Up 6", "tag_line": "LV6", "base_cost": "35.10"},
    {"key": "weekly_lite", "name": "Weekly Lite", "tag_line": "LITE", "base_cost": "35.10"},
    {"key": "lv10", "name": "Level Up 10", "tag_line": "LV10", "base_cost": "62.40"},
    {"key": "lv25", "name": "Level Up 25", "tag_line": "LV25", "base_cost": "62.40"},
    {"key": "lv20", "name": "Level Up 20", "tag_line": "LV20", "base_cost": "62.40"},
    {"key": "lv15", "name": "Level Up 15", "tag_line": "LV15", "base_cost": "62.40"},
    {"key": "d115", "name": "115 Diamond", "tag_line": "115", "base_cost": "74.10"},
    {"key": "lv30", "name": "Level Up 30", "tag_line": "LV30", "base_cost": "88.40"},
    {"key": "d240", "name": "240 Diamond", "tag_line": "240", "base_cost": "146.90"},
    {"key": "weekly", "name": "Weekly", "tag_line": "WEEKLY", "base_cost": "149.50"},
    {"key": "d610", "name": "610 Diamond", "tag_line": "610", "base_cost": "371.80"},
    {"key": "monthly", "name": "Monthly", "tag_line": "MONTHLY", "base_cost": "734.50"},
    {"key": "d1240", "name": "1240 Diamond", "tag_line": "1240", "base_cost": "741.00"},
    {"key": "d2530", "name": "2530 Diamond", "tag_line": "2530", "base_cost": "1487.20"},
]

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("freefire_bot")


def db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            wallet_balance TEXT NOT NULL DEFAULT '0.00', created_at TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, package_key TEXT NOT NULL,
            package_name TEXT NOT NULL, tag_line TEXT NOT NULL, player_uid TEXT NOT NULL,
            base_cost TEXT NOT NULL, sale_price TEXT NOT NULL, status TEXT NOT NULL,
            payment_ref TEXT, supplier_order_id TEXT, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, note TEXT
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS deposits (
            deposit_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, amount TEXT NOT NULL,
            payment_method TEXT NOT NULL, transaction_ref TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, reviewed_at TEXT
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_deposits_user ON deposits(user_id)")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def sale_price(base_cost):
    return money(Decimal(str(base_cost)) * (Decimal("1") + MARKUP_PERCENT / Decimal("100")))


def packages():
    """Load only the exact packages configured from the supplier's current catalog."""
    raw = os.getenv("PACKAGES_JSON", "").strip()
    try:
        parsed = json.loads(raw) if raw else DEFAULT_PACKAGES
        if not isinstance(parsed, list) or not parsed:
            raise ValueError("PACKAGES_JSON must be a non-empty JSON list")
        seen_keys = set()
        cleaned = []
        for p in parsed:
            if not isinstance(p, dict):
                raise ValueError("Each package must be a JSON object")
            for k in ("key", "name", "tag_line", "base_cost"):
                if k not in p or str(p[k]).strip() == "":
                    raise ValueError(f"Package is missing {k}")
            key = str(p["key"]).strip()
            tag_line = str(p["tag_line"]).strip()
            cost = money(p["base_cost"])
            if not key.replace("_", "").replace("-", "").isalnum():
                raise ValueError("Package key may contain only letters, numbers, _ and -")
            if key in seen_keys:
                raise ValueError(f"Duplicate package key: {key}")
            if cost <= 0:
                raise ValueError(f"base_cost must be positive for package {key}")
            seen_keys.add(key)
            cleaned.append({
                "key": key,
                "name": str(p["name"]).strip(),
                "tag_line": tag_line,
                "base_cost": str(cost),
            })
        return cleaned
    except Exception as exc:
        log.error("Invalid PACKAGES_JSON; refusing to show/order packages: %s", exc)
        return []


def get_package(key):
    return next((p for p in packages() if p["key"] == key), None)


def ensure_user(tg_user):
    with db() as con:
        con.execute(
            "INSERT INTO users(user_id, username, first_name, created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name",
            (tg_user.id, tg_user.username or "", tg_user.first_name or "", now_iso()),
        )


def get_balance(user_id):
    with db() as con:
        row = con.execute("SELECT wallet_balance FROM users WHERE user_id=?", (user_id,)).fetchone()
    return money(row["wallet_balance"] if row else "0")


def change_balance(user_id, delta):
    with db() as con:
        row = con.execute("SELECT wallet_balance FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            raise ValueError("User not found")
        new_value = money(Decimal(row["wallet_balance"]) + Decimal(str(delta)))
        if new_value < 0:
            raise ValueError("Insufficient wallet balance")
        con.execute("UPDATE users SET wallet_balance=? WHERE user_id=?", (str(new_value), user_id))
        return new_value


async def api_request(method, path, *, payload=None, params=None):
    if not API_KEY:
        raise RuntimeError("API_KEY is not configured in Railway Variables")
    headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}
    timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(
            method, f"{API_BASE_URL}{path}", headers=headers,
            json=payload, params=params,
        ) as response:
            raw = await response.text()
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                data = {"raw": raw[:500]}
            if response.status >= 400:
                raise RuntimeError(f"Supplier API HTTP {response.status}: {data}")
            return data


def home_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Buy Diamonds", callback_data="buy")],
        [InlineKeyboardButton("➕ Add Balance", callback_data="deposit")],
        [InlineKeyboardButton("📦 My Orders", callback_data="myorders"), InlineKeyboardButton("💰 Balance", callback_data="balance")],
        [InlineKeyboardButton("🆘 Support", url=WHATSAPP_SUPPORT)],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user)
    context.user_data.clear()
    text = (
        "🎮 *Free Fire Diamond Top-up*\n\n"
        "1. Add balance by bKash/Nagad.\n"
        "2. Admin verifies the transaction manually.\n"
        "3. Choose a package and enter your Player UID.\n\n"
        f"💰 Your balance: ৳{get_balance(user.id)}\n"
        "Only place an order after checking your Player UID carefully."
    )
    await update.effective_message.reply_text(text, parse_mode="Markdown", reply_markup=home_keyboard())

async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.effective_message.reply_text(
        f"💰 Your wallet balance: ৳{get_balance(update.effective_user.id)}",
        reply_markup=home_keyboard(),
    )

async def buy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await show_packages(update.effective_message)

async def show_packages(message):
    catalog = packages()
    if not catalog:
        await message.reply_text(
            "⚠️ Package catalog configuration is invalid. Admin should check Railway Variables → PACKAGES_JSON or restore the built-in catalog.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="home")]]),
        )
        return
    rows = []
    for p in catalog:
        price = sale_price(p["base_cost"])
        rows.append([InlineKeyboardButton(f"{p['name']} — ৳{price}", callback_data=f"pkg:{p['key']}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="home")])
    await message.reply_text(
        f"💎 *Select a package* (your price includes {MARKUP_PERCENT}% markup)\n\n"
        "Prices use the configured supplier reseller costs plus the markup. Supplier rates can change; admin should update the catalog when the supplier changes prices.",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows),
    )

async def begin_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    context.user_data.clear()
    context.user_data["state"] = "deposit_amount"
    await query.message.reply_text(
        "কত টাকা ব্যালেন্স যোগ করতে চান? শুধু সংখ্যায় লিখুন (যেমন 100)।\n\n"
        f"bKash Personal: `{BKASH_NUMBER}`\nNagad Personal: `{NAGAD_NUMBER}`\n\n"
        "পেমেন্ট করার আগে নম্বর ও অ্যাকাউন্টের ধরন যাচাই করুন।",
        parse_mode="Markdown",
    )

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    ensure_user(q.from_user)
    if data == "home":
        context.user_data.clear()
        await q.message.reply_text("🏠 Main menu", reply_markup=home_keyboard())
    elif data == "buy":
        context.user_data.clear()
        await show_packages(q.message)
    elif data == "balance":
        await q.message.reply_text(f"💰 Your wallet balance: ৳{get_balance(q.from_user.id)}", reply_markup=home_keyboard())
    elif data == "deposit":
        await begin_deposit(update, context)
    elif data == "myorders":
        with db() as con:
            rows = con.execute(
                "SELECT order_id, package_name, player_uid, sale_price, status, created_at FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 10",
                (q.from_user.id,),
            ).fetchall()
        if not rows:
            await q.message.reply_text("You have no orders yet.", reply_markup=home_keyboard())
            return
        lines = ["📦 *Your latest orders*"]
        for r in rows:
            lines.append(f"`{r['order_id']}` · {r['package_name']} · UID `{r['player_uid']}` · ৳{r['sale_price']} · *{r['status']}*")
        lines.append("\nUse /status ORDER_ID to refresh an order status.")
        await q.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=home_keyboard())
    elif data.startswith("pkg:"):
        p = get_package(data.split(":", 1)[1])
        if not p:
            await q.message.reply_text("Package not found. Please reopen the package list.")
            return
        context.user_data.clear()
        context.user_data["state"] = "player_uid"
        context.user_data["package_key"] = p["key"]
        await q.message.reply_text(
            f"Selected: *{p['name']}*\nPrice: *৳{sale_price(p['base_cost'])}*\n\n"
            "এখন আপনার Free Fire Player UID পাঠান। শুধু UID পাঠাবেন, password কখনো নয়।",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Cancel", callback_data="home")]]),
        )
    elif data.startswith("depapprove:") or data.startswith("depdecline:"):
        if q.from_user.id != ADMIN_ID:
            await q.message.reply_text("Not authorized.")
            return
        action, deposit_id = data.split(":", 1)
        with db() as con:
            dep = con.execute("SELECT * FROM deposits WHERE deposit_id=?", (deposit_id,)).fetchone()
            if not dep or dep["status"] != "pending":
                await q.message.reply_text("Deposit already reviewed or not found.")
                return
            if action == "depapprove":
                con.execute("UPDATE deposits SET status='approved', reviewed_at=? WHERE deposit_id=?", (now_iso(), deposit_id))
                row = con.execute("SELECT wallet_balance FROM users WHERE user_id=?", (dep["user_id"],)).fetchone()
                new_bal = money(Decimal(row["wallet_balance"]) + Decimal(dep["amount"]))
                con.execute("UPDATE users SET wallet_balance=? WHERE user_id=?", (str(new_bal), dep["user_id"]))
            else:
                con.execute("UPDATE deposits SET status='rejected', reviewed_at=? WHERE deposit_id=?", (now_iso(), deposit_id))
        if action == "depapprove":
            await q.message.reply_text(f"Deposit {deposit_id} approved. Wallet credited ৳{dep['amount']}.")
            try:
                await context.bot.send_message(dep["user_id"], f"✅ Deposit verified. ৳{dep['amount']} added. New balance: ৳{new_bal}.")
            except Exception:
                pass
        else:
            await q.message.reply_text(f"Deposit {deposit_id} rejected.")
            try:
                await context.bot.send_message(dep["user_id"], f"❌ Deposit {deposit_id} was not approved. Please contact support if you believe this is an error.")
            except Exception:
                pass
    elif data.startswith("adminorder:"):
        await q.message.reply_text("Order approval is not required after a verified wallet balance is used. Use /admin for pending deposits.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user)
    state = context.user_data.get("state")
    text = (update.effective_message.text or "").strip()
    if not state:
        await update.effective_message.reply_text("Please choose an option from the menu.", reply_markup=home_keyboard())
        return
    if state == "deposit_amount":
        try:
            amount = money(text)
            if amount < Decimal("10") or amount > Decimal("50000"):
                raise ValueError()
        except Exception:
            await update.effective_message.reply_text("Enter a valid amount between ৳10 and ৳50,000.")
            return
        context.user_data["deposit_amount"] = str(amount)
        context.user_data["state"] = "deposit_method"
        await update.effective_message.reply_text(
            f"Amount: ৳{amount}\nChoose which number you paid:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"bKash Personal · {BKASH_NUMBER}", callback_data="method:bkash")],
                [InlineKeyboardButton(f"Nagad Personal · {NAGAD_NUMBER}", callback_data="method:nagad")],
                [InlineKeyboardButton("Cancel", callback_data="home")],
            ]),
        )
    elif state == "deposit_txn":
        if len(text) < 5 or len(text) > 80 or " " in text:
            await update.effective_message.reply_text("Please send the exact transaction ID from your payment receipt.")
            return
        amount = context.user_data.get("deposit_amount")
        method = context.user_data.get("deposit_method")
        deposit_id = "D" + uuid.uuid4().hex[:10].upper()
        with db() as con:
            con.execute(
                "INSERT INTO deposits(deposit_id,user_id,amount,payment_method,transaction_ref,status,created_at) VALUES(?,?,?,?,?,'pending',?)",
                (deposit_id, user.id, amount, method, text, now_iso()),
            )
        context.user_data.clear()
        await update.effective_message.reply_text(
            f"✅ Deposit request submitted: `{deposit_id}`\nAmount: ৳{amount}\nStatus: Pending manual verification. Balance will be added only after admin verifies the payment.",
            parse_mode="Markdown", reply_markup=home_keyboard(),
        )
        if ADMIN_ID:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Approve + credit", callback_data=f"depapprove:{deposit_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"depdecline:{deposit_id}"),
            ]])
            try:
                await context.bot.send_message(
                    ADMIN_ID,
                    f"💳 *Deposit verification request*\nID: `{deposit_id}`\nUser: `{user.id}` (@{user.username or 'no_username'})\nAmount: ৳{amount}\nMethod: {method}\nTransaction ID: `{text}`\n\nVerify in your bKash/Nagad merchant/personal transaction history before approving.",
                    parse_mode="Markdown", reply_markup=kb,
                )
            except Exception as exc:
                log.error("Could not notify admin about deposit: %s", exc)
    elif state == "player_uid":
        uid = text
        if not uid.isdigit() or not (6 <= len(uid) <= 15):
            await update.effective_message.reply_text("UID must be 6–15 digits. Please check and send the Player UID again.")
            return
        p = get_package(context.user_data.get("package_key", ""))
        if not p:
            context.user_data.clear()
            await update.effective_message.reply_text("Package session expired. Please start again.", reply_markup=home_keyboard())
            return
        price = sale_price(p["base_cost"])
        balance = get_balance(user.id)
        if balance < price:
            context.user_data.clear()
            await update.effective_message.reply_text(
                f"Not enough balance. Package price: ৳{price}; your balance: ৳{balance}.\nPlease add balance first.",
                reply_markup=home_keyboard(),
            )
            return
        order_id = "FF" + uuid.uuid4().hex[:12].upper()
        # Deduct wallet before supplier request; if API request definitively fails, refund.
        try:
            change_balance(user.id, -price)
        except ValueError:
            await update.effective_message.reply_text("Insufficient balance. Please add balance first.", reply_markup=home_keyboard())
            context.user_data.clear()
            return
        with db() as con:
            con.execute(
                "INSERT INTO orders(order_id,user_id,package_key,package_name,tag_line,player_uid,base_cost,sale_price,status,created_at,updated_at,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (order_id, user.id, p["key"], p["name"], p["tag_line"], uid, str(p["base_cost"]), str(price), "submitting", now_iso(), now_iso(), "Wallet deducted; supplier request in progress"),
            )
        context.user_data.clear()
        await update.effective_message.reply_text(
            f"⏳ Order `{order_id}` received. Checking supplier…\nPackage: {p['name']}\nPlayer UID: `{uid}`\nCharged: ৳{price}\nRemaining balance: ৳{get_balance(user.id)}\n\nDo not place the same order again while this one is processing.",
            parse_mode="Markdown",
        )
        payload = {
            "account_info": {"player_id": uid},
            "selectedPackage": {"id": 1, "tag_line": p["tag_line"]},
            "quantity": 1,
            "order_id": order_id,
        }
        try:
            result = await api_request("POST", "/api/resaleorder", payload=payload)
            log.info("Supplier order response for %s: %s", order_id, result)
            success_flag = result.get("success") is True
            order_obj = result.get("order") or {}
            supplier_id = str(order_obj.get("id", ""))
            supplier_status = str(order_obj.get("status", "pending")).lower()
            if not success_flag:
                # Supplier explicitly rejected the order: refund wallet.
                change_balance(user.id, price)
                with db() as con:
                    con.execute("UPDATE orders SET status='failed_refunded', updated_at=?, note=? WHERE order_id=?", (now_iso(), str(result)[:800], order_id))
                await context.bot.send_message(user.id, f"❌ Supplier rejected order `{order_id}`. Your ৳{price} was refunded to your bot balance.\nDetails: {result.get('message', 'Supplier rejected the request.')}", parse_mode="Markdown", reply_markup=home_keyboard())
                return
            if supplier_status in ("complete", "completed", "success", "delivered"):
                new_status = "completed"
            elif supplier_status in ("failed", "cancelled", "canceled", "rejected"):
                change_balance(user.id, price)
                new_status = "failed_refunded"
            else:
                new_status = "processing"
            with db() as con:
                con.execute("UPDATE orders SET status=?, supplier_order_id=?, updated_at=?, note=? WHERE order_id=?", (new_status, supplier_id, now_iso(), str(result)[:800], order_id))
            if new_status == "failed_refunded":
                await context.bot.send_message(user.id, f"❌ Order `{order_id}` failed. ৳{price} refunded to your bot balance.", parse_mode="Markdown", reply_markup=home_keyboard())
            elif new_status == "completed":
                await context.bot.send_message(user.id, f"✅ Supplier reports order `{order_id}` completed.\nUID: `{uid}`\nPackage: {p['name']}", parse_mode="Markdown", reply_markup=home_keyboard())
            else:
                await context.bot.send_message(user.id, f"🕒 Order `{order_id}` was accepted and is still processing. It is NOT marked delivered yet. Use `/status {order_id}` to check later.", parse_mode="Markdown", reply_markup=home_keyboard())
        except Exception as exc:
            # A timeout can mean supplier accepted the order but response was lost. Do NOT auto-refund
            # or retry automatically; that could create a duplicate top-up. Keep it for admin review.
            log.exception("Supplier request uncertain for order %s", order_id)
            with db() as con:
                con.execute("UPDATE orders SET status='needs_review', updated_at=?, note=? WHERE order_id=?", (now_iso(), str(exc)[:800], order_id))
            await context.bot.send_message(user.id, f"⚠️ Order `{order_id}` needs a status check. Supplier response was unclear, so your balance has not been automatically refunded and no duplicate order was sent. Contact support: {WHATSAPP_SUPPORT}", parse_mode="Markdown", reply_markup=home_keyboard())
            try:
                await context.bot.send_message(ADMIN_ID, f"⚠️ Order needs review: {order_id}\nUser: {user.id}\nUID: {uid}\nPackage: {p['name']}\nError: {str(exc)[:500]}\nCheck supplier dashboard before refunding or resubmitting.")
            except Exception:
                pass
    else:
        context.user_data.clear()
        await update.effective_message.reply_text("Session expired. Please choose an option again.", reply_markup=home_keyboard())

async def on_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not q.data.startswith("method:"):
        return
    if context.user_data.get("state") != "deposit_method":
        await q.message.reply_text("Please start the Add Balance flow again.")
        return
    method = q.data.split(":", 1)[1]
    context.user_data["deposit_method"] = method
    context.user_data["state"] = "deposit_txn"
    number = BKASH_NUMBER if method == "bkash" else NAGAD_NUMBER
    await q.message.reply_text(
        f"Send ৳{context.user_data['deposit_amount']} to {method.title()} Personal: `{number}`.\n\nAfter payment, send the exact transaction ID here. Your balance will not be credited until admin verifies it.",
        parse_mode="Markdown",
    )

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.effective_message.reply_text("Usage: /status ORDER_ID")
        return
    order_id = context.args[0].strip()
    with db() as con:
        row = con.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
    if not row or (row["user_id"] != user_id and user_id != ADMIN_ID):
        await update.effective_message.reply_text("Order not found.")
        return
    if row["status"] in ("processing", "needs_review", "submitting"):
        try:
            result = await api_request("GET", "/api/resaleorder/status", params={"order_id": order_id})
            log.info("Supplier status for %s: %s", order_id, result)
            obj = result.get("order") or result
            raw_status = str(obj.get("status", result.get("status", "pending"))).lower()
            if raw_status in ("complete", "completed", "success", "delivered"):
                new_status = "completed"
            elif raw_status in ("failed", "cancelled", "canceled", "rejected"):
                new_status = "failed_refunded"
                # Refund only once when moving into failed_refunded.
                if row["status"] != "failed_refunded":
                    change_balance(row["user_id"], row["sale_price"])
            else:
                new_status = "processing"
            with db() as con:
                con.execute("UPDATE orders SET status=?, updated_at=?, note=? WHERE order_id=?", (new_status, now_iso(), str(result)[:800], order_id))
            if new_status == "failed_refunded":
                await context.bot.send_message(row["user_id"], f"❌ Order `{order_id}` failed. ৳{row['sale_price']} has been refunded to your bot balance.", parse_mode="Markdown")
            elif new_status == "completed":
                await context.bot.send_message(row["user_id"], f"✅ Supplier reports order `{order_id}` completed.", parse_mode="Markdown")
            row_status = new_status
        except Exception as exc:
            log.exception("Status check failed for %s", order_id)
            row_status = row["status"]
            await update.effective_message.reply_text(f"Could not confirm supplier status yet. Current local status: {row_status}. Please retry later or contact {WHATSAPP_SUPPORT}.")
            return
    else:
        row_status = row["status"]
    await update.effective_message.reply_text(
        f"Order: {order_id}\nPackage: {row['package_name']}\nUID: {row['player_uid']}\nAmount: ৳{row['sale_price']}\nStatus: {row_status}",
        reply_markup=home_keyboard(),
    )

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("Not authorized.")
        return
    with db() as con:
        deps = con.execute("SELECT * FROM deposits WHERE status='pending' ORDER BY created_at LIMIT 15").fetchall()
        reviews = con.execute("SELECT * FROM orders WHERE status IN ('needs_review','processing') ORDER BY created_at LIMIT 15").fetchall()
    await update.effective_message.reply_text(f"Admin panel\nPending deposits: {len(deps)}\nOrders needing review/processing: {len(reviews)}")
    for d in deps:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approve + credit", callback_data=f"depapprove:{d['deposit_id']}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"depdecline:{d['deposit_id']}"),
        ]])
        await update.effective_message.reply_text(
            f"Deposit `{d['deposit_id']}`\nUser: `{d['user_id']}`\nAmount: ৳{d['amount']}\nMethod: {d['payment_method']}\nTxn: `{d['transaction_ref']}`",
            parse_mode="Markdown", reply_markup=kb,
        )
    for o in reviews:
        await update.effective_message.reply_text(
            f"Order `{o['order_id']}` · {o['status']}\nUser: `{o['user_id']}` · UID `{o['player_uid']}`\n{o['package_name']} · ৳{o['sale_price']}\nUse `/status {o['order_id']}` to query supplier.",
            parse_mode="Markdown",
        )

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text("Cancelled. Main menu:", reply_markup=home_keyboard())

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Unhandled bot error", exc_info=context.error)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Add it in Railway Variables.")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buy", buy_cmd))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CallbackQueryHandler(on_method, pattern=r"^method:"))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)
    log.info("Free Fire top-up bot starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)

if __name__ == "__main__":
    main()
