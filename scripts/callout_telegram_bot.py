#!/usr/bin/env python3
"""
Telegram alert for every new callout of the accounts a pump.fun user follows.

Message: who called, the callout's text, the market cap at the call (pump.fun's figure, and the live
one), the token's contract address (CA) and links.

Sources (pump.fun's public frontend API, no login):
  - GET /home-feed/new every FEED_EVERY_S: every new callout of every user, seen ~1 s after creation;
  - GET /callout/list/<address>?limit=3 for one followed account every LIST_EVERY_S, in turn, as a
    backstop for callouts the feed's floors leave out;
  - the followed accounts are re-read every FOLLOWING_EVERY_S, so follows/unfollows are picked up.
Already-sent callouts are kept in a state file, so a restart does not resend them; callouts older than
MAX_AGE_S (a new follow, a long downtime) are never sent.

Setup:
  1. On Telegram, talk to @BotFather -> /newbot -> copy the token.
  2. Send any message to your new bot, then run:  callout_telegram_bot.py --chat-id-help
  3. Run:  TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... callout_telegram_bot.py <pump.fun username>
Options: --dry-run (print instead of sending), --test (send one message with the latest callout of a
followed account, then exit), --all (alert on every pump.fun user's callouts, to check the setup).
"""
import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://frontend-api-v3.pump.fun"
FEED_EVERY_S, LIST_EVERY_S, FOLLOWING_EVERY_S, MAX_AGE_S = 1.5, 2.0, 600, 600
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "telegram_bot_state.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def http_json(url, data=None, timeout=15):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **({"Content-Type": "application/json"} if data else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


BACKOFF = {}  # endpoint family -> time until which it is not called (after a 429)


def api(path):
    """GET on pump.fun's API; None on failure. A 429 pauses only that endpoint family, never the loop."""
    fam = path.split("?")[0].rsplit("/", 1)[0]
    if time.time() < BACKOFF.get(fam, 0):
        return None
    try:
        return http_json(API + path)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            BACKOFF[fam] = time.time() + min(float(e.headers.get("Retry-After", 5)), 60)
        return None
    except Exception:
        return None


def following(username):
    users = api(f"/users/search-v2?searchTerm={urllib.parse.quote(username)}&limit=10") or []
    me = next((u for u in users if u["username"].lower() == username.lower()), None)
    if not me:
        raise SystemExit(f"utilisateur pump.fun introuvable : {username}")
    rows = api(f"/following/v3/following/{me['address']}?limit=500")
    if rows is None:
        return None
    return {u["address"]: u["username"] for u in rows}


def fmt_usd(v):
    if v is None:
        return "?"
    return f"${v / 1e6:.2f}M" if v >= 1e6 else f"${v / 1e3:.1f}k" if v >= 1e3 else f"${v:.0f}"


def message(c):
    mint = c["mint"]
    name = f"{c.get('name') or ''} (${c['symbol']})" if c.get("symbol") else mint[:6] + "…"
    lines = [f"📣 <b>{html.escape(c['author'])}</b> a callé <b>{html.escape(name)}</b>"]
    if c.get("thesis"):
        lines.append(f"💬 « {html.escape(c['thesis'])} »")
    mc = f"💰 MC au call : <b>{fmt_usd(c.get('mcap_call'))}</b>"
    if c.get("mcap_now"):
        mc += f" · maintenant {fmt_usd(c['mcap_now'])}"
    lines.append(mc)
    lines.append(f"📄 CA : <code>{html.escape(mint)}</code>")
    lines.append(f'<a href="https://pump.fun/coin/{mint}">pump.fun</a> · <a href="https://dexscreener.com/solana/{mint}">DexScreener</a>'
                 f" · {time.strftime('%H:%M:%S', time.gmtime(c['created']))} UTC")
    return "\n".join(lines)


def send(token, chat_id, text, dry):
    if dry:
        print("-" * 60 + "\n" + text, flush=True)
        return True
    body = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
    for _ in range(3):
        try:
            http_json(f"https://api.telegram.org/bot{token}/sendMessage", body)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(json.load(e).get("parameters", {}).get("retry_after", 3)))
                continue
            print(f"Telegram a refusé le message ({e.code}): {e.read()[:200]!r}", flush=True)
            return False
        except Exception as e:
            print(f"envoi Telegram impossible: {e}", flush=True)
            time.sleep(2)
    return False


