import os
import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

from database import init_db, add_user, is_vip_user, set_vip_status
from api_client import fetch_upcoming_fixtures
from predictor import analyze_fixture

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Main Menu Keyboards
KEYBOARD = [
    ["⚽ আজকের ফ্রি প্রেডিকশন", "👑 VIP টিপস"],
    ["💎 VIP মেম্বারশিপ নিন", "📞 হেল্প ও সহায়তা"]
]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.username or "Unknown")
    
    reply_markup = ReplyKeyboardMarkup(KEYBOARD, resize_keyboard=True)
    welcome_text = (
        f"আসসালামু আলাইকুম {user.first_name}! 👋\n\n"
        f"Football AI Pro Bot-এ আপনাকে স্বাগতম। "
        f"এখানে আপনি এআই ও পরিসংখ্যান ভিত্তিক নির্ভুল ফুটবল টিপস পাবেন।"
    )
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    
    if text == "⚽ আজকের ফ্রি প্রেডিকশন":
        await update.message.reply_text("⏳ ম্যাচ ডেটা বিশ্লেষণ করা হচ্ছে, অনুগ্রহ করে অপেক্ষা করুন...")
        fixtures = fetch_upcoming_fixtures()
        
        if not fixtures:
            await update.message.reply_text("দুঃখিত, এই মুহূর্তে কোনো ম্যাচের ডেটা পাওয়া যায়নি।")
            return
            
        # ১ম ২টি ম্যাচ ফ্রি প্রেডিকশন
        for fixture in fixtures[:2]:
            analysis = analyze_fixture(fixture)
            await update.message.reply_text(analysis, parse_mode="Markdown")

    elif text == "👑 VIP টিপস":
        if is_vip_user(user_id):
            await update.message.reply_text("⏳ ভিআইপি প্রেডিকশন লোড করা হচ্ছে...")
            fixtures = fetch_upcoming_fixtures()
            if len(fixtures) > 2:
                for fixture in fixtures[2:5]:
                    analysis = analyze_fixture(fixture)
                    await update.message.reply_text(f"🔥 **[VIP PREDICTION]**\n\n{analysis}", parse_mode="Markdown")
            else:
                await update.message.reply_text("বর্তমানে পর্যাপ্ত VIP ম্যাচ উপলব্ধ নেই।")
        else:
            await update.message.reply_text("❌ আপনি VIP মেম্বার নন! ভিআইপি টিপস পেতে '💎 VIP মেম্বারশিপ নিন' বাটনে ক্লিক করুন।")

    elif text == "💎 VIP মেম্বারশিপ নিন":
        vip_info = (
            "👑 **VIP মেম্বারশিপ ফি:**\n"
            "• বিকাশ/নগদ: প্রিমিয়াম অ্যাক্সেসের জন্য অ্যাডমিনের সাথে যোগাযোগ করুন।\n\n"
            "আপনার ইউজার আইডি Admin-কে পাঠান:\n"
            f"👉 আপনার Telegram ID: `{user_id}`"
        )
        await update.message.reply_text(vip_info, parse_mode="Markdown")

    elif text == "📞 হেল্প ও সহায়তা":
        await update.message.reply_text("যেকোনো প্রয়োজনে যোগাযোগ করুন অ্যাডমিনের সাথে।")

# Admin Approval Command: /approve <user_id>
async def approve_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ আপনি এই কমান্ড ব্যবহার করার অনুমতিপ্রাপ্ত নন।")
        return
        
    try:
        target_id = int(context.args[0])
        set_vip_status(target_id, True)
        await update.message.reply_text(f"✅ User ID {target_id} কে VIP এক্সেস দেওয়া হয়েছে।")
    except (IndexError, ValueError):
        await update.message.reply_text("কমান্ড ফরম্যাট: `/approve <user_id>`", parse_mode="Markdown")

def main():
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("approve", approve_vip))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
      
