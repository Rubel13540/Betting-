# Football Tips + VIP Telegram Bot (Railway)

## Files
- `main.py`
- `requirements.txt`
- `Procfile`

## Railway Variables
Add these in Railway → your service → Variables:
- `BOT_TOKEN` = Telegram bot token from @BotFather
- `API_FOOTBALL_KEY` = API-Football API key
- `ADMIN_ID` = `5293614793` (or your numeric Telegram ID)
- `BKASH_PERSONAL` = your bKash Personal number
- `NAGAD_PERSONAL` = your Nagad Personal number
- `WHATSAPP_SUPPORT` = `https://wa.me/message/ZFPUNOUHWSWRI1`
- `TIMEZONE` = `Asia/Dhaka`

For safer secret handling, store bot/API credentials in Railway Variables. Do not commit `.env` or secrets to GitHub.

## Deploy
1. Upload these files to a GitHub repository root.
2. In Railway, create a project from the GitHub repository.
3. Add all variables above and deploy.
4. Check deployment logs for `Football VIP bot starting`.

## Bot features
- Home menu with fixture/prediction/odds/live/history/VIP/support buttons
- Upcoming matches in the next 12 hours
- API-Football predictions and odds when available for a fixture
- VIP plans: 1 month ৳2,000; 2 months ৳3,000; 6 months ৳4,000; 1 year ৳5,000
- bKash/Nagad Personal payment request with TrxID submission
- Admin notification with Approve/Reject buttons
- VIP activation on approval; expiry calculated automatically
- Admin commands: `/tip`, `/mytips`, `/settle`, `/pending`

## Important limitations / setup notes
- Odds and predictions depend on your API-Football plan, league coverage and request quota. The bot does not invent missing odds.
- Payment is NOT automatically verified. Admin must check the payment in the official bKash/Nagad account before pressing Approve.
- This first version stores SQLite in the app filesystem. Railway files may be ephemeral. Attach a Railway Volume and set `DB_PATH=/data/bot.sqlite3` (mount volume at `/data`) if supported by your service; otherwise use PostgreSQL for persistent data.
- `View History` displays manually saved Admin tips and their WIN/LOSS/VOID status, not every raw API prediction.
- `Daily Sure Tips` is an API-data-based prediction list, not guaranteed sure wins.
- A basic VIP command gate is included. To make separate VIP-only menu categories/content, add your private tip publishing workflow next.
- Run one Railway replica only when using Telegram polling to avoid duplicate polling conflicts.
