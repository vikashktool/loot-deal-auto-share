import os
import re
import hashlib
import logging
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

import requests
import feedparser
import psycopg

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("loot-deal-bot")

BOT_TOKEN = os.environ["BOT_TOKEN"]
DESTINATION_CHANNEL_ID = os.environ["DESTINATION_CHANNEL_ID"]
DATABASE_URL = os.environ["DATABASE_URL"]
AMAZON_ASSOCIATES_TAG = os.getenv("AMAZON_ASSOCIATES_TAG", "").strip()
ADMIN_USER_IDS = {x.strip() for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip()}
ALLOWED_SOURCE_CHAT_IDS = {x.strip() for x in os.getenv("ALLOWED_SOURCE_CHAT_IDS", "").split(",") if x.strip()}
RSS_FEEDS = [x.strip() for x in os.getenv("RSS_FEEDS", "").split(",") if x.strip()]

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
URL_RE = re.compile(r"https?://[^\s<>()\]]+", re.I)
AMAZON_HOSTS = {"amazon.in", "www.amazon.in"}


def db():
    return psycopg.connect(DATABASE_URL, sslmode="require")


def init_db():
    with db() as conn, conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS bot_state (
          key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS seen_deals (
          fingerprint TEXT PRIMARY KEY,
          source TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'posted',
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """)
        cur.execute("""
        INSERT INTO bot_state(key, value) VALUES ('last_update_id', '0')
        ON CONFLICT (key) DO NOTHING
        """)


def api_call(method, payload=None, timeout=25):
    response = requests.post(f"{API}/{method}", json=payload or {}, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API {method} failed: {data}")
    return data["result"]


def clean_url(raw):
    raw = raw.rstrip(".,!?:;)")
    try:
        p = urlparse(raw)
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
            return None
        host = p.hostname.lower()
        if host in {"localhost", "127.0.0.1"} or host.endswith(".local"):
            return None
        return raw
    except Exception:
        return None


def affiliate_url(raw):
    url = clean_url(raw)
    if not url:
        return None
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if host not in AMAZON_HOSTS:
        return url
    if not AMAZON_ASSOCIATES_TAG:
        return url
    # Only add a tag to recognizable direct product pages, never short links/search pages.
    if not re.search(r"/(?:dp|gp/product)/[A-Z0-9]{8,14}", p.path, re.I):
        return url
    pairs = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() != "tag"]
    pairs.append(("tag", AMAZON_ASSOCIATES_TAG))
    return urlunparse(p._replace(query=urlencode(pairs)))


def extract_urls(text):
    out = []
    for candidate in URL_RE.findall(text or ""):
        value = clean_url(candidate)
        if value and value not in out:
            out.append(value)
    return out


def fingerprint(source, url, title=""):
    # Deduplicate a deal across different source messages when a product URL exists.
    normalized = (url or "").split("#", 1)[0].rstrip("/")
    raw = f"url:{normalized}" if normalized else f"{source}|title:{title.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def already_seen(fp):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM seen_deals WHERE fingerprint=%s", (fp,))
        return cur.fetchone() is not None


def mark_seen(fp, source, status="posted"):
    with db() as conn, conn.cursor() as cur:
        cur.execute("""
        INSERT INTO seen_deals(fingerprint, source, status)
        VALUES (%s, %s, %s) ON CONFLICT (fingerprint) DO NOTHING
        """, (fp, source, status))


def send_deal(title, body, source, source_url=None, photo_file_id=None):
    text = (body or title or "New deal").strip()
    urls = extract_urls(text)
    chosen = affiliate_url(urls[0]) if urls else affiliate_url(source_url) if source_url else None

    for old in urls:
        text = text.replace(old, "").strip()
    if title and title.lower() not in text.lower():
        text = f"{title}\n\n{text}".strip()
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if chosen:
        text = f"{text}\n\n🛒 Buy here: {chosen}".strip()
    text = f"{text}\n\n#ad | Affiliate link: I may earn a commission from qualifying purchases."
    if len(text) > 1000:
        text = text[:990].rsplit(" ", 1)[0] + "…"

    fp = fingerprint(source, chosen or source_url or "", title)
    if already_seen(fp):
        log.info("Duplicate skipped: %s", fp[:10])
        return False

    reply_markup = None
    if chosen:
        reply_markup = {"inline_keyboard": [[{"text": "🛒 BUY NOW", "url": chosen}]]}
    if photo_file_id:
        payload = {"chat_id": DESTINATION_CHANNEL_ID, "photo": photo_file_id,
                   "caption": text[:1024]}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        api_call("sendPhoto", payload)
    else:
        payload = {"chat_id": DESTINATION_CHANNEL_ID, "text": text[:4000],
                   "disable_web_page_preview": False}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        api_call("sendMessage", payload)
    mark_seen(fp, source)
    log.info("Posted deal: %s", fp[:10])
    return True


def process_telegram_update(update):
    msg = update.get("channel_post") or update.get("message")
    if not msg:
        return
    chat = msg.get("chat", {})
    chat_id = str(chat.get("id", ""))
    user_id = str((msg.get("from") or {}).get("id", ""))
    is_channel_post = "channel_post" in update

    if is_channel_post:
        if chat_id not in ALLOWED_SOURCE_CHAT_IDS:
            log.warning("Ignoring unapproved source chat %s", chat_id)
            return
        source = f"telegram:{chat_id}:{msg.get('message_id')}"
    else:
        if chat.get("type") != "private" or user_id not in ADMIN_USER_IDS:
            return
        source = f"admin-forward:{user_id}:{msg.get('message_id')}"

    text = msg.get("text") or msg.get("caption") or ""
    urls = extract_urls(text)
    photos = msg.get("photo") or []
    photo_id = photos[-1].get("file_id") if photos else None

    if not urls:
        log.info("Skipped message without valid URL")
        return

    # Reposting third-party media/text requires permission; configure only authorized sources.
    title = text.splitlines()[0][:180] if text else "Loot Deal"
    send_deal(title, text, source, photo_file_id=photo_id)


def poll_telegram():
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT value FROM bot_state WHERE key='last_update_id'")
        offset = int(cur.fetchone()[0]) + 1
    updates = api_call("getUpdates", {
        "offset": offset, "timeout": 0,
        "allowed_updates": ["message", "channel_post"]
    })
    max_id = offset - 1
    for update in updates:
        max_id = max(max_id, int(update["update_id"]))
        try:
            process_telegram_update(update)
        except Exception:
            log.exception("Could not process Telegram update %s", update.get("update_id"))
    if max_id >= offset:
        with db() as conn, conn.cursor() as cur:
            cur.execute("UPDATE bot_state SET value=%s WHERE key='last_update_id'", (str(max_id),))


def poll_rss():
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            if getattr(feed, "bozo", False):
                log.warning("RSS feed parse warning: %s", feed_url)
            for entry in feed.entries[:10]:
                title = str(entry.get("title", "Shopping deal")).strip()
                link = clean_url(str(entry.get("link", "")))
                if not link:
                    continue
                # Use only feed title/link; feed sources must be authorized for this use.
                send_deal(title, title, f"rss:{feed_url}", source_url=link)
        except Exception:
            log.exception("RSS feed failed: %s", feed_url)


def main():
    required = {
        "BOT_TOKEN": BOT_TOKEN,
        "DESTINATION_CHANNEL_ID": DESTINATION_CHANNEL_ID,
        "DATABASE_URL": DATABASE_URL,
    }
    missing = [name for name, value in required.items() if not value.strip()]
    if missing:
        raise RuntimeError("Missing required GitHub Actions secrets: " + ", ".join(missing))

    init_db()

    # Telegram getUpdates polling cannot be used while a webhook is configured.
    # Stop clearly rather than silently failing or changing the bot's webhook.
    webhook = api_call("getWebhookInfo")
    if webhook.get("url"):
        raise RuntimeError(
            "A Telegram webhook is configured for this bot. getUpdates polling cannot run "
            "at the same time. Review the existing deployment before changing webhook settings."
        )

    poll_telegram()
    poll_rss()


if __name__ == "__main__":
    main()
