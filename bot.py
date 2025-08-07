import os
import re
from collections import defaultdict
from datetime import datetime, date
from zoneinfo import ZoneInfo


import asyncpg
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

# Totals keyed by message date
LOCAL_TZ = ZoneInfo("Europe/Moscow")
_daily_totals: dict[date, int] = defaultdict(int)
# Channel ID where summary will be posted
CHANNEL_ID = int(os.getenv("TARGET_CHAT_ID", "0"))
ADMIN_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

async def handle_message(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.channel_post or not update.channel_post.text:
        await context.bot.send_message(ADMIN_ID, f"Сообщение не содержит текста или не из channel_post")
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
        _daily_totals[msg_date] += amount


        # Запись в PostgreSQL с повторными попытками
        for attempt in range(3):
            try:
                pool = context.application.bot_data.get('pg_pool')
                if pool is None:
                    await context.bot.send_message(ADMIN_ID, "Нет соединения с БД!")
                    break
                async with pool.acquire() as conn:
                    await conn.execute("SELECT 1")  # wake-up
                    await conn.execute(
                        "INSERT INTO expenses (amount, msg_date) VALUES ($1, $2)",
                        amount, msg_date
                    )
                break  # успех, выходим из цикла
            except Exception as e:
                if attempt < 2:
                    await context.bot.send_message(ADMIN_ID, f"Ошибка записи в БД (попытка {attempt+1}): {e}. Пробую ещё раз...")
                    import asyncio
                    await asyncio.sleep(5)
                else:
                    await context.bot.send_message(ADMIN_ID, f"Ошибка записи в БД: {e}")
    else:
        await context.bot.send_message(ADMIN_ID, f"Сообщение не попало под шаблон: ([+-])(\\d+)")


async def send_summary() -> None:
    today = datetime.now(LOCAL_TZ).date()
    pool = getattr(application, 'bot_data', {}).get('pg_pool')
    if pool is None:
        if ADMIN_ID != 0:
            await application.bot.send_message(ADMIN_ID, "Нет соединения с БД!")
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")  # wake-up
            total = await conn.fetchval(
                "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE msg_date = $1",
                today
            )
            # Запись итога дня в таблицу daily_summary
            await conn.execute(
                "INSERT INTO daily_summary (summary_date, total) VALUES ($1, $2) ON CONFLICT (summary_date) DO UPDATE SET total = $2",
                today, total
            )
        if ADMIN_ID != 0:
            await application.bot.send_message(CHANNEL_ID, f"Итог за день: {total}")
    except Exception as e:
        if ADMIN_ID != 0:
            await application.bot.send_message(ADMIN_ID, f"Ошибка получения итога из БД: {e}")


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    global application
    application = ApplicationBuilder().token(token).build()

    async def setup_pg_pool(app):
        pg_dsn = os.getenv("POSTGRES_DSN")
        if not pg_dsn:
            raise RuntimeError("POSTGRES_DSN is not set")
        pool = await asyncpg.create_pool(dsn=pg_dsn)
        # Инициализация таблиц
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS expenses (
                    id SERIAL PRIMARY KEY,
                    amount INTEGER NOT NULL,
                    msg_date DATE NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_summary (
                    id SERIAL PRIMARY KEY,
                    summary_date DATE NOT NULL UNIQUE,
                    total INTEGER NOT NULL
                );
            """)
        app.bot_data['pg_pool'] = pool

    application.add_handler(
        MessageHandler(filters.ChatType.CHANNEL & filters.TEXT, handle_message)
    )

    application.post_init = setup_pg_pool


    scheduler = AsyncIOScheduler(timezone=LOCAL_TZ)
    scheduler.add_job(send_summary, "cron", hour=23, minute=50)
    scheduler.start()

    application.run_polling()


if __name__ == "__main__":
    main()
