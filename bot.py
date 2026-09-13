import asyncio
import logging
import os
from datetime import datetime
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
TZ_NAME = os.getenv("TZ", "Asia/Kolkata").strip()

if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing.")

TZ = ZoneInfo(TZ_NAME)

# In-memory chat registry. A persistent external store can be added later.
# Chats are automatically registered when they use /start or /activate.
subscribers = set()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat:
        return

    subscribers.add(chat.id)

    if chat.type == "private":
        text = (
            "✅ Anime Daily Update Bot Active!\n\n"
            "Aapka DM daily update list me add ho gaya hai. "
            "Har din 5:00 PM IST par update milega.\n\n"
            "🔎 Source: DC"
        )
    else:
        text = (
            "✅ Ye group daily anime update list me add ho gaya hai.\n"
            "Har din 5:00 PM IST par update milega.\n\n"
            "🔎 Source: DC"
        )

    await update.effective_message.reply_text(text)


async def activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat:
        return
    subscribers.add(chat.id)
    await update.effective_message.reply_text(
        "✅ Daily anime updates ON.\n"
        "⏰ Daily time: 5:00 PM IST\n"
        "🔎 Source: DC"
    )


async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat:
        return
    subscribers.discard(chat.id)
    await update.effective_message.reply_text(
        "🔕 Daily anime updates OFF for this chat."
    )


async def chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"Chat ID: {update.effective_chat.id}"
    )


async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat:
        return
    subscribers.add(chat.id)
    try:
        await context.bot.send_message(
            chat_id=chat.id,
            text=build_message(datetime.now(TZ).date()),
        )
    except Exception:
        log.exception("Test send failed")


async def send_daily(bot):
    if not subscribers:
        log.info("No subscribed chats; daily post skipped.")
        return

    msg = build_message(datetime.now(TZ).date())
 
    dead = []
    for chat_id in list(subscribers):
        try:
            await bot.send_message(chat_id=chat_id, text=msg)
        except Exception as exc:
            log.warning("Could not send to %s: %s", chat_id, exc)
            # Remove chats that can no longer receive messages.
            if "chat not found" in str(exc).lower() or "forbidden" in str(exc).lower():
                dead.append(chat_id)

    for chat_id in dead:
        subscribers.discard(chat_id)

    log.info("Daily anime update sent to %d chat(s).", len(subscribers))


async def health(request):
    return "OK"


async def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("activate", activate))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
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
        args=[app.bot],
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
