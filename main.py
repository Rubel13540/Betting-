import os
import sqlite3
import logging
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

load_dotenv()
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = os.getenv("DATABASE_PATH", "bot_data.db")
COMMISSION_RATE = float(os.getenv("COMMISSION_RATE", "2.0"))
ADMIN_CUT_RATE = float(os.getenv("ADMIN_CUT_RATE", "1.0"))
ADMIN_CUT_BASE = os.getenv("ADMIN_CUT_BASE", "commission")

# Conversation states
(
    ASK_WORKER_ID, ASK_WORKER_NAME,
    ASK_POOL_AMOUNT,
    ASK_GIVE_AMOUNT, ASK_TAKE_AMOUNT,
    ASK_DEPOSIT_AMOUNT, ASK_WITHDRAW_AMOUNT, ASK_CLAIM_AMOUNT,
) = range(8)

# ==================== DATABASE ====================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS admin_pool (
        id INTEGER PRIMARY KEY CHECK (id=1),
        balance REAL DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS workers (
        user_id INTEGER PRIMARY KEY,
        name TEXT UNIQUE,
        balance REAL DEFAULT 0,
        commission_balance REAL DEFAULT 0,
        total_commission REAL DEFAULT 0,
        total_admin_cut REAL DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, type TEXT, amount REAL,
        balance_after REAL, note TEXT, timestamp TEXT)''')
    c.execute('INSERT OR IGNORE INTO admin_pool (id, balance) VALUES (1, 0)')
    conn.commit()
    conn.close()

def db():
    return sqlite3.connect(DB_PATH)

def get_pool():
    conn = db(); c = conn.cursor()
    c.execute('SELECT balance FROM admin_pool WHERE id=1')
    r = c.fetchone(); conn.close()
    return r[0] if r else 0

def set_pool(amount):
    conn = db(); c = conn.cursor()
    c.execute('UPDATE admin_pool SET balance=? WHERE id=1', (amount,))
    conn.commit(); conn.close()

def add_pool(delta):
    set_pool(get_pool() + delta)

def get_worker(user_id):
    conn = db(); c = conn.cursor()
    c.execute('SELECT user_id,name,balance,commission_balance,total_commission,total_admin_cut FROM workers WHERE user_id=?', (user_id,))
    r = c.fetchone(); conn.close()
    return r

def get_worker_by_name(name):
    conn = db(); c = conn.cursor()
    c.execute('SELECT user_id,name,balance,commission_balance FROM workers WHERE name=?', (name,))
    r = c.fetchone(); conn.close()
    return r

def get_all_workers():
    conn = db(); c = conn.cursor()
    c.execute('SELECT user_id,name,balance,commission_balance FROM workers ORDER BY name')
    r = c.fetchall(); conn.close()
    return r

def add_worker(user_id, name):
    conn = db(); c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO workers (user_id,name,balance,commission_balance,total_commission,total_admin_cut) VALUES (?, ?, COALESCE((SELECT balance FROM workers WHERE user_id=?),0), COALESCE((SELECT commission_balance FROM workers WHERE user_id=?),0), COALESCE((SELECT total_commission FROM workers WHERE user_id=?),0), COALESCE((SELECT total_admin_cut FROM workers WHERE user_id=?),0))',
              (user_id, name, user_id, user_id, user_id, user_id))
    conn.commit(); conn.close()

def remove_worker(user_id):
    conn = db(); c = conn.cursor()
    c.execute('DELETE FROM workers WHERE user_id=?', (user_id,))
    conn.commit(); conn.close()

def update_worker_balance(user_id, delta):
    conn = db(); c = conn.cursor()
    c.execute('UPDATE workers SET balance = balance + ? WHERE user_id=?', (delta, user_id))
    conn.commit()
    c.execute('SELECT balance FROM workers WHERE user_id=?', (user_id,))
    b = c.fetchone()[0]; conn.close()
    return b

def log_txn(user_id, ttype, amount, balance_after, note=""):
    conn = db(); c = conn.cursor()
    c.execute('INSERT INTO transactions (user_id,type,amount,balance_after,note,timestamp) VALUES (?,?,?,?,?,?)',
              (user_id, ttype, amount, balance_after, note, datetime.now().isoformat()))
    conn.commit(); conn.close()

def get_history(user_id, limit=10):
    conn = db(); c = conn.cursor()
    c.execute('SELECT type,amount,balance_after,timestamp FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT ?', (user_id, limit))
    r = c.fetchall(); conn.close()
    return r

# ==================== COMMISSION ====================
def process_commission(user_id, deposit_amount):
    commission = deposit_amount * (COMMISSION_RATE / 100)
    if ADMIN_CUT_BASE == "commission":
        admin_cut = commission * (ADMIN_CUT_RATE / 100)
    else:
        admin_cut = deposit_amount * (ADMIN_CUT_RATE / 100)
    worker_comm = commission - admin_cut
    if worker_comm < 0:
        worker_comm = 0

    conn = db(); c = conn.cursor()
    c.execute('''UPDATE workers SET
        commission_balance = commission_balance + ?,
        total_commission = total_commission + ?,
        total_admin_cut = total_admin_cut + ?
        WHERE user_id=?''', (worker_comm, commission, admin_cut, user_id))
    conn.commit(); conn.close()

    if admin_cut > 0:
        add_pool(admin_cut)

    return commission, admin_cut, worker_comm

# ==================== KEYBOARDS ====================
def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Pool Management", callback_data="pool")],
        [InlineKeyboardButton("👥 Workers", callback_data="workers")],
        [InlineKeyboardButton("💸 Give / Take", callback_data="give_take")],
        [InlineKeyboardButton("📊 Report", callback_data="report")],
        [InlineKeyboardButton("👑 Commission Report", callback_data="comm_report")],
    ])

def pool_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Money", callback_data="pool_add")],
        [InlineKeyboardButton("✏️ Set Balance", callback_data="pool_set")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_home")],
    ])

def workers_menu():
    rows = []
    for uid, name, bal, comm in get_all_workers():
        rows.append([InlineKeyboardButton(f"👤 {name} — {bal:.2f}৳", callback_data=f"w_{uid}")])
    rows.append([InlineKeyboardButton("➕ Add Worker", callback_data="w_add")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="admin_home")])
    return InlineKeyboardMarkup(rows)

def worker_detail_menu(uid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💸 Give", callback_data=f"give_{uid}")],
        [InlineKeyboardButton("📥 Take", callback_data=f"take_{uid}")],
        [InlineKeyboardButton("📜 History", callback_data=f"hist_{uid}")],
        [InlineKeyboardButton("🗑 Remove", callback_data=f"rm_{uid}")],
        [InlineKeyboardButton("🔙 Back", callback_data="workers")],
    ])

def give_take_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💸 Give Money", callback_data="gt_give")],
        [InlineKeyboardButton("📥 Take Money", callback_data="gt_take")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_home")],
    ])

def worker_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 My Balance", callback_data="w_balance")],
        [InlineKeyboardButton("📉 Deposit", callback_data="w_deposit")],
        [InlineKeyboardButton("📈 Withdraw", callback_data="w_withdraw")],
        [InlineKeyboardButton("🎁 My Commission", callback_data="w_comm")],
        [InlineKeyboardButton("📜 History", callback_data="w_hist")],
    ])

def cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])

# ==================== START ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    context.user_data.clear()
    if uid == ADMIN_ID:
        await update.message.reply_text(
            f"👑 ADMIN PANEL\n\n💰 Pool: {get_pool():.2f}৳",
            reply_markup=admin_menu()
        )
    else:
        w = get_worker(uid)
        if w:
            await update.message.reply_text(
                f"👋 হ্যালো {w[1]}\n💰 Balance: {w[2]:.2f}৳\n🎁 Commission: {w[3]:.2f}৳",
                reply_markup=worker_menu()
            )
        else:
            await update.message.reply_text(
                f"❌ আপনি রেজিস্টার্ড নন।\n\nআপনার Telegram ID: {uid}\nএটি অ্যাডমিনকে দিন।"
            )

# ==================== CALLBACK ROUTER ====================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    uid = q.from_user.id

    # Admin routes
    if data == "admin_home":
        await q.edit_message_text(f"👑 ADMIN PANEL\n\n💰 Pool: {get_pool():.2f}৳", reply_markup=admin_menu()); return

    if data == "pool":
        await q.edit_message_text(f"💰 POOL\n\nCurrent: {get_pool():.2f}৳", reply_markup=pool_menu()); return

    if data == "pool_add":
        context.user_data['action'] = ('pool_add', None)
        await q.edit_message_text("➕ কত টাকা যোগ করবেন? (শুধু সংখ্যা লিখুন)", reply_markup=cancel_kb()); return

    if data == "pool_set":
        context.user_data['action'] = ('pool_set', None)
        await q.edit_message_text("✏️ নতুন ব্যালেন্স কত?", reply_markup=cancel_kb()); return

    if data == "workers":
        await q.edit_message_text("👥 WORKERS", reply_markup=workers_menu()); return

    if data == "w_add":
        context.user_data['action'] = ('add_worker_id', None)
        await q.edit_message_text("➕ Worker এর Telegram ID পাঠান:", reply_markup=cancel_kb()); return

    if data.startswith("w_") and data[2:].isdigit():
        wid = int(data[2:])
        w = get_worker(wid)
        if w:
            await q.edit_message_text(
                f"👤 {w[1]} (ID: {w[0]})\n💰 Balance: {w[2]:.2f}৳\n🎁 Commission: {w[3]:.2f}৳\n📊 মোট কমিশন: {w[4]:.2f}৳\n👑 Admin Cut: {w[5]:.2f}৳",
                reply_markup=worker_detail_menu(wid)); return

    if data.startswith("give_") and data[5:].isdigit():
        wid = int(data[5:])
        context.user_data['action'] = ('give', wid)
        await q.edit_message_text(f"💸 কত টাকা দেবেন worker ID {wid} কে?", reply_markup=cancel_kb()); return

    if data.startswith("take_") and data[5:].isdigit():
        wid = int(data[5:])
        context.user_data['action'] = ('take', wid)
        await q.edit_message_text(f"📥 কত টাকা নেবেন worker ID {wid} থেকে?", reply_markup=cancel_kb()); return

    if data.startswith("hist_") and data[5:].isdigit():
        wid = int(data[5:])
        rows = get_history(wid, 15)
        text = f"📜 History (ID {wid}):\n\n"
        for t, a, b, ts in rows:
            text += f"• {t}: {a:.2f} → {b:.2f} ({ts[:16]})\n"
        if not rows: text += "(খালি)"
        await q.edit_message_text(text, reply_markup=worker_detail_menu(wid)); return

    if data.startswith("rm_") and data[3:].isdigit():
        wid = int(data[3:])
        remove_worker(wid)
        await q.edit_message_text("✅ Worker সরানো হলো", reply_markup=workers_menu()); return

    if data == "give_take":
        await q.edit_message_text("💸 GIVE / TAKE", reply_markup=give_take_menu()); return

    if data == "gt_give":
        rows = [[InlineKeyboardButton(f"{name}", callback_data=f"give_{uid_2}")] for uid_2, name, _, _ in get_all_workers()]
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="give_take")])
        await q.edit_message_text("কোন Worker কে দেবেন?", reply_markup=InlineKeyboardMarkup(rows)); return

    if data == "gt_take":
        rows = [[InlineKeyboardButton(f"{name}", callback_data=f"take_{uid_2}")] for uid_2, name, _, _ in get_all_workers()]
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="give_take")])
        await q.edit_message_text("কোন Worker থেকে নেবেন?", reply_markup=InlineKeyboardMarkup(rows)); return

    if data == "report":
        workers = get_all_workers()
        text = f"📊 REPORT\n\n🏦 Pool: {get_pool():.2f}৳\n\n👥 Workers:\n"
        total_w = 0
        for wu, name, bal, comm in workers:
            text += f"• {name}: {bal:.2f}৳ (comm: {comm:.2f}৳)\n"
            total_w += bal + comm
        text += f"\n💰 Workers Total: {total_w:.2f}৳\n💎 Grand Total: {total_w + get_pool():.2f}৳"
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_home")]])); return

    if data == "comm_report":
        workers = get_all_workers()
        text = "👑 COMMISSION REPORT\n\n"
        total_earned = 0
        total_admin = 0
        for wu, name, bal, comm in workers:
            w = get_worker(wu)
            text += f"• {name}\n  Earned: {w[4]:.2f}৳ | Admin Cut: {w[5]:.2f}৳\n"
            total_earned += w[4]; total_admin += w[5]
        text += f"\n🎁 মোট ওয়ার্কার কমিশন: {total_earned:.2f}৳\n👑 আপনার মোট কাট: {total_admin:.2f}৳"
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_home")]])); return

    # Worker routes
    if data == "w_balance":
        w = get_worker(uid)
        await q.edit_message_text(f"💰 Balance: {w[2]:.2f}৳\n🎁 Commission: {w[3]:.2f}৳", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="w_home")]])); return

    if data == "w_home":
        w = get_worker(uid)
        await q.edit_message_text(f"👋 {w[1]}\n💰 Balance: {w[2]:.2f}৳\n🎁 Commission: {w[3]:.2f}৳", reply_markup=worker_menu()); return

    if data == "w_deposit":
        context.user_data['action'] = ('deposit', None)
        w = get_worker(uid)
        await q.edit_message_text(f"📉 Deposit\nBalance: {w[2]:.2f}৳\n\nকত টাকা ডিপোজিট করবেন?", reply_markup=cancel_kb()); return

    if data == "w_withdraw":
        context.user_data['action'] = ('withdraw', None)
        await q.edit_message_text("📈 Withdraw\n\nকত টাকা withdraw করবেন?", reply_markup=cancel_kb()); return

    if data == "w_comm":
        w = get_worker(uid)
        await q.edit_message_text(
            f"🎁 Commission Balance: {w[3]:.2f}৳\n📈 Total Earned: {w[4]:.2f}৳\n\n"
            f"কমিশন মূল ব্যালেন্সে নিতে /claim <amount> দিন",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="w_home")]])); return

    if data == "w_hist":
        rows = get_history(uid, 15)
        text = "📜 আপনার History:\n\n"
        for t, a, b, ts in rows:
            text += f"• {t}: {a:.2f} → {b:.2f} ({ts[:16]})\n"
        if not rows: text += "(খালি)"
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="w_home")]])); return

    if data == "cancel":
        context.user_data.clear()
        if uid == ADMIN_ID:
            await q.edit_message_text(f"👑 ADMIN PANEL\n\n💰 Pool: {get_pool():.2f}৳", reply_markup=admin_menu())
        else:
            w = get_worker(uid)
            if w:
                await q.edit_message_text(f"👋 {w[1]}\n💰 Balance: {w[2]:.2f}৳", reply_markup=worker_menu())
        return

# ==================== TEXT HANDLER ====================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    action = context.user_data.get('action')
    if not action:
        return
    a_type, target = action
    text = update.message.text.strip()

    try:
        amount = float(text) if a_type != 'add_worker_id' and a_type != 'add_worker_name' else None
    except:
        await update.message.reply_text("❌ সংখ্যা সঠিক নয়", reply_markup=cancel_kb()); return

    # ---- ADMIN ACTIONS ----
    if uid == ADMIN_ID:
        if a_type == 'pool_add':
            add_pool(amount)
            context.user_data.clear()
            await update.message.reply_text(f"✅ Pool: {get_pool():.2f}৳", reply_markup=admin_menu()); return

        if a_type == 'pool_set':
            set_pool(amount)
            context.user_data.clear()
            await update.message.reply_text(f"✅ Pool Set: {get_pool():.2f}৳", reply_markup=admin_menu()); return

        if a_type == 'add_worker_id':
            try:
                wid = int(text)
            except:
                await update.message.reply_text("❌ ভুল ID"); return
            context.user_data['new_worker_id'] = wid
            context.user_data['action'] = ('add_worker_name', None)
            await update.message.reply_text("এখন Worker এর নাম পাঠান:", reply_markup=cancel_kb()); return

        if a_type == 'add_worker_name':
            wid = context.user_data.get('new_worker_id')
            add_worker(wid, text)
            context.user_data.clear()
            await update.message.reply_text(f"✅ Worker {text} (ID {wid}) যোগ হলো", reply_markup=workers_menu()); return

        if a_type == 'give':
            if amount <= 0: await update.message.reply_text("০ এর বেশি দিন"); return
            if get_pool() < amount:
                await update.message.reply_text(f"❌ পুলে মাত্র {get_pool():.2f}৳"); return
            new_bal = update_worker_balance(target, amount)
            add_pool(-amount)
            log_txn(target, "admin_give", amount, new_bal, "from admin")
            context.user_data.clear()
            await update.message.reply_text(f"✅ দেওয়া হলো। Worker balance: {new_bal:.2f}৳, Pool: {get_pool():.2f}৳", reply_markup=workers_menu())
            try:
                await context.bot.send_message(target, f"💰 অ্যাডমিন {amount:.2f}৳ দিয়েছেন। ব্যালেন্স: {new_bal:.2f}৳")
            except: pass
            return

        if a_type == 'take':
            w = get_worker(target)
            if not w or w[2] < amount:
                await update.message.reply_text(f"❌ Worker এর কাছে পর্যাপ্ত নেই"); return
            new_bal = update_worker_balance(target, -amount)
            add_pool(amount)
            log_txn(target, "admin_take", amount, new_bal, "to admin")
            context.user_data.clear()
            await update.message.reply_text(f"✅ নেওয়া হলো। Worker: {new_bal:.2f}৳, Pool: {get_pool():.2f}৳", reply_markup=workers_menu())
            try:
                await context.bot.send_message(target, f"📤 অ্যাডমিন {amount:.2f}৳ নিয়েছেন। ব্যালেন্স: {new_bal:.2f}৳")
            except: pass
            return

    # ---- WORKER ACTIONS ----
    w = get_worker(uid)
    if not w:
        context.user_data.clear()
        await update.message.reply_text("রেজিস্টার্ড নন"); return

    if a_type == 'deposit':
        if amount <= 0: await update.message.reply_text("০ এর বেশি দিন"); return
        if w[2] < amount:
            await update.message.reply_text(f"❌ ব্যালেন্স মাত্র {w[2]:.2f}৳", reply_markup=worker_menu()); return
        new_bal = update_worker_balance(uid, -amount)
        log_txn(uid, "deposit", amount, new_bal, "to site")
        commission, admin_cut, worker_comm = process_commission(uid, amount)
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ Deposit: {amount:.2f}৳\n"
            f"🎁 আপনার কমিশন: {worker_comm:.2f}৳\n"
            f"💰 নতুন ব্যালেন্স: {new_bal:.2f}৳",
            reply_markup=worker_menu())
        try:
            await context.bot.send_message(ADMIN_ID,
                f"📉 {w[1]} deposit {amount:.2f}৳\n"
                f"👑 আপনার কাট: {admin_cut:.2f}৳\n"
                f"🎁 Worker comm: {worker_comm:.2f}৳\n"
                f"💰 Pool: {get_pool():.2f}৳")
        except: pass
        return

    if a_type == 'withdraw':
        if amount <= 0: await update.message.reply_text("০ এর বেশি দিন"); return
        new_bal = update_worker_balance(uid, amount)
        log_txn(uid, "withdraw", amount, new_bal, "from site")
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ Withdraw: {amount:.2f}৳\n💰 নতুন ব্যালেন্স: {new_bal:.2f}৳",
            reply_markup=worker_menu())
        try:
            await context.bot.send_message(ADMIN_ID, f"📈 {w[1]} withdraw {amount:.2f}৳ | Balance: {new_bal:.2f}৳")
        except: pass
        return

# ==================== CLAIM ====================
async def claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid == ADMIN_ID: return
    try:
        amount = float(context.args[0])
    except:
        await update.message.reply_text("ব্যবহার: /claim <amount>"); return
    w = get_worker(uid)
    if not w or w[3] < amount:
        await update.message.reply_text(f"❌ কমিশন মাত্র {w[3]:.2f}৳"); return
    conn = db(); c = conn.cursor()
    c.execute('UPDATE workers SET commission_balance = commission_balance - ? WHERE user_id=?', (amount, uid))
    conn.commit(); conn.close()
    new_bal = update_worker_balance(uid, amount)
    log_txn(uid, "claim", amount, new_bal, "commission→balance")
    await update.message.reply_text(f"✅ {amount:.2f}৳ কমিশন মূল ব্যালেন্সে যোগ হলো।\n💰 Balance: {new_bal:.2f}৳")

# ==================== MAIN ====================
def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN সেট করুন"); return
    if not ADMIN_ID:
        print("❌ ADMIN_ID সেট করুন"); return
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("claim", claim))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    print("🚀 Bot চালু...")
    app.run_polling()

if __name__ == "__main__":
    main()
