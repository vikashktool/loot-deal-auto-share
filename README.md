# Loot Deal Auto Share Bot

Multi-customer Telegram deal-forwarding bot. Each customer can connect a destination channel, create tasks from authorized source channels, select a platform filter, set a watermark, and manage tasks.

## Important status
This project requires Telegram bot credentials and a PostgreSQL database configured as Railway environment variables before it can run. The bot can only read source-channel posts it receives; add the bot to source channels and ensure you have permission to repost content. Amazon tag handling applies only to eligible direct Amazon.in product URLs. Flipkart, Meesho, and EarnKaro need their own approved affiliate links or integrations; ordinary URLs are not magically converted into affiliate links.

## Railway variables
- `BOT_TOKEN`: Telegram bot token (keep private)
- `DATABASE_URL`: PostgreSQL connection URL (keep private)

## Setup
1. Create a PostgreSQL database, e.g. Neon, and set `DATABASE_URL` in Railway Variables.
2. Set `BOT_TOKEN` in Railway Variables.
3. Set the service start command to `python bot.py` if auto-detection does not do so.
4. Ensure the Telegram bot has no competing webhook while using long polling.
5. Add the bot as an admin to destination channels with posting permission and to source channels where permitted.
6. Test using a private test channel first.

Do not put tokens, passwords, or affiliate API secrets in GitHub files or chat.