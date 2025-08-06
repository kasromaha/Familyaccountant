import os
import re
from collections import defaultdict
from datetime import datetime, date
from zoneinfo import ZoneInfo


from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

# Totals keyed by message date
LOCAL_TZ = ZoneInfo("Europe/Moscow")
_daily_totals: dict[date, int] = defaultdict(int)
# Channel ID where summary will be posted
CHANNEL_ID = int(os.getenv("TARGET_CHAT_ID", "0"))


async def handle_message(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.channel_post or not update.channel_post.text:
        await context.bot.send_message(CHANNEL_ID, f"Сообщение не содержит текста или не из channel_post")
        return

    text = update.channel_post.text.strip()
    print(f"Получено сообщение: {text}")

    match = re.match(r"([+-])(\d+)", text)
    if match:
        sign, amount_str = match.groups()
        amount = int(amount_str)
        if sign == '-':
            amount = -amount
        msg_date = update.channel_post.date.astimezone(LOCAL_TZ).date()
        await context.bot.send_message(CHANNEL_ID, f"Дата сообщения: {msg_date}, Сумма: {amount}")
        _daily_totals[msg_date] += amount
        await context.bot.send_message(CHANNEL_ID, f"Текущий итог на {msg_date}: {_daily_totals[msg_date]}")
    else:
        await context.bot.send_message(CHANNEL_ID, f"Сообщение не попало под шаблон: ([+-])(\\d+)")


async def send_summary() -> None:
    today = datetime.now(LOCAL_TZ).date()
    total = _daily_totals.pop(today, 0)
    if CHANNEL_ID != 0:
        await application.bot.send_message(CHANNEL_ID, f"Итог за день: {total}")


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    global application
    application = ApplicationBuilder().token(token).build()

    application.add_handler(
        MessageHandler(filters.ChatType.CHANNEL & filters.TEXT, handle_message)
    )

    scheduler = AsyncIOScheduler(timezone=LOCAL_TZ)
    scheduler.add_job(send_summary, "cron", hour=19, minute=21)
    scheduler.start()

    application.run_polling()


if __name__ == "__main__":
    main()
