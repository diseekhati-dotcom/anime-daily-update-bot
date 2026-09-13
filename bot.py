import asyncio
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ChatMemberHandler,
)
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
PORT = int(os.getenv("PORT", "10000"))

# Optional GitHub persistence. Recommended on Render so chat IDs survive restarts.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.getenv("GITHUB_REPO", "").strip()       # example: username/anime-daily-update-bot
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main").strip()
STORE_FILE = os.getenv("STORE_FILE", "data/chats.json").strip()

if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing.")

TZ = ZoneInfo(TZ_NAME)
chat_ids = set()
store_lock = threading.Lock()


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"OK"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


def start_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    log.info("Health server listening on port %s", PORT)
    server.serve_forever()


def github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def load_chats():
    global chat_ids

    # GitHub is persistent across Render restarts.
    if GITHUB_TOKEN and GITHUB_REPO:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{STORE_FILE}"
        try:
            r = requests.get(
                url,
                headers=github_headers(),
                params={"ref": GITHUB_BRANCH},
                timeout=15,
            )
            if r.status_code == 200:
                import base64
                data = r.json()
                raw = base64.b64decode(data["content"]).decode("utf-8")
                obj = json.loads(raw)
                chat_ids = {int(x) for x in obj.get("chat_ids", [])}
                log.info("Loaded %d saved chats from GitHub.", len(chat_ids))
                return
            if r.status_code != 404:
                log.warning("GitHub load failed: %s %s", r.status_code, r.text[:200])
        except Exception:
            log.exception("GitHub chat-store load failed.")

    # Local fallback. This works, but Render may clear it after a restart.
    try:
        p = os.path.abspath(STORE_FILE)
        if os.path.exists(p):
            obj = json.loads(open(p, "r", encoding="utf-8").read())
            chat_ids = {int(x) for x in obj.get("chat_ids", [])}
            log.info("Loaded %d chats from local storage.", len(chat_ids))
    except Exception:
        log.exception("Local chat-store load failed.")


def save_chats():
    payload = {"chat_ids": sorted(chat_ids)}
    text = json.dumps(payload, indent=2)

    if GITHUB_TOKEN and GITHUB_REPO:
        import base64
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{STORE_FILE}"
        try:
            r = requests.get(
                url,
                headers=github_headers(),
                params={"ref": GITHUB_BRANCH},
                timeout=15,
            )
            sha = r.json().get("sha") if r.status_code == 200 else None

            data = {
                "message": "Update registered Telegram chats",
                "content": base64.b64encode(text.encode()).decode(),
                "branch": GITHUB_BRANCH,
            }
            if sha:
                data["sha"] = sha

            put = requests.put(url, headers=github_headers(), json=data, timeout=15)
            if put.status_code in (200, 201):
                return
            log.warning("GitHub chat-store save failed: %s %s", put.status_code, put.text[:300])
        except Exception:
            log.exception("GitHub chat-store save failed.")

    try:
        p = os.path.abspath(STORE_FILE)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        log.exception("Local chat-store save failed.")


def add_chat(chat_id: int):
    with store_lock:
        if chat_id not in chat_ids:
            chat_ids.add(chat_id)
            save_chats()
            log.info("Registered chat %s. Total=%d", chat_id, len(chat_ids))


def remove_chat(chat_id: int):
    with store_lock:
        if chat_id in chat_ids:
            chat_ids.remove(chat_id)
            save_chats()
            log.info("Removed chat %s. Total=%d", chat_id, len(chat_ids))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat:
        return
    add_chat(chat.id)

    if chat.type == ChatType.PRIVATE:
        text = (
            "✅ Anime Daily Update Bot active.\n\n"
            "🕔 Daily update: 5:00 PM IST\n"
            "📌 DM: /start ke baad daily update isi chat me aayega.\n"
            "📌 GC: Bot ko group me add karte hi group register ho jayega.\n"
            "🧪 Test: /test\n"
            "🛑 Stop: /stop\n"
            "🔎 Source: DC"
        )
    else:
        text = (
            "✅ This group is registered for daily anime updates.\n"
            "🕔 Time: 5:00 PM IST\n"
            "🧪 Test: /test\n"
            "🛑 Stop: /stop\n"
            "🔎 Source: DC"
        )
    await update.effective_message.reply_text(text)


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat:
        return
    remove_chat(chat.id)
    await update.effective_message.reply_text("🛑 Daily anime updates stopped for this chat.")


async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat:
        return
    add_chat(chat.id)
    try:
        await update.effective_message.reply_text("⏳ Today's anime update is being prepared...")
        msg = build_message()
        await context.bot.send_message(chat_id=chat.id, text=msg)
    except Exception:
        log.exception("Test send failed for %s", chat.id)
        await update.effective_message.reply_text("❌ Update fetch/send failed. Check Render logs.")


async def chat_member_changed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    change = update.my_chat_member
    if not change or not change.chat:
        return

    chat = change.chat
    old = change.old_chat_member.status
    new = change.new_chat_member.status

    active = {"member", "administrator"}
    if new in active and old not in active:
        add_chat(chat.id)
        log.info("Bot added to chat %s (%s). Registered automatically.", chat.id, chat.type)
    elif new in {"left", "kicked"}:
        remove_chat(chat.id)
        log.info("Bot removed from chat %s. Unregistered.", chat.id)


async def send_daily(bot):
    if not chat_ids:
        log.info("No registered chats. Nothing to send.")
        return

    try:
        msg = build_message()
    except Exception:
        log.exception("Could not build today's anime message.")
        return

    targets = list(chat_ids)
    sent = 0
    failed = 0

    for chat_id in targets:
        try:
            await bot.send_message(chat_id=chat_id, text=msg)
            sent += 1
            await asyncio.sleep(0.15)
        except Exception as e:
            failed += 1
            log.warning("Send failed for %s: %s", chat_id, e)
            # If the bot was removed or chat is invalid, stop trying every day.
            if "chat not found" in str(e).lower() or "kicked" in str(e).lower():
                remove_chat(chat_id)

    log.info("Daily update finished: sent=%d failed=%d total=%d", sent, failed, len(targets))


async def main():
    load_chats()

    # Render Web Service health endpoint.
    threading.Thread(target=start_health_server, daemon=True).start()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(ChatMemberHandler(chat_member_changed, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("test", test))

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(
        lambda: asyncio.create_task(send_daily(app.bot)),
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
    await app.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )

    log.info(
        "Bot running. DM + GC enabled. Daily post scheduled for 17:00 %s.",
        TZ_NAME,
    )

    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
