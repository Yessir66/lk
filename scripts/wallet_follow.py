#!/usr/bin/env python3
"""
Follow the callers' wallets on the Solana blockchain instead of their pump.fun callouts (pump.fun's API now answers
scripts with a Cloudflare challenge, HTTP 403): each time one of them buys a coin it did not hold, send the coin's
address (CA) to Trojan from your Telegram account; its Auto Buy buys it.

  wallet_follow.py [--live] [--wallets <wallet>,...] [--min-sol 0.5] [--max-age 60] [--max-coin-age 2]
                   [--max-run X] [--send-to @bot] [--alerts] [--rpc-http URL] [--rpc-ws URL]

Wallets: the CALLERS below unless --wallets is given; a pump.fun profile's address is the wallet it trades with. On
the saved callouts, the callers bought the coin from that wallet 30 s to 6 min before posting the callout, for 0.8
to 5 SOL, so their buy is seen before their callout would have been; but every coin they buy is seen, called or not.
Detection: one WebSocket subscription per wallet to the transactions that mention it (logsSubscribe at the confirmed
commitment, `pip install websockets`), ~1 s after the buy; transactions whose logs hold no buy, swap or route
instruction are left out (most are pump.fun's fee distributions to holders, which credit the caller with coins it
never bought). As a backstop for the dropped connections (and the only source without websockets), each wallet's
latest signatures are read in turn over HTTP. Each candidate transaction is then read (getTransaction): a buy is a
transaction signed by the wallet that raises its balance of a coin from (almost) nothing.
Filters: buy seen within --max-age s of its block, at least --min-sol SOL spent (SOL, wrapped SOL and USDC / USDT
at SOL's price, fees included), coin created less than --max-coin-age days before (default 2, 0: off; Jupiter's
token data, a coin Jupiter does not list yet being brand new), each coin once. --max-run X: skipped when Jupiter's
last price is more than X times the caller's average price (SOL spent / tokens received); the caller's own buy
lifts the price above that average, so a low X skips the coins it bought big on a small market cap.
Without --live nothing is sent. The CA goes as a Telegram message from YOUR account to @solana_trojanbot (--send-to
another bot): amount, slippage and sells are Trojan's settings. The login is the one of callout_follow.py, kept in
data/telegram.session: close that script first, a Telegram session opens in one window at a time.
--alerts: a message through your alert bot (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) for each buy of at least
--min-sol: who bought, how much, the coin, its market cap, whether the CA went to Trojan.
While it runs, typing pause / reprise + Enter stops / resumes the sends to Trojan (detection and alerts go on);
etat shows the counts.
--until-pump-fun (set by callout_follow.py, which runs this script while pump.fun is closed to scripts): every
5 min, look whether pump.fun's API answers again, and if so exit with code 3 so that callout_follow.py goes back to
the callouts.
--rpc-http / --rpc-ws: your own Solana RPC (e.g. a free Helius key) if the public one throttles.
Output: data/wallet_follow_<timestamp>.jsonl, one line per buy seen.
"""
import asyncio
import datetime
import html
import json
import os
import queue
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
LIVE = "--live" in sys.argv
RPC = arg("--rpc-http", "https://api.mainnet-beta.solana.com")
WS = arg("--rpc-ws", RPC.replace("https://", "wss://").replace("http://", "ws://"))
MIN_SOL = float(arg("--min-sol", "0.5"))
MAX_AGE = float(arg("--max-age", "60"))
MAX_COIN_AGE = float(arg("--max-coin-age", "2"))   # days; 0: off
MAX_RUN = float(arg("--max-run", "0"))             # 0: off
SEND_TO = arg("--send-to", "@solana_trojanbot")
ALERTS = "--alerts" in sys.argv
UNTIL_PUMP = "--until-pump-fun" in sys.argv   # run by callout_follow.py: hand back once pump.fun answers again
TG_API_ID, TG_API_HASH = 30461091, "1ba7f769c94596baaf675447c02bb478"   # my.telegram.org app "CALLOUT"
CALLERS = {"6yVb4pxNwDfr6rovwNnBg3SyKSvDcHGD4WdFPN1JJBqm": "0xEthan",
           "Be24Gbf5KisDk1LcWWZsBn8dvB816By7YzYF5zWZnRR6": "chairman",
           "5hAgYC8TJCcEZV7LTXAzkTrm7YL29YXyQQJPCNrG84zM": "schoen",
           "87rRdssFiTJKY4MGARa4G5vQ31hmR7MxSmhzeaJ5AAxJ": "Dior",
           "ardinRsN1mNYVeoJWTBsWeYeXvuR9UUDGMsCDKpb6AT": "trunoest",
           "CE44oKS3wpUerx8afyeii56u5oQjBLZknzm4Q2CYHUz9": "ely",
           "DuARUpsEzdVbVpSnbGipLJvWxaXUCPLDJDjNov6Rc6s6": "HarryPaulDavies",
           "EqBQhoU88ty3a3aDphDRCBuiRirjUsFSFi4UHGY64qvv": "sadcrissy",
           "2M2vLX34LXMg24dMEnjWHvRXS1tshpEDRWzmXgV8ENNZ": "SossaDotKek",
           "AH39BneW9UeWxQysUcwogQrYFdPvJuRVZ5w4ccq9SKfL": "_logjam"}
