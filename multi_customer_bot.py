import os, re, time, logging
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
import requests
import psycopg

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("loot-deal")
TOKEN = os.getenv("BOT_TOKEN", "").strip()
DBURL = os.getenv("DATABASE_URL", "").strip()
API = "https://api.telegram.org/bot" + TOKEN
URL_RE = re.compile(r"https?://[^\s<>()]+", re.I)
PLATFORM_HOSTS = {
 "amazon": ("amazon.in", "www.amazon.in", "amzn.in"),
 "flipkart": ("flipkart.com", "www.flipkart.com", "fkrt.it"),
 "meesho": ("meesho.com", "www.meesho.com"),
 "earnkaro": ("earnkaro.com", "www.earnkaro.com")
}

def db():
    if not DBURL: raise RuntimeError("Set DATABASE_URL in Railway Variables")
    return psycopg.connect(DBURL, sslmode="require", connect_timeout=10)

def api(method, data=None):
    r = requests.post(API + "/" + method, json=data or {}, timeout=40)
    r.raise_for_status()
    j = r.json()
    if not j.get("ok"): raise RuntimeError(j.get("description", method + " failed"))
    return j["result"]

def send(chat, text, keyboard=None):
    p = {"chat_id": chat, "text": text}
    if keyboard: p["reply_markup"] = keyboard
    api("sendMessage", p)

def menu():
    return {"inline_keyboard":[
      [{"text":"🔗 Connect destination","callback_data":"dest"}],
      [{"text":"➕ Create forwarding task","callback_data":"task"}],
      [{"text":"📋 My tasks","callback_data":"tasks"}],
      [{"text":"🏷 Affiliate / watermark settings","callback_data":"settings"}]
    ]}

def init_db():
    with db() as c, c.cursor() as q:
        q.execute("""CREATE TABLE IF NOT EXISTS customers(
          user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT,
          destination TEXT, amazon_tag TEXT, watermark TEXT DEFAULT '')""")
        q.execute("""CREATE TABLE IF NOT EXISTS tasks(
          id BIGSERIAL PRIMARY KEY, user_id BIGINT REFERENCES customers(user_id) ON DELETE CASCADE,
          source TEXT NOT NULL, destination TEXT NOT NULL, platform TEXT NOT NULL DEFAULT 'all',
          active BOOLEAN NOT NULL DEFAULT TRUE)""")
        q.execute("""CREATE TABLE IF NOT EXISTS seen(
          task_id BIGINT REFERENCES tasks(id) ON DELETE CASCADE, fingerprint TEXT,
          PRIMARY KEY(task_id,fingerprint))""")
        q.execute("CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        q.execute("INSERT INTO state(key,value) VALUES('offset','0') ON CONFLICT DO NOTHING")

def save_user(u):
    with db() as c, c.cursor() as q:
        q.execute("""INSERT INTO customers(user_id,username,first_name)
        VALUES(%s,%s,%s) ON CONFLICT(user_id) DO UPDATE SET
        username=EXCLUDED.username,first_name=EXCLUDED.first_name""",
        (u["id"],u.get("username"),u.get("first_name")))

def get_customer(uid):
    with db() as c, c.cursor() as q:
        q.execute("SELECT destination,amazon_tag,watermark FROM customers WHERE user_id=%s",(uid,))
        r=q.fetchone()
    return r if r else (None,None,"")

def set_state(uid, value):
    with db() as c, c.cursor() as q:
        q.execute("INSERT INTO state(key,value) VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",(f"u:{uid}",value))

def get_state(uid):
    with db() as c, c.cursor() as q:
        q.execute("SELECT value FROM state WHERE key=%s",(f"u:{uid}",))
        r=q.fetchone()
    return r[0] if r else ""

def clear_state(uid):
    with db() as c, c.cursor() as q: q.execute("DELETE FROM state WHERE key=%s",(f"u:{uid}",))

def detect(url):
    host=(urlparse(url).hostname or "").lower()
    for k, hosts in PLATFORM_HOSTS.items():
        if any(host==h or host.endswith("."+h) for h in hosts): return k
    return "other"

def urls(text):
    result=[]
    for x in URL_RE.findall(text or ""):
        x=x.rstrip(".,!?:;)")
        p=urlparse(x)
        if p.scheme in ("http","https") and p.hostname and x not in result: result.append(x)
    return result

def apply_affiliate(url, tag):
    p=urlparse(url)
    if tag and (p.hostname or "").lower() in ("amazon.in","www.amazon.in") and re.search(r"/(?:dp|gp/product)/[A-Z0-9]{8,14}",p.path,re.I):
        pairs=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower()!="tag"]
        pairs.append(("tag",tag))
        return urlunparse(p._replace(query=urlencode(pairs)))
    return url

