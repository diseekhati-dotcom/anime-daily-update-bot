import asyncio
import logging
import os
from datetime import time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from anime_service import build_message

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("anime_daily_update_bot")

TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
TZ_NAME = os.getenv("TZ", "Asia/Kolkata").strip()

if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing.")
if not CHAT_ID:
    raise RuntimeError("CHAT_ID environment variable is missing.")

try:
    TARGET_CHAT = int(CHAT_ID)
except ValueError:
    TARGET_CHAT = CHAT_ID

TZ = ZoneInfo(TZ_NAME)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Anime Daily Update Bot active.\n"
        "Daily schedule: 5:00 PM IST\n"
        "Source: DC"
    )

async def chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"CHAT_ID: {update.effective_chat.id}")

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(TARGET_CHAT):
        await update.message.reply_text("❌ Test command is only enabled in the configured chat.")
        return
    msg = build_message()
    await context.bot.send_message(chat_id=TARGET_CHAT, text=msg)

async def send_daily():
    from telegram import Bot
    msg = build_message()
    async with Bot(TOKEN) as bot:
        await bot.send_message(chat_id=TARGET_CHAT, text=msg)
    log.info("Daily anime update sent.")

async def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("chatid", chatid))
    app.add_handler(CommandHandler("test", test))

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(
        send_daily,
        trigger="cron",
        hour=17,
        minute=0,
        id="daily_anime_update",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.start()

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    log.info("Bot running. Daily post scheduled for 17:00 %s.", TZ_NAME)
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