def from_feed(row):
    p = row.get("position") or {}
    co = p.get("callout") or {}
    if not co.get("calloutId"):
        return None
    created = time.mktime(time.strptime(co["calloutTimestamp"][:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    return {"id": co["calloutId"], "author_address": p.get("walletAddress"), "author": p.get("userName"),
            "mint": row["coinMint"], "name": row.get("coinName"), "symbol": row.get("symbol"),
            "thesis": co.get("thesis"), "mcap_call": co.get("calledOutAtMcap"), "mcap_now": row.get("marketCap"),
            "created": created}


def from_list(c, address, author):
    return {"id": c["calloutId"], "author_address": address, "author": author, "mint": c["coinMint"],
            "thesis": c.get("thesis"), "mcap_call": c.get("marketCap"), "created": c["createdAt"] / 1000}


def enrich(c):
    if not c.get("symbol"):
        meta = api(f"/coins-v2/{c['mint']}") or {}
        c["name"], c["symbol"] = meta.get("name"), meta.get("symbol")
        if meta.get("usd_market_cap"):
            c["mcap_now"] = meta["usd_market_cap"]
    return c


def chat_id_help(token):
    d = http_json(f"https://api.telegram.org/bot{token}/getUpdates")
    chats = {(u.get("message") or {}).get("chat", {}).get("id"): (u.get("message") or {}).get("chat", {}).get("first_name")
             for u in d.get("result", []) if u.get("message")}
    if not chats:
        print("Aucun message reçu : envoie d'abord un message à ton bot sur Telegram, puis relance.")
    for cid, n in chats.items():
        print(f"TELEGRAM_CHAT_ID={cid}   ({n})")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry, test, everyone = "--dry-run" in sys.argv, "--test" in sys.argv, "--all" in sys.argv
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if "--chat-id-help" in sys.argv:
        return chat_id_help(token)
    if not args or (not dry and not (token and chat_id)):
        raise SystemExit(__doc__)
    username = args[0].lstrip("@")
    follow = following(username)
    if not follow:
        raise SystemExit("liste des abonnements indisponible")
    print(f"{username} suit {len(follow)} comptes", flush=True)

    if test:
        for addr, name in follow.items():
            d = api(f"/callout/list/{addr}?limit=1&sortBy=TIMESTAMP&sortOrder=DESC")
            if d and d.get("callouts"):
                c = enrich(from_list(d["callouts"][0], addr, name))
                print("envoyé" if send(token, chat_id, "🧪 Test\n" + message(c), dry) else "échec")
                return
        raise SystemExit("aucun callout trouvé pour tester")

    try:
        seen = set(json.load(open(STATE)))
    except Exception:
        seen = set()
    os.makedirs(os.path.dirname(STATE), exist_ok=True)

    def save():
        json.dump(sorted(seen)[-5000:], open(STATE, "w"))

    def emit(c):
        if c["id"] in seen or (c["author_address"] not in follow and not everyone):
            return
        seen.add(c["id"])
        if time.time() - c["created"] > MAX_AGE_S:  # an old callout (new follow, long downtime): skip
            return
        c["author"] = follow.get(c["author_address"], c["author"])
        delay = time.time() - c["created"]
        ok = send(token, chat_id, message(enrich(c)), dry)
        print(f"{time.strftime('%H:%M:%S')} {c['author']} -> {c['mint'][:8]}… "
              f"({'envoyé' if ok else 'ÉCHEC'}, {delay:.1f} s après le call)", flush=True)
        save()

    save()
    print("en écoute… (Ctrl+C pour arrêter)", flush=True)
    order = list(follow)
    k, t_feed, t_list, t_follow = 0, 0.0, 0.0, time.time()
    while True:
        now = time.time()
        if now - t_feed >= FEED_EVERY_S:
            t_feed = now
            d = api("/home-feed/new") or {}
            for row in d.get("coins", []):
                c = from_feed(row)
                if c:
                    emit(c)
        if now - t_list >= LIST_EVERY_S and order:
            t_list = now
            addr = order[k % len(order)]
            k += 1
            d = api(f"/callout/list/{addr}?limit=3&sortBy=TIMESTAMP&sortOrder=DESC") or {}
            for c in d.get("callouts") or []:
                if not c.get("perp"):
                    emit(from_list(c, addr, follow[addr]))
        if now - t_follow >= FOLLOWING_EVERY_S:
            t_follow = now
            f = following(username)
            if f:
                follow, order = f, list(f)
        time.sleep(0.2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("arrêté")
