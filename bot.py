import os
import re
from collections import defaultdict
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo


import asyncpg
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes, CommandHandler

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


async def result_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Проверка, что запрос от ADMIN_ID
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("Используйте: /result <начало> <конец> (например, /result 05.08.2025 07.08.2025)")
        return
    try:
        start_date = datetime.strptime(args[0], "%d.%m.%Y").date()
        end_date = datetime.strptime(args[1], "%d.%m.%Y").date()
    except Exception:
        await update.message.reply_text("Неверный формат дат. Используйте: /result 05.08.2025 07.08.2025")
        return
    pool = getattr(context.application, 'bot_data', {}).get('pg_pool')
    if pool is None:
        await update.message.reply_text("Нет соединения с БД!")
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")  # wake-up
            rows = await conn.fetch(
                "SELECT summary_date, total FROM daily_summary WHERE summary_date >= $1 AND summary_date <= $2 ORDER BY summary_date",
                start_date, end_date
            )
        # Собрать результат с нулями для отсутствующих дат и итоговую сумму
        result = ""
        total_sum = 0
        current = start_date
        while current <= end_date:
            found = next((r for r in rows if r["summary_date"] == current), None)
            total = found["total"] if found else 0
            result += f"{current.strftime('%d.%m.%Y')}: {total}\n"
            total_sum += total
            current += timedelta(days=1)
        result += f"\nИтог за период: {total_sum}"
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"Ошибка получения результата: {e}")


async def sync_summary_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Только для ADMIN_ID
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return
    pool = getattr(context.application, 'bot_data', {}).get('pg_pool')
    if pool is None:
        await update.message.reply_text("Нет соединения с БД!")
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")  # wake-up
            # Получить все даты из expenses
            dates = await conn.fetch("SELECT DISTINCT msg_date FROM expenses")
            # Получить уже внесённые даты из daily_summary
            summary_dates = await conn.fetch("SELECT summary_date FROM daily_summary")
            summary_dates_set = set(r["summary_date"] for r in summary_dates)
            count = 0
            for d in dates:
                date_val = d["msg_date"]
                if date_val not in summary_dates_set:
                    total = await conn.fetchval(
                        "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE msg_date = $1",
                        date_val
                    )
                    await conn.execute(
                        "INSERT INTO daily_summary (summary_date, total) VALUES ($1, $2)",
                        date_val, total
                    )
                    count += 1
        await update.message.reply_text(f"Синхронизация завершена. Добавлено итогов: {count}")
    except Exception as e:
        await update.message.reply_text(f"Ошибка синхронизации: {e}")


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
    application.add_handler(CommandHandler("result", result_command))
    application.add_handler(CommandHandler("sync_summary", sync_summary_command))

    application.post_init = setup_pg_pool


    scheduler = AsyncIOScheduler(timezone=LOCAL_TZ)
    scheduler.add_job(send_summary, "cron", hour=23, minute=50)
    scheduler.start()

    application.run_polling()


if __name__ == "__main__":
    main()
