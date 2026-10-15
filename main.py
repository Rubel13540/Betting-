import os
import asyncio
import logging
import socket
from urllib.parse import urlparse

import aiohttp
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters,
)

# Safe, bounded website smoke-testing bot.
# Only domains explicitly listed in TEST_ALLOWED_HOSTS may be tested.
# This is not a fake-view, ad-click, or human-traffic generator.

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("traffic-test-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
ALLOWED_HOSTS_RAW = os.getenv("TEST_ALLOWED_HOSTS", "").strip()

MAX_TESTS = 50
MIN_INTERVAL_SECONDS = 10
ALLOWED_COUNTS = [1, 5, 10, 20, 50]
ALLOWED_INTERVALS = [10, 15, 30, 60]

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required.")
if not ADMIN_ID_RAW.isdigit():
    raise RuntimeError("ADMIN_ID must be your numeric Telegram user ID.")
ADMIN_ID = int(ADMIN_ID_RAW)
ALLOWED_HOSTS = {
    h.strip().lower().rstrip(".")
    for h in ALLOWED_HOSTS_RAW.split(",")
    if h.strip()
}
if not ALLOWED_HOSTS:
    raise RuntimeError(
        "Set TEST_ALLOWED_HOSTS to a comma-separated list of domains you own, "
        "for example: example.com,test.example.com"
    )

def home_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 নতুন টেস্ট", callback_data="new")],
        [InlineKeyboardButton("🏠 Home", callback_data="home")],
    ])

def cancel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏹ Stop", callback_data="stop"),
         InlineKeyboardButton("🏠 Home", callback_data="home")]
    ])

def count_keyboard():
    rows, row = [], []
    for n in ALLOWED_COUNTS:
        row.append(InlineKeyboardButton(str(n), callback_data=f"count:{n}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🏠 Home", callback_data="home")])
    return InlineKeyboardMarkup(rows)

def interval_keyboard():
    rows = [
        [InlineKeyboardButton(f"{n} সেকেন্ড", callback_data=f"interval:{n}")]
        for n in ALLOWED_INTERVALS
    ]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="back_count"),
                 InlineKeyboardButton("🏠 Home", callback_data="home")])
    return InlineKeyboardMarkup(rows)

def is_authorized(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == ADMIN_ID)

def validate_url(raw: str):
    raw = raw.strip()
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.hostname:
        return None, "শুধু বৈধ HTTPS URL ব্যবহার করুন।"
    host = parsed.hostname.lower().rstrip(".")
    # Exact host allowlist only; no arbitrary subdomains.
    if host not in ALLOWED_HOSTS:
        return None, (
            "এই ডোমেইন অনুমোদিত তালিকায় নেই। নিজের ডোমেইনটি Railway-এর "
            "TEST_ALLOWED_HOSTS-এ যোগ করুন।"
        )
    if parsed.username or parsed.password:
        return None, "URL-এর মধ্যে username/password রাখা যাবে না।"
    return parsed.geturl(), None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.effective_message.reply_text("দুঃখিত, এই বটটি ব্যক্তিগতভাবে সীমাবদ্ধ।")
        return
    context.user_data.clear()
    await update.effective_message.reply_text(
        "🌐 ওয়েবসাইট টেস্ট বটে স্বাগতম!\n\n"
        "এটি শুধু TEST_ALLOWED_HOSTS-এ থাকা অনুমোদিত HTTPS ডোমেইনে "
        "সীমিত health-check চালায়। এটি বিজ্ঞাপনের ক্লিক বা ভুয়া ভিউ তৈরি করে না।\n\n"
        "শুরু করতে নিচের বাটন চাপুন।",
        reply_markup=home_keyboard(),
    )

async def show_count(query, context):
    context.user_data["stage"] = "count"
    await query.edit_message_text(
        "কতটি সীমিত পেজ-চেক চালাতে চান?\n"
        f"সর্বোচ্চ {MAX_TESTS}টি। যেকোনো ব্যর্থতায় টেস্ট থেমে যাবে।",
        reply_markup=count_keyboard(),
    )

