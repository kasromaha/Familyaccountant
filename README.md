# Familyaccountant

Bot helper for your everyday money control.

## Usage
1. Create a Telegram bot with BotFather and add it to your private channel as an administrator.
2. Set environment variables:
   - `TELEGRAM_BOT_TOKEN` – token of your bot.
   - `TARGET_CHAT_ID` – identifier of the channel where the bot posts the summary.
3. Install dependencies: `pip install -r requirements.txt`.
4. Run the bot: `python bot.py`.

Send messages to the channel in the form `+100 salary` or `-50 groceries`. Every day at 23:59 the bot sums the numbers and posts the result to the channel.