def verify_channel(name, require_admin=False):
    chat=api("getChat",{"chat_id":name})
    bot=api("getMe")
    m=api("getChatMember",{"chat_id":chat["id"],"user_id":bot["id"]})
    if require_admin and (m.get("status") not in ("administrator","creator") or m.get("can_post_messages") is False):
        raise ValueError("Bot ko destination channel ka admin aur Post Messages permission dein.")
    if not require_admin and m.get("status") not in ("administrator","creator","member"):
        raise ValueError("Bot ko source channel mein add karein; sirf username se posts read nahi ho sakte.")
    return str(chat["id"]),chat.get("title",name)

def handle_message(m):
    if m.get("chat",{}).get("type")!="private" or not m.get("from"): return
    u=m["from"]; uid=u["id"]; text=(m.get("text") or "").strip()
    save_user(u); state=get_state(uid)
    if text.startswith("/start") or text.startswith("/menu"):
        clear_state(uid); send(uid,"Welcome to Loot Deal Auto Share! Har customer ki settings aur tasks alag save hoti hain.",menu()); return
    if state=="destination":
        try:
            cid,title=verify_channel(text,True)
            with db() as c, c.cursor() as q: q.execute("UPDATE customers SET destination=%s WHERE user_id=%s",(cid,uid))
            clear_state(uid); send(uid,"Destination connected: "+title,menu())
        except Exception as e: send(uid,"Channel verify nahi hua: "+str(e)+"\nBot ko admin banakar @username dobara bhejein.")
        return
    if state=="source":
        try:
            sid,title=verify_channel(text,False); set_state(uid,"source:"+sid)
            send(uid,"Source: "+title+"\nPlatform filter choose karein:",{"inline_keyboard":[[{"text":"All","callback_data":"p:all"}],[{"text":"Amazon","callback_data":"p:amazon"},{"text":"Flipkart","callback_data":"p:flipkart"}],[{"text":"Meesho","callback_data":"p:meesho"},{"text":"EarnKaro","callback_data":"p:earnkaro"}]]})
        except Exception as e: send(uid,"Source verify nahi hua: "+str(e))
        return
    if state=="amazon_tag":
        if not re.fullmatch(r"[A-Za-z0-9_-]{2,80}",text): send(uid,"Approved Amazon tracking ID bhejein. Password/API secret nahi.")
        else:
            with db() as c, c.cursor() as q: q.execute("UPDATE customers SET amazon_tag=%s WHERE user_id=%s",(text,uid))
            clear_state(uid); send(uid,"Amazon tracking ID saved.",menu())
        return
    if state=="watermark":
        with db() as c, c.cursor() as q: q.execute("UPDATE customers SET watermark=%s WHERE user_id=%s",(text[:60],uid))
        clear_state(uid); send(uid,"Watermark text saved. Image overlay is a follow-up implementation; current version stores the label.",menu()); return
    send(uid,"Menu se option choose karein ya /start bhejein.",menu())

