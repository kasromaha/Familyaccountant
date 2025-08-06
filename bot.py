import os
import re

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

# Accumulator for today's records
_daily_total = 0
# Channel ID where summary will be posted
CHANNEL_ID = int(os.getenv("TARGET_CHAT_ID", "0"))


async def handle_message(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _daily_total
    if not update.channel_post or not update.channel_post.text:
        return

    text = update.channel_post.text.strip()
    match = re.match(r"([+-])(\d+)", text)
    if match:
        sign, amount_str = match.groups()
        amount = int(amount_str)
        if sign == '-':
            amount = -amount
        _daily_total += amount


async def send_summary() -> None:
    global _daily_total
    if CHANNEL_ID != 0:
        await application.bot.send_message(CHANNEL_ID, f"Итог за день: {_daily_total}")
    _daily_total = 0


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    global application
    application = ApplicationBuilder().token(token).build()

    application.add_handler(
        MessageHandler(filters.ChatType.CHANNEL & filters.TEXT, handle_message)
    )

    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_summary, "cron", hour=14, minute=5)
    scheduler.start()

    application.run_polling()


if __name__ == "__main__":
    main()
