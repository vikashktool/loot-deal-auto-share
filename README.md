# Loot Deal Auto Share Bot

Telegram deal-sharing starter designed for scheduled runs with GitHub Actions.

## Planned features
- Scheduled processing every 10 minutes (GitHub Actions can delay scheduled runs).
- Optional RSS feed sources.
- Repost only Telegram content from source channels you own or have explicit permission to use.
- Deduplicate published deals with persistent PostgreSQL storage.
- Add an Amazon Associates tag only to eligible direct Amazon.in product links, after your Associates account is approved.
- Include affiliate disclosure on posts.

## Setup checklist
1. Add the bot as an administrator to the destination Telegram channel with permission to post.
2. Only configure source channels where reposting is permitted; the bot cannot read arbitrary channels from usernames alone.
3. Create a PostgreSQL database and keep its connection string private.
4. In GitHub, open Settings → Secrets and variables → Actions and add the secrets described below.
5. If this bot currently uses a Telegram webhook, inspect that configuration before using getUpdates polling; Telegram does not support both at once.
6. Test in a private channel before posting publicly.

## Required repository secrets
- `BOT_TOKEN`: Telegram bot token
- `DESTINATION_CHANNEL_ID`: destination channel ID
- `DATABASE_URL`: PostgreSQL connection string
- `ADMIN_USER_IDS`: comma-separated Telegram numeric user IDs allowed to submit deals
- `ALLOWED_SOURCE_CHAT_IDS`: comma-separated numeric IDs for authorized source channels

## Optional repository secrets
- `AMAZON_ASSOCIATES_TAG`: your approved Amazon.in tracking ID. Leave blank until your Associates account is approved.
- `RSS_FEEDS`: comma-separated RSS feed URLs you are authorized to use.

Amazon Associates India: https://affiliate-program.amazon.in/

## Important
- Never commit bot tokens, passwords, database credentials, or affiliate credentials to source code.
- Do not repost content without permission or bypass Telegram access restrictions.
- This project needs configuration and end-to-end testing before production use. GitHub Actions scheduling is not exact real-time hosting.
