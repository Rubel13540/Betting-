import os
import sqlite3
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, ConversationHandler, filters, ContextTypes
)

# Logging Setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- Environment Variables ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("API_KEY", "default_api_key")
BKASH_NUMBER = os.getenv("BKASH_NUMBER", "017XXXXXXXX")  # Personal bKash Number
NAGAD_NUMBER = os.getenv("NAGAD_NUMBER", "018XXXXXXXX")    # Personal Nagad Number
WHATSAPP_NUMBER = os.getenv("WHATSAPP_NUMBER", "88017XXXXXXXX") # WhatsApp Number (Country code shoho)
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

DB_NAME = "bot_database.db"

# League Logos & Icons Mapping
LEAGUE_LOGOS = {
    "Premier League": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 🏆",
    "La Liga": "🇪🇸 🏆",
    "Serie A": "🇮🇹 🏆",
    "Bundesliga": "🇩🇪 🏆",
    "Ligue 1": "🇫🇷 🏆"
}

# Conversation States
SELECT_METHOD, ENTER_TRX = range(2)

# ================= DATABASE FUNCTIONS =================

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            subscribed_until TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS payment_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            plan_months INTEGER,
            method TEXT,
            amount INTEGER,
            trx_id TEXT,
            status TEXT DEFAULT 'PENDING'
        )
    ''')
    
    conn.commit()
    conn.close()

def is_subscribed(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT subscribed_until FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row and row[0]:
        expiry = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        if expiry > datetime.now():
            return True
    return False

def add_subscription(user_id, months):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT subscribed_until FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    
    now = datetime.now()
    if row and row[0]:
        try:
            current_exp = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
            start_date = max(now, current_exp)
        except ValueError:
            start_date = now
    else:
        start_date = now
        
    new_exp = start_date + timedelta(days=30 * months)
    exp_str = new_exp.strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute('''
        INSERT INTO users (user_id, subscribed_until) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET subscribed_until = ?
    ''', (user_id, exp_str, exp_str))
    
    conn.commit()
    conn.close()

def create_payment_request(user_id, plan_months, method, amount, trx_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO payment_requests (user_id, plan_months, method, amount, trx_id)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, plan_months, method, amount, trx_id))
    req_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return req_id

def update_payment_status(req_id, status):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE payment_requests SET status = ? WHERE id = ?", (status, req_id))
    cursor.execute("SELECT user_id, plan_months FROM payment_requests WHERE id = ?", (req_id,))
    row = cursor.fetchone()
    conn.commit()
    conn.close()
    return row

# ================= MATCH DATA & FILTER LOGIC =================

def fetch_and_filter_matches(category_type, is_vip=False):
    sample_games = [
        {
            "id": 101, "league": "Premier League", "teams": "Arsenal vs Chelsea", 
            "time": "22:00 BST", "category": "safe_win", "odd": 1.35, "last_4_win": True,
            "tip": "Arsenal Win (Safe)"
        },
        {
            "id": 102, "league": "La Liga", "teams": "Real Madrid vs Valencia", 
            "time": "23:30 BST", "category": "8_corner_over", "odd": 1.65, "last_4_win": True,
            "tip": "Over 8.5 Corners"
        },
        {
            "id": 103, "league": "Serie A", "teams": "Inter vs Milan", 
            "time": "01:00 BST", "category": "both_team_to_score", "odd": 1.80, "last_4_win": True,
            "tip": "BTTS - Yes"
        },
        {
            "id": 104, "league": "Bundesliga", "teams": "Bayern vs Dortmund", 
            "time": "21:30 BST", "category": "ht_ft", "odd": 2.10, "last_4_win": True,
            "tip": "HT/FT - Bayern/Bayern"
        },
        {
            "id": 105, "league": "Ligue 1", "teams": "PSG vs Lyon", 
            "time": "00:00 BST", "category": "team_win", "odd": 1.50, "last_4_win": True,
            "tip": "PSG Win"
        }
    ]
    
    filtered = [g for g in sample_games if g['category'] == category_type and g['last_4_win']]
    if not is_vip:
        filtered = filtered[:1]
    return filtered

def build_tips_message(matches, category_title):
    if not matches:
        return f"⚽ **{category_title}**\n\nআজকের জন্য কোনো ফিল্টার করা ম্যাচ পাওয়া যায়নি।"
    
    msg = f"⚽ **{category_title}**\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━\n"
    total_acc_odds = 1.0
    
    for idx, m in enumerate(matches, 1):
        league_logo = LEAGUE_LOGOS.get(m['league'], "🏆")
        msg += f"**{idx}. {m['teams']}**\n"
        msg += f"🏆 লিগ: {league_logo} {m['league']}\n"
        msg += f"⏰ সময়: {m['time']}\n"
        msg += f"💡 টিপস: `{m['tip']}`\n"
        msg += f"📈 Odds: `{m['odd']}`\n"
        msg += "-------------------------------------\n"
        total_acc_odds *= m['odd']
        
    msg += f"\n🔥 **Accumulated Total Odds:** `{round(total_acc_odds, 2)}`\n"
    msg += "⚠️ *বি.দ্র: শেষ ৪টি খেলায় সফল হওয়ার রেকর্ড অনুযায়ী অটোমেটিক ফিল্টারকৃত।*"
    return msg

# ================= TELEGRAM HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🆓 Free Tips Folder", callback_data="folder_free")],
        [InlineKeyboardButton("👑 VIP Tips Folder", callback_data="folder_vip")],
        [InlineKeyboardButton("💳 VIP Subscription কিনুন", callback_data="buy_vip")],
        [InlineKeyboardButton("💬 WhatsApp-এ যোগাযোগ করুন", url=f"https://wa.me/{WHATSAPP_NUMBER}")]
    ]
    
    welcome_text = (
        "👋 **Welcome to Betting Tips Bot!**\n\n"
        "এখানে বিগত ৪ ম্যাচে উইন হওয়া গেমগুলোর টিপস ফিল্টার করে দেখানো হয়।"
    )
    
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.callback_query.edit_message_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data.startswith("vip_") or data == "folder_vip":
        if not is_subscribed(user_id):
            sub_msg = (
                "🔒 **VIP এক্সেস সংরক্ষিত!**\n\n"
                "VIP সুবিধা পেতে সাবস্ক্রিপশন ফি:\n"
                "• ১ মাস: ২০০০ টাকা\n"
                "• ২ মাস: ৩০০০ টাকা\n"
                "• ৫ মাস: ৫০০০০ টাকা\n"
                "• ১ বছর: ৬০০০ টাকা\n\n"
                "সাবস্ক্রাইব করতে নিচের বাটনে চাপুন।"
            )
            btn = [
                [InlineKeyboardButton("💳 এখনই সাবস্ক্রাইব করুন", callback_data="buy_vip")],
                [InlineKeyboardButton("💬 WhatsApp-এ যোগাযোগ", url=f"https://wa.me/{WHATSAPP_NUMBER}")]
            ]
            await query.edit_message_text(sub_msg, reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")
            return

    if data == "folder_free":
        btn = [
            [InlineKeyboardButton("🛡️ Safe Win", callback_data="free_safe_win")],
            [InlineKeyboardButton("🚩 Team Win", callback_data="free_team_win")],
            [InlineKeyboardButton("📐 8 Corner Over", callback_data="free_8_corner")],
            [InlineKeyboardButton("⚽ Both Team to Score", callback_data="free_btts")],
            [InlineKeyboardButton("⏳ HT-FT", callback_data="free_ht_ft")],
            [InlineKeyboardButton("📜 History", callback_data="free_history")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
        ]
        await query.edit_message_text("📂 **Free Tips Menu:**", reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")

    elif data == "folder_vip":
        btn = [
            [InlineKeyboardButton("🛡️ Safe Win", callback_data="vip_safe_win")],
            [InlineKeyboardButton("🚩 Team Win", callback_data="vip_team_win")],
            [InlineKeyboardButton("📐 8 Corner Over", callback_data="vip_8_corner")],
            [InlineKeyboardButton("⚽ Both Team to Score", callback_data="vip_btts")],
            [InlineKeyboardButton("⏳ HT-FT", callback_data="vip_ht_ft")],
            [InlineKeyboardButton("📜 History", callback_data="vip_history")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
        ]
        await query.edit_message_text("👑 **VIP Tips Menu:**", reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")

    elif data == "free_safe_win":
        matches = fetch_and_filter_matches("safe_win", is_vip=False)
        msg = build_tips_message(matches, "Free - Safe Win")
        btn = [[InlineKeyboardButton("🔙 Back", callback_data="folder_free")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")

    elif data == "free_team_win":
        matches = fetch_and_filter_matches("team_win", is_vip=False)
        msg = build_tips_message(matches, "Free - Team Win")
        btn = [[InlineKeyboardButton("🔙 Back", callback_data="folder_free")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")

    elif data == "free_8_corner":
        matches = fetch_and_filter_matches("8_corner_over", is_vip=False)
        msg = build_tips_message(matches, "Free - 8 Corner Over")
        btn = [[InlineKeyboardButton("🔙 Back", callback_data="folder_free")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")

    elif data == "free_btts":
        matches = fetch_and_filter_matches("both_team_to_score", is_vip=False)
        msg = build_tips_message(matches, "Free - Both Team to Score")
        btn = [[InlineKeyboardButton("🔙 Back", callback_data="folder_free")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")

    elif data == "free_ht_ft":
        matches = fetch_and_filter_matches("ht_ft", is_vip=False)
        msg = build_tips_message(matches, "Free - HT-FT")
        btn = [[InlineKeyboardButton("🔙 Back", callback_data="folder_free")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")

    elif data == "free_history":
        msg = "📜 **Free History:**\n\n• গতকাল: ৩/৩ উইন\n• ২ দিন আগে: ২/২ উইন"
        btn = [[InlineKeyboardButton("🔙 Back", callback_data="folder_free")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")

    elif data == "vip_safe_win":
        matches = fetch_and_filter_matches("safe_win", is_vip=True)
        msg = build_tips_message(matches, "VIP - Safe Win")
        btn = [[InlineKeyboardButton("🔙 Back", callback_data="folder_vip")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")

    elif data == "vip_team_win":
        matches = fetch_and_filter_matches("team_win", is_vip=True)
        msg = build_tips_message(matches, "VIP - Team Win")
        btn = [[InlineKeyboardButton("🔙 Back", callback_data="folder_vip")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")

    elif data == "vip_8_corner":
        matches = fetch_and_filter_matches("8_corner_over", is_vip=True)
        msg = build_tips_message(matches, "VIP - 8 Corner Over")
        btn = [[InlineKeyboardButton("🔙 Back", callback_data="folder_vip")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")

    elif data == "vip_btts":
        matches = fetch_and_filter_matches("both_team_to_score", is_vip=True)
        msg = build_tips_message(matches, "VIP - Both Team to Score")
        btn = [[InlineKeyboardButton("🔙 Back", callback_data="folder_vip")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")

    elif data == "vip_ht_ft":
        matches = fetch_and_filter_matches("ht_ft", is_vip=True)
        msg = build_tips_message(matches, "VIP - HT-FT")
        btn = [[InlineKeyboardButton("🔙 Back", callback_data="folder_vip")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")

    elif data == "vip_history":
        msg = "📜 **VIP High-Odds History:**\n\n• গতকাল: ৫/৫ উইন (Total Odds: 8.50)\n• ২ দিন আগে: ৪/৪ উইন (Total Odds: 6.20)"
        btn = [[InlineKeyboardButton("🔙 Back", callback_data="folder_vip")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")

    elif data == "main_menu":
        await start(update, context)

# ================= PAYMENT CONVERSATION =================

async def buy_vip_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    btn = [
        [InlineKeyboardButton("১ মাস - ২০০০ টাকা", callback_data="plan_1_2000")],
        [InlineKeyboardButton("২ মাস - ৩০০০ টাকা", callback_data="plan_2_3000")],
        [InlineKeyboardButton("৫ মাস - ৫০০০০ টাকা", callback_data="plan_5_5000")],
        [InlineKeyboardButton("১ বছর - ৬০০০ টাকা", callback_data="plan_12_6000")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
    ]
    await query.edit_message_text("মেয়াদ অনুযায়ী সাবস্ক্রিপশন প্ল্যান নির্বাচন করুন:", reply_markup=InlineKeyboardMarkup(btn))

async def plan_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    context.user_data['plan_months'] = int(parts[1])
    context.user_data['plan_amount'] = int(parts[2])
    
    btn = [
        [InlineKeyboardButton("bKash Personal", callback_data="pay_bkash")],
        [InlineKeyboardButton("Nagad Personal", callback_data="pay_nagad")]
    ]
    await query.edit_message_text("পেমেন্ট মেথড সিলেক্ট করুন:", reply_markup=InlineKeyboardMarkup(btn))
    return SELECT_METHOD

async def payment_method_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    method = "bKash" if query.data == "pay_bkash" else "Nagad"
    number = BKASH_NUMBER if method == "bKash" else NAGAD_NUMBER
    context.user_data['pay_method'] = method
    
    msg = (
        f"আপনি **{method} Personal** সিলেক্ট করেছেন।\n\n"
        f"📌 আমাদের Send Money নম্বর: `{number}`\n"
        f"💰 পরিশোধযোগ্য পরিমাণ: **{context.user_data['plan_amount']} টাকা**\n\n"
        "Send Money করার পর প্রাপ্ত **Transaction ID (Trx ID)** টি এখানে লিখে সেন্ড করুন:"
    )
    await query.edit_message_text(msg, parse_mode="Markdown")
    return ENTER_TRX

async def receive_trx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trx_id = update.message.text.strip()
    user = update.effective_user
    
    months = context.user_data.get('plan_months', 1)
    amount = context.user_data.get('plan_amount', 2000)
    method = context.user_data.get('pay_method', 'bKash')
    
    req_id = create_payment_request(user.id, months, method, amount, trx_id)
    
    await update.message.reply_text(
        "✅ **আপনার পেমেন্ট তথ্য জমা হয়েছে!**\n"
        "এডমিন ট্রানজাকশন ভেরিফাই করে অনুমোদন করলেই আপনার VIP সার্ভিস চালু হবে।",
        parse_mode="Markdown"
    )
    
    if ADMIN_CHAT_ID != 0:
        admin_msg = (
            f"🔔 **নতুন পেমেন্ট রিকোয়েস্ট (#{req_id})**\n\n"
            f"👤 ইউজার: {user.full_name} (@{user.username})\n"
            f"🆔 User ID: `{user.id}`\n"
            f"📦 প্ল্যান: {months} মাস ({amount} TK)\n"
            f"💳 মেথড: {method} Personal\n"
            f"🧾 Trx ID: `{trx_id}`"
        )
        admin_btn = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"adm_app_{req_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"adm_rej_{req_id}")
            ]
        ]
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_msg, reply_markup=InlineKeyboardMarkup(admin_btn), parse_mode="Markdown")
        
    return ConversationHandler.END

# ================= ADMIN APPROVAL =================

async def admin_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    parts = data.split("_")
    action = parts[1]
    req_id = int(parts[2])
    
    if action == "app":
        user_id, months = update_payment_status(req_id, "APPROVED")
        add_subscription(user_id, months)
        
        await query.edit_message_text(query.message.text + "\n\n✅ **Approved by Admin!**")
        await context.bot.send_message(
            chat_id=user_id,
            text=f"🎉 **অভিনন্দন!** আপনার {months} মাসের VIP সাবস্ক্রিপশন সফলভাবে একটিভ করা হয়েছে।",
            parse_mode="Markdown"
        )
    elif action == "rej":
        user_id, _ = update_payment_status(req_id, "REJECTED")
        await query.edit_message_text(query.message.text + "\n\n❌ **Rejected by Admin!**")
        await context.bot.send_message(
            chat_id=user_id,
            text="⚠️ আপনার পেমেন্ট ভেরিফিকেশনটি বাতিল করা হয়েছে। সঠিক Trx ID প্রদান করে আবার চেষ্টা করুন।"
        )

# ================= MAIN FUNCTION =================

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    
    pay_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(plan_selected, pattern="^plan_")],
        states={
            SELECT_METHOD: [CallbackQueryHandler(payment_method_selected, pattern="^pay_")],
            ENTER_TRX: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_trx)]
        },
        fallbacks=[]
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(pay_conv)
    app.add_handler(CallbackQueryHandler(buy_vip_start, pattern="^buy_vip$"))
    app.add_handler(CallbackQueryHandler(admin_approval_callback, pattern="^adm_"))
    app.add_handler(CallbackQueryHandler(callback_router))
    
    print("Bot is running...")
    app.run_polling()

if __name__ == '__main__':
    main()