async def run_checks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    data = context.user_data
    url = data["url"]
    count = data["count"]
    interval = data["interval"]
    stop_event = asyncio.Event()
    data["stop_event"] = stop_event
    data["running"] = True

    completed = 0
    failures = 0
    last_error = ""
    status_counts = {}
    await context.bot.send_message(
        chat_id,
        f"▶️ টেস্ট শুরু\nURL: {url}\nলক্ষ্য: {count}\n"
        f"বিরতি: {interval} সেকেন্ড\nপ্রতি ২০টি সম্পন্ন হলে রিপোর্ট।",
        reply_markup=cancel_keyboard(),
        disable_web_page_preview=True,
    )

    timeout = aiohttp.ClientTimeout(total=15, connect=7)
    connector = aiohttp.TCPConnector(limit=1, ttl_dns_cache=60)
    headers = {"User-Agent": "AuthorizedWebsiteHealthCheck/1.0"}
    try:
        async with aiohttp.ClientSession(
            timeout=timeout, connector=connector, headers=headers
        ) as session:
            for i in range(1, count + 1):
                if stop_event.is_set():
                    break
                try:
                    async with session.get(url, allow_redirects=False) as response:
                        # Read a small amount only; don't download large pages.
                        await response.content.read(2048)
                        status_counts[response.status] = status_counts.get(response.status, 0) + 1
                        if response.status >= 400:
                            failures += 1
                            last_error = f"HTTP {response.status}"
                            await context.bot.send_message(
                                chat_id,
                                f"❌ টেস্ট বন্ধ: {last_error}\n"
                                f"সম্পন্ন: {completed}/{count}\n"
                                "ব্যর্থ হলে নির্দেশনা অনুযায়ী কাজ থামানো হয়েছে।",
                                reply_markup=home_keyboard(),
                            )
                            break
                        completed += 1
                except asyncio.TimeoutError:
                    failures += 1
                    last_error = "Timeout: ১৫ সেকেন্ডের মধ্যে উত্তর আসেনি"
                except aiohttp.ClientConnectorError as e:
                    failures += 1
                    last_error = f"সংযোগ/DNS সমস্যা: {type(e).__name__}"
                except aiohttp.ClientError as e:
                    failures += 1
                    last_error = f"নেটওয়ার্ক সমস্যা: {type(e).__name__}"
                except Exception as e:
                    failures += 1
                    last_error = f"অপ্রত্যাশিত সমস্যা: {type(e).__name__}"

                if failures:
                    await context.bot.send_message(
                        chat_id,
                        f"❌ টেস্ট বন্ধ করা হয়েছে।\nকারণ: {last_error}\n"
                        f"সফল চেক: {completed}\nব্যর্থ: {failures}\n"
                        f"HTTP স্ট্যাটাস: {status_counts or 'কোনো উত্তর নেই'}",
                        reply_markup=home_keyboard(),
                    )
                    break

                if completed > 0 and completed % 20 == 0:
                    await context.bot.send_message(
                        chat_id,
                        f"📊 অগ্রগতি: {completed}/{count}\n"
                        f"ব্যর্থ: {failures}\nHTTP স্ট্যাটাস: {status_counts}",
                        reply_markup=cancel_keyboard(),
                    )

                if i < count and not stop_event.is_set():
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=interval)
                    except asyncio.TimeoutError:
                        pass

        if stop_event.is_set():
            await context.bot.send_message(
                chat_id,
                f"⏹ টেস্ট থামানো হয়েছে।\nসফল চেক: {completed}/{count}\n"
                f"ব্যর্থ: {failures}\nHTTP স্ট্যাটাস: {status_counts}",
                reply_markup=home_keyboard(),
            )
        elif failures == 0:
            await context.bot.send_message(
                chat_id,
                f"✅ টেস্ট শেষ।\nসফল চেক: {completed}/{count}\n"
                f"ব্যর্থ: {failures}\nHTTP স্ট্যাটাস: {status_counts}",
                reply_markup=home_keyboard(),
            )
    finally:
        data["running"] = False
        data.pop("stop_event", None)
        data["stage"] = "home"

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_authorized(update):
        await query.edit_message_text("দুঃখিত, অনুমতি নেই।")
        return
    data = context.user_data
    action = query.data

    if action == "home":
        if data.get("running") and data.get("stop_event"):
            data["stop_event"].set()
            await query.edit_message_text(
                "⏹ চলমান টেস্ট বন্ধ করার অনুরোধ পাঠানো হয়েছে। রিপোর্ট আসার পর Home-এ ফিরুন।"
            )
            return
        data.clear()
        await query.edit_message_text("🏠 Home\nকী করতে চান?", reply_markup=home_keyboard())
    elif action == "new":
        if data.get("running"):
            await query.edit_message_text("একটি টেস্ট চলছে। আগে Stop করুন।", reply_markup=cancel_keyboard())
            return
        data.clear()
        await show_count(query, context)
    elif action.startswith("count:"):
        count = int(action.split(":")[1])
        if count not in ALLOWED_COUNTS:
            await query.edit_message_text("অবৈধ সংখ্যা। Home-এ ফিরুন।", reply_markup=home_keyboard())
            return
        data["count"] = count
        data["stage"] = "interval"
        await query.edit_message_text(
            "প্রতিটি চেকের মধ্যে কত সেকেন্ড বিরতি চান?",
            reply_markup=interval_keyboard(),
        )
    elif action == "back_count":
        await show_count(query, context)
    elif action.startswith("interval:"):
        interval = int(action.split(":")[1])
        if interval not in ALLOWED_INTERVALS or interval < MIN_INTERVAL_SECONDS:
            await query.edit_message_text("অবৈধ বিরতি।", reply_markup=home_keyboard())
            return
        data["interval"] = interval
        data["stage"] = "url"
        await query.edit_message_text(
            "এখন নিজের অনুমোদিত সাইটের HTTPS URL পাঠান।\n"
            "শুধু TEST_ALLOWED_HOSTS-এ থাকা ডোমেইন গ্রহণ করা হবে।",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="new"),
                 InlineKeyboardButton("🏠 Home", callback_data="home")]
            ]),
        )
    elif action == "stop":
        if data.get("running") and data.get("stop_event"):
            data["stop_event"].set()
            await query.edit_message_text("⏹ Stop অনুরোধ পাঠানো হয়েছে। রিপোর্ট আসছে।")
        else:
            await query.edit_message_text("এখন কোনো টেস্ট চলছে না।", reply_markup=home_keyboard())

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("দুঃখিত, এই বটটি ব্যক্তিগতভাবে সীমাবদ্ধ।")
        return
    data = context.user_data
    if data.get("running"):
        await update.message.reply_text("টেস্ট চলছে। বন্ধ করতে Stop চাপুন।", reply_markup=cancel_keyboard())
        return
    if data.get("stage") != "url":
        await update.message.reply_text("মেনু থেকে শুরু করুন।", reply_markup=home_keyboard())
        return
    url, error = validate_url(update.message.text)
    if error:
        await update.message.reply_text(f"⚠️ {error}", reply_markup=home_keyboard())
        return
    data["url"] = url
    await update.message.reply_text(
        f"টেস্ট নিশ্চিত করুন:\nURL: {url}\n"
        f"চেক: {data['count']}\nবিরতি: {data['interval']} সেকেন্ড\n\n"
        "শুরু করতে Confirm চাপুন।",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Confirm & Start", callback_data="confirm")],
            [InlineKeyboardButton("⬅️ Back", callback_data="new"),
             InlineKeyboardButton("🏠 Home", callback_data="home")],
        ]),
        disable_web_page_preview=True,
    )
    data["stage"] = "confirm"

async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_authorized(update):
        await query.edit_message_text("দুঃখিত, অনুমতি নেই।")
        return
    if query.data != "confirm":
        return
    data = context.user_data
    if data.get("running"):
        await query.edit_message_text("একটি টেস্ট ইতিমধ্যে চলছে।", reply_markup=cancel_keyboard())
        return
    if data.get("stage") != "confirm" or not data.get("url"):
        await query.edit_message_text("টেস্ট সেটিংস নেই। Home থেকে আবার শুরু করুন.", reply_markup=home_keyboard())
        return
    data["stage"] = "running"
    await query.edit_message_text("কনফিগারেশন ঠিক আছে। টেস্ট চালু হচ্ছে…")
    task = asyncio.create_task(run_checks(update, context))
    data["task"] = task

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Unhandled bot error", exc_info=context.error)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(confirm_callback, pattern="^confirm$"))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(error_handler)
    log.info("Bot starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