SOL = "So11111111111111111111111111111111111111112"
STABLES = {"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}   # USDC, USDT
NOT_COINS = STABLES | {SOL}
BUY_LOG = re.compile(r"Instruction: (Buy|\w*Swap|\w*Route)|Log truncated")
LOG = os.path.normpath(os.path.join(DATA, f"wallet_follow_{int(time.time())}.jsonl"))
CA = "/root/.ccr/ca-bundle.crt"
SSL = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()

lock = threading.Lock()
seen = {}            # signature -> time first seen, by either source
tried = set()        # coins already handled
jobs = queue.Queue() # (signature, wallet, seen at, block time) of the transactions to read
tg = {}              # Telegram client and target bot
state = {"paused": False, "sent": 0, "buys": 0, "tx": 0, "ws": "pas encore connecté"}
sol_usd = {"t": 0, "v": None}


def http(url, body=None, timeout=10):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Accept": "application/json",
                                          "User-Agent": "Mozilla/5.0"})
    return json.load(urllib.request.urlopen(req, timeout=timeout, context=SSL))


def rpc(method, params):
    r = http(RPC, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if "error" in r:
        raise RuntimeError(f"{method}: {r['error'].get('message')}")
    return r["result"]


def log(rec):
    with lock, open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")


def now_str():
    return time.strftime("%H:%M:%S")


# ---------- detection: the wallets' transactions, from the WebSocket and from HTTP

def submit(sig, wallet, candidate, block_time=None):
    """Queue a transaction to read, once whichever source sees it first."""
    with lock:
        if sig in seen:
            return
        seen[sig] = time.time()
    state["tx"] += 1
    if candidate:
        jobs.put((sig, wallet, time.time(), block_time))


def ws_loop(wallets):
    try:
        import websockets
    except ImportError:
        state["ws"] = "absent (pip install websockets)"
        return print("pip install websockets pour voir les achats ~1 s plus tôt ; en attendant, lecture HTTP seule",
                     flush=True)

    async def run():
        fails, quiet = 0, False   # quiet: connected and already said so
        while True:
            try:
                async with websockets.connect(WS, ssl=SSL if WS.startswith("wss") else None, max_size=None,
                                              ping_interval=20, ping_timeout=20) as ws:
                    for i, w in enumerate(wallets):
                        await ws.send(json.dumps({"jsonrpc": "2.0", "id": i, "method": "logsSubscribe",
                                                  "params": [{"mentions": [w]}, {"commitment": "confirmed"}]}))
                    subs = {}
                    async for msg in ws:
                        m = json.loads(msg)
                        if "id" in m:   # a subscription's answer
                            if "result" in m:
                                subs[m["result"]] = wallets[m["id"]]
                                if len(subs) == len(wallets):
                                    if not quiet:
                                        print(f"{now_str()} WebSocket connecté : {len(subs)} wallets suivis en direct",
                                              flush=True)
                                    state["ws"], fails, quiet = "connecté", 0, True
                            else:
                                print(f"  abonnement refusé pour {wallets[m['id']][:8]} : {m.get('error')}", flush=True)
                            continue
                        p = m.get("params") or {}
                        v, w = p["result"]["value"], subs.get(p.get("subscription"))
                        if w and not v.get("err"):
                            submit(v["signature"], w, any(BUY_LOG.search(x) for x in v.get("logs") or []))
            except Exception as e:
                # the public endpoint drops connections every minute or so: reconnect silently at once, the HTTP
                # backstop covers the gap
                fails += 1
                state["ws"] = f"reconnexion ({type(e).__name__})"
                if fails == 5:
                    quiet = False
                    print(f"{now_str()} WebSocket injoignable ({type(e).__name__}) : détection par HTTP seule en "
                          f"attendant, plus lente de quelques secondes", flush=True)
                await asyncio.sleep(min(fails, 10))

    asyncio.run(run())


def poll_loop(wallets):
    """Backstop: each wallet's latest signatures in turn, one request every 2.5 s while the WebSocket is up (each
    wallet every ~25 s), else every 0.5 s."""
    k = 0
    while True:
        w = wallets[k % len(wallets)]
        k += 1
        try:
            for s in rpc("getSignaturesForAddress", [w, {"limit": 15, "commitment": "confirmed"}]):
                if not s.get("err") and s.get("blockTime") and time.time() - s["blockTime"] < MAX_AGE:
                    submit(s["signature"], w, True, s["blockTime"])
        except urllib.error.HTTPError as e:
            if e.code == 429:   # the public RPC throttles: back off
                time.sleep(10)
        except Exception:
            pass
        time.sleep(2.5 if state["ws"] == "connecté" else 0.5)


def get_tx(sig):
    for _ in range(12):   # a just-confirmed transaction can take a moment to be readable
        try:
            tx = rpc("getTransaction", [sig, {"encoding": "jsonParsed", "commitment": "confirmed",
                                              "maxSupportedTransactionVersion": 1}])
            if tx:
                return tx
        except Exception:
            time.sleep(0.5)
        time.sleep(0.5)
    return None


def buys_in(tx, wallet):
    """[(coin, raw tokens bought, decimals, SOL spent)]: the coins a transaction signed by wallet took from
    (almost) nothing to a position."""
    meta = tx["meta"]
    keys = tx["transaction"]["message"]["accountKeys"]
    i = next((j for j, k in enumerate(keys) if k["pubkey"] == wallet and k.get("signer")), None)
    if i is None or meta.get("err"):
        return []

    def balances(key):
        out = {}
        for b in meta.get(key) or []:
            if b.get("owner") == wallet:
                amt = out.setdefault(b["mint"], [0, b["uiTokenAmount"]["decimals"]])
                amt[0] += int(b["uiTokenAmount"]["amount"])
        return out

    pre, post = balances("preTokenBalances"), balances("postTokenBalances")
    wsol = pre.get(SOL, [0])[0] - post.get(SOL, [0])[0]
    usd = sum(pre.get(m, [0])[0] - post.get(m, [0])[0] for m in STABLES) / 1e6   # some pay in USDC
    sol = (meta["preBalances"][i] - meta["postBalances"][i] + wsol) / 1e9 + (usd / sol_price() if usd > 0 and
                                                                             sol_price() else 0)
    return [(m, amt - pre.get(m, [0])[0], dec, sol) for m, (amt, dec) in post.items()
            if m not in NOT_COINS and amt > pre.get(m, [0])[0] and pre.get(m, [0])[0] < amt * 0.1]


def worker():
    while True:
        sig, wallet, seen_at, block_time = jobs.get()
        try:
            tx = get_tx(sig)
            if tx:
                for mint, raw, dec, sol in buys_in(tx, wallet):
                    on_buy(wallet, sig, tx.get("blockTime") or block_time or seen_at, mint, raw, dec, sol, seen_at)
        except Exception as e:
            print(f"  transaction {sig[:10]} : {type(e).__name__} {e}", flush=True)


# ---------- decision: filters, then the CA to Trojan

def token_info(mint):
    """Jupiter's data on the coin (name, symbol, usdPrice, mcap, createdAt); {} if Jupiter does not list it yet,
    None if Jupiter does not answer."""
    try:
        return next((t for t in http(f"https://lite-api.jup.ag/tokens/v2/search?query={mint}") if t.get("id") == mint), {})
    except Exception:
        return None


def sol_price():
    if time.time() - sol_usd["t"] > 60:
        try:
            sol_usd.update(v=http(f"https://lite-api.jup.ag/price/v3?ids={SOL}")[SOL]["usdPrice"], t=time.time())
        except Exception:
            pass
    return sol_usd["v"]


def on_buy(wallet, sig, block_time, mint, raw, dec, sol, seen_at):
    name, delay = CALLERS.get(wallet, wallet[:8]), seen_at - block_time
    rec = {"type": "buy", "caller": name, "wallet": wallet, "mint": mint, "sig": sig, "sol": round(sol, 4),
           "block_time": block_time, "seen_at": seen_at, "delay_s": round(delay, 2)}
    print(f"\n{now_str()} ACHAT {name} -> {mint} | {sol:.2f} SOL | vu {delay:.1f} s après", flush=True)
    state["buys"] += 1
    with lock:
        skip = (f"seulement {sol:.2f} SOL (< {MIN_SOL:g})" if sol < MIN_SOL else
                f"vu trop tard ({delay:.0f} s > {MAX_AGE:.0f} s)" if delay > MAX_AGE else
                "déjà vu sur ce coin" if mint in tried else None)
        if not skip:
            tried.add(mint)
    if skip:
        print(f"  ignoré : {skip}", flush=True)
        return log({**rec, "skipped": skip})
    info = token_info(mint)
    created = (info or {}).get("createdAt") or ((info or {}).get("firstPool") or {}).get("createdAt")
    age = (time.time() - datetime.datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()) / 86400 \
        if created else 0 if info == {} else None
    run = (info["usdPrice"] / (sol / (raw / 10 ** dec) * sol_price())
           if info and info.get("usdPrice") and sol > 0 and sol_price() else None)
    rec.update({"coin_age_days": age, "mcap": (info or {}).get("mcap"), "run_since_buy": run})
    skip = ("âge du coin inconnu (Jupiter ne répond pas)" if MAX_COIN_AGE and age is None else
            f"coin créé il y a {age:.1f} jours (> {MAX_COIN_AGE:g})" if MAX_COIN_AGE and age > MAX_COIN_AGE else
            f"prix déjà x{run:.2f} depuis son achat (> x{MAX_RUN:g})" if MAX_RUN and run and run > MAX_RUN else
            "envois en pause" if state["paused"] else None)
    if skip:
        print(f"  pas d'envoi à Trojan : {skip}", flush=True)
        log({**rec, "skipped": skip})
    else:
        skip = forward(mint, rec, block_time, seen_at, age, run)
    if ALERTS:
        threading.Thread(target=send_alert, args=(name, mint, sol, info, age, sig, skip), daemon=True).start()


def forward(mint, rec, block_time, seen_at, age, run):
    """Send the CA to the bot; its Auto Buy does the rest. Returns why it was not sent, else None."""
    info = (f", coin créé il y a {age * 24:.1f} h" if age is not None else "") + (f", prix x{run:.2f} vs son achat"
                                                                                  if run else "")
    if not LIVE:
        print(f"  SIMULATION : enverrait {mint} à @{tg['bot'].username}{info}", flush=True)
        log({**rec, "type": "forward", "simulated": True})
        return "simulation"
    cl = tg["client"]
    fut = asyncio.run_coroutine_threadsafe(cl.send_message(tg["bot"], mint), cl.loop)
    try:
        fut.result(8)
    except Exception as e:
        fut.cancel()   # else the message would go out whenever Telegram is back, buying a stale call
        print(f"  CA NON ENVOYÉ à Trojan : Telegram injoignable ({type(e).__name__}). La reconnexion continue toute "
              f"seule ; vérifie ta connexion internet", flush=True)
        log({**rec, "type": "forward", "error": f"telegram {type(e).__name__}: {e}"})
        return "Telegram injoignable"
    sent_at = time.time()
    state["sent"] += 1
    print(f"  CA envoyé à @{tg['bot'].username} {sent_at - seen_at:.1f} s après détection "
          f"({sent_at - block_time:.1f} s après son achat){info}", flush=True)
    log({**rec, "type": "forward", "sent_at": sent_at, "buy_to_send_s": round(sent_at - block_time, 2)})


def telegram(target):
    """Log in as the user (first run: phone number and code), resolve the bot, keep the connection alive."""
    try:
        from telethon.sync import TelegramClient
    except ImportError:
        sys.exit("ce script demande telethon : pip install telethon")
    api_id, api_hash = os.environ.get("TG_API_ID") or TG_API_ID, os.environ.get("TG_API_HASH") or TG_API_HASH
    client = TelegramClient(os.path.join(DATA, "telegram"), int(api_id), api_hash, connection_retries=-1, retry_delay=3)
    try:
        client.start()
    except Exception as e:
        if "locked" in str(e):
            sys.exit("la session Telegram est déjà ouverte dans une autre fenêtre (callout_repost.py ?) : ferme-la "
                     "d'abord (Ctrl+C), puis relance")
        raise
    bot = client.get_entity(target)
    if not getattr(bot, "bot", False):
        sys.exit(f"{target} n'est pas un bot Telegram : rien ne sera envoyé")
    print(f"Telegram connecté : les CA iront à @{bot.username} ({bot.first_name}"
          f"{', compte vérifié' if bot.verified else ''}). Vérifie que c'est bien ton bot Trojan.")
    # run the client's loop in the background so pings keep the connection open between buys
    threading.Thread(target=client.loop.run_forever, daemon=True).start()
    tg.update(client=client, bot=bot)


def fmt_usd(v):
    return "?" if v is None else f"${v / 1e6:.2f}M" if v >= 1e6 else f"${v / 1e3:.1f}k" if v >= 1e3 else f"${v:.0f}"


def send_alert(name, mint, sol, info, age, sig, skip):
    coin = f"{info.get('name') or ''} (${info['symbol']})" if info and info.get("symbol") else mint[:6] + "…"
    age_s = (f" · créé il y a {age * 24:.1f} h" if age < 2 else f" · créé il y a {age:.0f} j") if age is not None else ""
    lines = [f"🟢 <b>{html.escape(name)}</b> a acheté <b>{html.escape(coin)}</b>",
             f"💸 {sol:.2f} SOL · MC <b>{fmt_usd((info or {}).get('mcap'))}</b>{age_s}",
             f"📄 CA : <code>{html.escape(mint)}</code>",
             "✅ CA envoyé à Trojan" if not skip else f"⏸ pas envoyé à Trojan : {html.escape(skip)}",
             f'<a href="https://pump.fun/coin/{mint}">pump.fun</a> · <a href="https://dexscreener.com/solana/{mint}">'
             f'DexScreener</a> · <a href="https://solscan.io/tx/{sig}">transaction</a>']
    try:
        r = http(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
                 {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": "\n".join(lines), "parse_mode": "HTML",
                  "disable_web_page_preview": True})
        if not r.get("ok"):
            print(f"  alerte Telegram non envoyée : {r.get('description')}", flush=True)
    except Exception as e:
        print(f"  alerte Telegram non envoyée : {type(e).__name__} {e}", flush=True)


def console():
    """Commands typed in the window while the script runs: pause / reprise of the sends to Trojan, etat."""
    for line in sys.stdin:
        cmd = line.strip().lower()
        if cmd in ("pause", "stop"):
            state["paused"] = True
            print("envois à Trojan EN PAUSE (la détection et les alertes continuent) ; tape « reprise » pour les "
                  "relancer", flush=True)
        elif cmd in ("reprise", "start"):
            state["paused"] = False
            print("envois à Trojan RELANCÉS", flush=True)
        elif cmd in ("etat", "état", "status"):
            print(f"envois {'EN PAUSE' if state['paused'] else 'actifs'} ; WebSocket {state['ws']} ; {state['tx']} "
                  f"transactions vues, {state['buys']} achats de tes callers, {state['sent']} CA envoyés", flush=True)
        elif cmd:
            print("commandes : pause, reprise, etat", flush=True)


def main():
    wallets = [w.strip() for w in arg("--wallets", "").split(",") if w.strip()] or list(CALLERS)
    os.makedirs(DATA, exist_ok=True)
    print("mode RÉEL : chaque CA retenu est envoyé au bot" if LIVE else
          "mode SIMULATION : rien n'est envoyé au bot (--live pour envoyer)")
    telegram(SEND_TO)
    if ALERTS and not (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")):
        print("alertes désactivées : définis TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID (ceux de ton bot d'alertes)")
        globals()["ALERTS"] = False
    filters = ", ".join(s for s in (f"achats d'au moins {MIN_SOL:g} SOL", MAX_COIN_AGE and
                                    f"coins de moins de {MAX_COIN_AGE:g} jours", MAX_RUN and
                                    f"prix ≤ x{MAX_RUN:g} son prix d'achat") if s)
    print(f"surveillance des wallets de {', '.join(CALLERS.get(w, w[:8]) for w in wallets)} sur la blockchain "
          f"({filters}) ; montant, slippage et ventes : ceux réglés dans Trojan"
          f"{' ; alertes Telegram actives' if ALERTS else ''} | journal {LOG}", flush=True)
    threading.Thread(target=ws_loop, args=(wallets,), daemon=True).start()
    threading.Thread(target=poll_loop, args=(wallets,), daemon=True).start()
    for _ in range(4):
        threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=console, daemon=True).start()
    print("pendant que le script tourne, tape « pause » ou « reprise » puis Entrée pour arrêter / relancer les envois "
          "à Trojan, « etat » pour voir où il en est", flush=True)
    k = 0
    while True:
        time.sleep(60)
        k += 1
        with lock:   # forget the signatures too old for either source to bring back
            for sig in [s for s, t in seen.items() if time.time() - t > 600]:
                del seen[sig]
        if UNTIL_PUMP and k % 5 == 0:
            try:
                http("https://frontend-api-v3.pump.fun/home-feed/new")
            except Exception:
                continue
            print(f"\n{now_str()} pump.fun répond de nouveau : retour aux callouts", flush=True)
            sys.exit(3)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\narrêt")