def callback(q):
    u=q.get("from",{}); uid=u.get("id"); data=q.get("data",""); chat=(q.get("message") or {}).get("chat",{}).get("id",uid)
    if not uid:return
    save_user(u)
    try: api("answerCallbackQuery",{"callback_query_id":q["id"]})
    except Exception: pass
    if data=="dest": set_state(uid,"destination"); send(chat,"Destination channel ka @username bhejein. Bot ko admin + Post Messages permission dein.")
    elif data=="task":
        dest,_,_=get_customer(uid)
        if not dest: send(chat,"Pehle destination channel connect karein.",menu())
        else: set_state(uid,"source"); send(chat,"Source channel ka @username bhejein. Bot ko source channel mein add karein.")
    elif data=="tasks":
        with db() as c, c.cursor() as q2:
            q2.execute("SELECT id,source,platform,active FROM tasks WHERE user_id=%s ORDER BY id DESC LIMIT 20",(uid,)); rows=q2.fetchall()
        send(chat,"\n".join([f"#{r[0]} {r[1]} | {r[2]} | {'ON' if r[3] else 'OFF'}" for r in rows]) or "No tasks yet.",menu())
    elif data=="settings": send(chat,"Affiliate / watermark settings",{"inline_keyboard":[[{"text":"Set Amazon tracking ID","callback_data":"amazon_tag"}],[{"text":"Set watermark text","callback_data":"watermark"}]]})
    elif data in ("amazon_tag","watermark"): set_state(uid,data); send(chat,"Apna approved tracking ID bhejein." if data=="amazon_tag" else "Watermark text bhejein.")
    elif data.startswith("p:"):
        state=get_state(uid)
        if not state.startswith("source:"): send(chat,"Task state expired. Try again.",menu()); return
        source=state.split(":",1)[1]; dest,_,_=get_customer(uid); platform=data.split(":",1)[1]
        with db() as c, c.cursor() as q2: q2.execute("INSERT INTO tasks(user_id,source,destination,platform) VALUES(%s,%s,%s,%s) RETURNING id",(uid,source,dest,platform)); tid=q2.fetchone()[0]
        clear_state(uid); send(chat,f"Task #{tid} created. Bot ko source posts receive karne ke liye source channel mein add karein.",menu())

def forward_post(m):
    source=str(m.get("chat",{}).get("id","")); text=m.get("text") or m.get("caption") or ""; found=urls(text)
    if not found:return
    with db() as c, c.cursor() as q:
        q.execute("SELECT id,user_id,destination,platform FROM tasks WHERE active=TRUE AND source=%s",(source,)); tasks=q.fetchall()
    for tid,uid,dest,platform in tasks:
        matches=[x for x in found if platform=="all" or detect(x)==platform]
        if not matches: continue
        fp=__import__("hashlib").sha256("|".join(sorted(matches)).encode()).hexdigest()
        with db() as c, c.cursor() as q:
            q.execute("SELECT 1 FROM seen WHERE task_id=%s AND fingerprint=%s",(tid,fp))
            if q.fetchone(): continue
        _,tag,watermark=get_customer(uid); link=apply_affiliate(matches[0],tag)
        caption=text
        for x in found: caption=caption.replace(x,"")
        caption=(caption.strip()+"\n\n🛒 Buy Now\n"+DISCLOSURE)[:1000]
        try:
            api("sendMessage",{"chat_id":dest,"text":caption+"\n"+link,"reply_markup":{"inline_keyboard":[[{"text":"🛒 BUY NOW","url":link}]]}})
            with db() as c, c.cursor() as q: q.execute("INSERT INTO seen(task_id,fingerprint) VALUES(%s,%s) ON CONFLICT DO NOTHING",(tid,fp))
        except Exception: log.exception("Forward failed for task %s",tid)

DISCLOSURE="Affiliate disclosure: qualifying purchases may earn commission for the link owner."

def main():
    if not TOKEN: raise RuntimeError("Set BOT_TOKEN in Railway Variables.")
    init_db()
    hook=api("getWebhookInfo")
    if hook.get("url"): raise RuntimeError("Webhook is active. Move this bot to long polling only after reviewing its existing deployment; no webhook was deleted.")
    api("deleteWebhook",{"drop_pending_updates":False})
    log.info("Loot Deal bot running")
    while True:
        try:
            with db() as c, c.cursor() as q:
                q.execute("SELECT value FROM state WHERE key='offset'"); r=q.fetchone(); offset=int(r[0])+1 if r else 0
            updates=api("getUpdates",{"offset":offset,"timeout":30,"allowed_updates":["message","callback_query","channel_post"]})
            maxid=offset-1
            for item in updates:
                maxid=max(maxid,item["update_id"])
                try:
                    if "callback_query" in item: callback(item["callback_query"])
                    elif "message" in item: handle_message(item["message"])
                    elif "channel_post" in item: forward_post(item["channel_post"])
                except Exception: log.exception("Update failed: %s",item.get("update_id"))
            if maxid>=offset:
                with db() as c, c.cursor() as q: q.execute("UPDATE state SET value=%s WHERE key='offset'",(str(maxid),))
        except KeyboardInterrupt: break
        except Exception: log.exception("Polling failed; retrying"); time.sleep(5)

if __name__=="__main__": main()
