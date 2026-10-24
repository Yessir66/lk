#!/usr/bin/env python3
"""
Follow pump.fun callers: as soon as one of them posts a callout, send the coin's address (CA) to Trojan from your
Telegram account (its Auto Buy buys it), or with --keypair buy it directly.

  callout_follow.py [--callers <username or wallet>,...] [--live] [--max-buys N] [--max-age 20] [--max-run 1.5]
                    [--max-coin-age 2] [--min-mcap USD] [--max-mcap USD] [--send-to @bot] [--rps 0.8]
                    [--copy <username or wallet>,... | --no-copy] [--copy-usd 2] [--post-delay 15] [--copy-trojan]
                    [--copy-tp 2] [--copy-sl 0.25] [--pump-wallet F] [--pump-token F] [--alerts <pump.fun username>]
  callout_follow.py --keypair bot.json [--live] [--amount-sol 0.05] [--slippage-bps 1500] [--jito-tip 0.001]
                    [--take-profit 2] [--stop-loss 0.6] [--max-hold 600] [--rpc-http URL]   (+ the options above)
  callout_follow.py --new-wallet bot.json

Callers: the CALLERS list below unless --callers is given.
Detection, on pump.fun's public frontend API (no account needed): GET /home-feed/new, the newest callouts of all
users, polled at --rps (the API allows about 1 request/s per endpoint and IP), so a callout is seen ~1 s after it
is posted; as a backstop for callouts the feed leaves out, each caller's own list (GET /callout/list/{wallet}) is
read in turn every 5 s (its 429s only pause it, silently). At startup, prints each caller's last 20 callouts: on
how many the wallets that followed the call (pump.fun's "tailers") were in profit 1 h later.
Filters: Solana coins only, callout seen within --max-age s, market cap at the callout within [--min-mcap,
--max-mcap], coin created less than --max-coin-age days before (default 2, 0: off; pump.fun's creation date, else
Jupiter's first-seen date, looked up in parallel with the price; a coin of unknown age is skipped), price not
already up more than --max-run x since the callout, each coin once, at most --max-buys per run (default 0: no
limit). Without --live nothing is sent.
Default mode: the CA is sent as a Telegram message from YOUR account to @solana_trojanbot (Trojan on Solana;
--send-to another bot), whose Auto Buy buys with Trojan's own wallet, amount, slippage and sell settings.
--max-run compares Jupiter's last trade price with the callout's and, when that price is unavailable, lets the CA
through. Trojan only takes messages from users, so the script logs in as you (`pip install telethon`) with the
api_id / api_hash of the "CALLOUT" app below (TG_API_ID / TG_API_HASH override them); the first run asks for your
phone number and login code, then keeps the session in data/telegram.session, which gives full access to your
Telegram account: never share it.
Repost (default mode only): when one of the COPY callers below (--copy to change them, --no-copy to turn it off)
calls a coin that passes the filters, the script buys --copy-usd (2 $) of it with YOUR pump.fun wallet instead of
sending it to Trojan (pump.fun only takes callouts on coins you hold, and the callout then shows your real
position), waits --post-delay (15) s after the buy is confirmed, then posts your own callout on the coin with one of
the CALLOUT_TEXTS below, picked at random (POST /callout/create). It needs two local files, never to be shared or
committed: data/pump_wallet.txt, the private key of the wallet of your pump.fun account (base58, as exported), and
data/pump_token.txt, your pump.fun session token (the auth_token cookie of pump.fun in your browser), which expires:
the file is re-read before each post, so it can be refreshed while the script runs. Without --live nothing is bought
or posted. Once its callout is out, each such position is sold at --copy-tp (x2) or --copy-sl (x0.25, i.e. -75 %)
times the SOL spent, from a Jupiter sell quote every 3 s (0 turns one off); the open positions are kept in
data/copy_positions.json and watched again after a restart. --max-run only stops the Trojan buy, never the repost
(the coin age filter stops both). --copy-trojan also sends the COPY callers' CAs to Trojan, as for the other
callers. --repost-only runs the repost alone, next to an instance already sending CAs to Trojan: no Telegram (its
session file stays with that instance), only the COPY callers, detected from their own lists every 1.5 s in turn, so
the all-users feed is left to the other instance.
While it runs, typing pause / reprise + Enter in the window suspends / resumes the repost (paused, those callers go
to Trojan like the others; the TP / SL of open positions go on); etat shows its state.
--keypair mode: Jupiter swap API (routes the pump.fun bonding curve and PumpSwap), signed locally, sent at once to
Jito's block engine (with a --jito-tip) and to the RPC; --max-run uses the buy quote, so it includes our own
price impact. Without --live the transaction is built, signed and simulated. Exits are optional and live only:
--take-profit / --stop-loss as a multiple of the SOL spent (from a Jupiter sell quote every 3 s) and --max-hold
seconds; without them the bot only buys. After a sale the empty token account is closed to recover its rent
(~0.002 SOL). Use a DEDICATED wallet holding only what the bot may spend; --new-wallet creates one.
--alerts <username>: a Telegram alert through a bot of yours (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) for each new
callout of an account that pump.fun user follows: who called, the text, the market cap at the call and now, the
CA, links. It reuses the feed read for detection (no extra pump.fun request but the follows, re-read every 10
min), and alerts are sent in the background so they never delay a buy.
While pump.fun answers scripts with a Cloudflare challenge (HTTP 403, since October 2026), callouts cannot be
read nor posted: the default mode then runs wallet_follow.py (to download next to this script), which sends
to Trojan the coins the callers buy on-chain, and comes back to the callouts once pump.fun answers again.
Output: data/callout_follow_<timestamp>.jsonl, one line per callout seen, buy and sale.
"""
import asyncio
import base64
import datetime
import html
import json
import os
import random
import ssl
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv[:-1] else d
num = lambda k: float(arg(k, None)) if k in sys.argv else None
API = "https://frontend-api-v3.pump.fun"
JUP = "https://lite-api.jup.ag/swap/v1"
JITO = "https://mainnet.block-engine.jito.wtf/api/v1/transactions"
RPC = arg("--rpc-http", "https://api.mainnet-beta.solana.com")
SOL = "So11111111111111111111111111111111111111112"
LIVE = "--live" in sys.argv
AMOUNT = int(float(arg("--amount-sol", "0.05")) * 1e9)
SLIPPAGE = int(arg("--slippage-bps", "1500"))
TIP = int(float(arg("--jito-tip", "0.001")) * 1e9)
MAX_BUYS = int(arg("--max-buys", "0"))   # 0: no limit
MAX_AGE = float(arg("--max-age", "20"))
MAX_RUN, MIN_MCAP, MAX_MCAP = num("--max-run"), num("--min-mcap"), num("--max-mcap")
MAX_COIN_AGE = float(arg("--max-coin-age", "2"))   # days; 0: off
TP, SL, HOLD = num("--take-profit"), num("--stop-loss"), num("--max-hold")
REPOST_ONLY = "--repost-only" in sys.argv   # next to a running Trojan instance: no Telegram, no feed, COPY only
COPY_TROJAN = "--copy-trojan" in sys.argv and not REPOST_ONLY   # COPY callers' CAs also go to Trojan
GAP = {"feed": 1 / float(arg("--rps", "0.8")), "list": 1.5 if REPOST_ONLY else 5.0, "other": 1.25}   # seconds
BUDGET = f"{MAX_BUYS} max" if MAX_BUYS else "illimités"
if MAX_COIN_AGE:
    BUDGET += f", coins de moins de {MAX_COIN_AGE:g} jours seulement"
SEND_TO = arg("--send-to", None if "--keypair" in sys.argv else "@solana_trojanbot")
TG_API_ID, TG_API_HASH = 30461091, "1ba7f769c94596baaf675447c02bb478"   # my.telegram.org app "CALLOUT"
CALLERS = ["6yVb4pxNwDfr6rovwNnBg3SyKSvDcHGD4WdFPN1JJBqm",   # 0xEthan
           "Be24Gbf5KisDk1LcWWZsBn8dvB816By7YzYF5zWZnRR6",   # chairman
           "5hAgYC8TJCcEZV7LTXAzkTrm7YL29YXyQQJPCNrG84zM",   # schoen
           "87rRdssFiTJKY4MGARa4G5vQ31hmR7MxSmhzeaJ5AAxJ",   # Dior
           "ardinRsN1mNYVeoJWTBsWeYeXvuR9UUDGMsCDKpb6AT",    # trunoest
           "CE44oKS3wpUerx8afyeii56u5oQjBLZknzm4Q2CYHUz9",   # ely
           "DuARUpsEzdVbVpSnbGipLJvWxaXUCPLDJDjNov6Rc6s6",   # HarryPaulDavies
           "EqBQhoU88ty3a3aDphDRCBuiRirjUsFSFi4UHGY64qvv",   # sadcrissy
           "2M2vLX34LXMg24dMEnjWHvRXS1tshpEDRWzmXgV8ENNZ",   # SossaDotKek
           "AH39BneW9UeWxQysUcwogQrYFdPvJuRVZ5w4ccq9SKfL"]   # _logjam
COPY =["87rRdssFiTJKY4MGARa4G5vQ31hmR7MxSmhzeaJ5AAxJ",      # Dior
        "CE44oKS3wpUerx8afyeii56u5oQjBLZknzm4Q2CYHUz9",      # ely
        "Be24Gbf5KisDk1LcWWZsBn8dvB816By7YzYF5zWZnRR6"]      # chairman
COPY_USD = float(arg("--copy-usd", "2"))
COPY_TP, COPY_SL = float(arg("--copy-tp", "2")), float(arg("--copy-sl", "0.25"))   # x the SOL spent; 0: off
COPY_BOOK = os.path.join(DATA, "copy_positions.json")
POST_DELAY = float(arg("--post-delay", "15"))
CALLOUT_TEXTS = ["let's run it, seems like a good narra",
                 "narra looks clean on this one, sending it",
                 "like this narrative a lot, let's see where it goes",
                 "good narra, early enough, let's run it",
                 "this one has a real narrative, i'm in"]
PUMP_WALLET = arg("--pump-wallet", os.path.join(DATA, "pump_wallet.txt"))
PUMP_TOKEN = arg("--pump-token", os.path.join(DATA, "pump_token.txt"))
ALERTS_USER = arg("--alerts", None)
if "--alerts" in sys.argv and (ALERTS_USER or "--").startswith("--"):
    ALERTS_USER = ""   # bare --alerts: the accounts the repost wallet's own pump.fun account follows
LOG =os.path.normpath(os.path.join(DATA, f"callout_follow_{int(time.time())}.jsonl"))
CA = "/root/.ccr/ca-bundle.crt"
SSL = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()

lock = threading.Lock()
positions = []      # open positions, watched by the exit thread
copy_positions = [] # the pump.fun wallet's repost buys, under --copy-tp / --copy-sl, kept in COPY_BOOK
next_call = {}      # pump.fun endpoint family -> earliest time of its next request
tg = {}             # --send-to mode: Telegram client and target bot
pump = {}           # repost: the pump.fun wallet's keypair and the COPY callers' wallets
alerts = {}         # --alerts: bot token, chat id, the user's wallet, the accounts it follows, callouts alerted


def http(url, body=None, timeout=10):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Accept": "application/json",
                                          "User-Agent": "Mozilla/5.0"})
    return json.load(urllib.request.urlopen(req, timeout=timeout, context=SSL))


def post_quiet(url, body):
    try:
        return http(url, body)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


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


def is_solana(addr):
    try:
        Pubkey.from_string(addr)
        return True
    except Exception:
        return False


def load_keypair(path):
    """A keypair file: the JSON array written by solana-keygen, or a base58 private key as exported by Phantom."""
    s = open(path).read().strip()
    return Keypair.from_bytes(bytes(json.loads(s))) if s.startswith("[") else Keypair.from_base58_string(s)


# ---------- pump.fun: callers and their callouts

def api(path, fam="other"):
    """GET on pump.fun's frontend API, each endpoint family paced on its own; None when rate limited or failing."""
    wait = next_call.get(fam, 0) - time.time()
    if wait > 0:
        time.sleep(wait)
    next_call[fam] = time.time() + GAP[fam]
    try:
        return http(API + path)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            ra = e.headers.get("Retry-After") or ""
            next_call[fam] = time.time() + (float(ra) if ra.isdigit() else 5)
            if fam != "list":   # the backstop list is throttled often and harmlessly
                print(f"  limite de requêtes pump.fun atteinte ({fam}), pause", flush=True)
        else:
            print(f"  pump.fun {path} : HTTP {e.code}", flush=True)
    except Exception as e:
        print(f"  pump.fun {path} : {type(e).__name__}", flush=True)
    return None


def resolve(name):
    """A caller given as a username or a wallet -> (wallet, username)."""
    if is_solana(name):
        prof = (api(f"/users/{name}/overview") or {}).get("profile") or {}
        print(f"  {prof.get('username', '?')} : wallet {name} ({prof.get('followers', '?')} followers)")
        return name, prof.get("username") or name[:8]
    hits = api(f"/users/search-v2?searchTerm={urllib.parse.quote(name)}&limit=10")
    if hits is None:
        sys.exit(f"pump.fun ne répond pas pour la recherche de {name}")
    exact = [h for h in hits if (h.get("username") or "").lower() == name.lower()]
    if not exact:
        sys.exit(f"utilisateur pump.fun introuvable : {name} (donne son wallet à la place)")
    h = exact[0]
    print(f"  {h['username']} : wallet {h['address']} ({h.get('followers', '?')} followers)")
    return h["address"], h["username"]


def callouts(wallet, n, fam="list"):
    r = api(f"/callout/list/{wallet}?limit={n}&sortBy=TIMESTAMP&sortOrder=DESC", fam)
    return None if r is None else [dict(c, author=wallet) for c in r.get("callouts") or []]


def feed_callouts():
    """The newest callouts of all pump.fun users, in the callout-list shape (the feed's calloutPrice is in USD)."""
    out = []
    for row in (api("/home-feed/new", "feed") or {}).get("coins") or []:
        p = row.get("position") or {}
        co = p.get("callout") or {}
        if not co.get("calloutId") or not co.get("calloutTimestamp"):
            continue
        created = datetime.datetime.fromisoformat(co["calloutTimestamp"].replace("Z", "+00:00")).timestamp()
        out.append({"calloutId": co["calloutId"], "author": p.get("walletAddress"), "coinMint": row.get("coinMint"),
                    "createdAt": created * 1000, "marketCap": co.get("calledOutAtMcap"), "thesis": co.get("thesis"),
                    "calloutPriceUsd": co.get("calloutPrice"), "chain": co.get("chain"), "perp": co.get("perp"),
                    "author_name": p.get("userName"), "name": row.get("coinName"), "symbol": row.get("symbol"),
                    "mcap_now": row.get("marketCap")})
    return out


def coin_age_days(mint):
    """Days since the coin was created: pump.fun's record (any coin it lists), else Jupiter's first-seen date,
    which is later than the real one for old coins; None if neither knows the coin."""
    try:
        ts = http(f"{API}/coins-v2/{mint}").get("created_timestamp")
        if ts:
            return (time.time() - ts / 1000) / 86400
    except Exception:
        pass
    try:
        s = (http(f"https://lite-api.jup.ag/price/v3?ids={mint}").get(mint) or {}).get("createdAt")
        if s:
            return (time.time() - datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()) / 86400
    except Exception:
        pass
    return None


def age_skip(age):
    """Skip reason from the coin's age (days), else None."""
    if MAX_COIN_AGE and age is None:
        return "âge du coin inconnu"
    if MAX_COIN_AGE and age > MAX_COIN_AGE:
        return f"coin créé il y a {age:.1f} jours (> {MAX_COIN_AGE:g})"
    return None


def run_skip(run):
    """Skip reason from the price run since the callout, else None."""
    return f"prix déjà x{run:.2f} depuis le call (> x{MAX_RUN})" if MAX_RUN and run and run > MAX_RUN else None


def summary(cs):
    """How the wallets that followed this caller's recent calls did after 1 h (pump.fun's tail stats)."""
    t = [c for c in cs if c.get("tailBuyers") and c.get("tailPnl1hUsd") is not None]
    mcs = [c["marketCap"] for c in cs if c.get("marketCap")]
    if not t:
        return f"{len(cs)} calls récents, pas de statistiques de followers"
    win = sum(c["tailPnl1hUsd"] > 0 for c in t)
    return (f"{len(cs)} derniers calls : followers en profit à 1 h sur {win}/{len(t)} "
            f"(total {sum(c['tailPnl1hUsd'] for c in t):+,.0f} $), market cap médiane au call "
            f"{statistics.median(mcs) if mcs else 0:,.0f} $")


# ---------- execution: Jupiter quote and swap, sending, confirmation

def quote(src, dst, amount):
    return http(f"{JUP}/quote?inputMint={src}&outputMint={dst}&amount={amount}&slippageBps={SLIPPAGE}")


def swap(q, kp, tip=TIP):
    s = http(f"{JUP}/swap", {"quoteResponse": q, "userPublicKey": str(kp.pubkey()), "wrapAndUnwrapSol": True,
                             "dynamicComputeUnitLimit": True, "prioritizationFeeLamports": {"jitoTipLamports": tip}})
    unsigned = VersionedTransaction.from_bytes(base64.b64decode(s["swapTransaction"]))
    return VersionedTransaction(unsigned.message, [kp])


def send(tx, funded):
    """Live: to Jito and the RPC at once. Otherwise: simulate (needs a funded wallet). Returns an error or None."""
    raw = base64.b64encode(bytes(tx)).decode()
    if not LIVE:
        if not funded:
            return "non simulée (pas de --keypair)"
        return rpc("simulateTransaction", [raw, {"encoding": "base64", "sigVerify": True,
                                                 "commitment": "processed"}])["value"]["err"]
    body = {"jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
            "params": [raw, {"encoding": "base64", "skipPreflight": True}]}
    with ThreadPoolExecutor(2) as ex:
        rs = list(ex.map(lambda u: post_quiet(u, body), (JITO, RPC)))
    return None if any("result" in r for r in rs) else [r.get("error") for r in rs]


def confirm(sig, timeout=40):
    """(slot, error) once the transaction is confirmed, None if it never landed."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = rpc("getSignatureStatuses", [[sig]])["value"][0]
        except Exception:
            v = None
        if v and v.get("confirmationStatus") in ("confirmed", "finalized"):
            return v["slot"], v.get("err")
        time.sleep(0.5)
    return None


def token_accounts(owner, mint):
    """[(account, token program, raw amount)] of the owner for that coin (SPL Token and Token-2022)."""
    r = rpc("getTokenAccountsByOwner", [owner, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}])
    return [(a["pubkey"], a["account"]["owner"], int(a["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"]))
            for a in r["value"]]


def close_empty_accounts(kp, mint):
    """Close the coin's empty token accounts (CloseAccount, same opcode in both token programs): rent back to us."""
    try:
        empty = [(a, prog) for a, prog, amt in token_accounts(str(kp.pubkey()), mint) if amt == 0]
        if not empty:
            return
        ixs = [Instruction(Pubkey.from_string(prog), bytes([9]),
                           [AccountMeta(Pubkey.from_string(a), False, True), AccountMeta(kp.pubkey(), False, True),
                            AccountMeta(kp.pubkey(), True, False)]) for a, prog in empty]
        bh = Hash.from_string(rpc("getLatestBlockhash", [{"commitment": "confirmed"}])["value"]["blockhash"])
        tx = VersionedTransaction(MessageV0.try_compile(kp.pubkey(), ixs, [], bh), [kp])
        rpc("sendTransaction", [base64.b64encode(bytes(tx)).decode(), {"encoding": "base64", "skipPreflight": True}])
    except Exception as e:
        print(f"  fermeture du compte {mint[:8]} impossible : {e}", flush=True)


# ---------- --send-to mode: hand the CA to a Telegram trading bot

def telegram(target):
    """Log in as the user (first run: phone number and code), resolve the bot, keep the connection alive."""
    try:
        from telethon.sync import TelegramClient
    except ImportError:
        sys.exit("le mode --send-to demande telethon : pip install telethon")
    api_id, api_hash = os.environ.get("TG_API_ID") or TG_API_ID, os.environ.get("TG_API_HASH") or TG_API_HASH
    # never stop reconnecting: after a network drop the default 5 attempts left the bot unable to send CAs
    client = TelegramClient(os.path.join(DATA, "telegram"), int(api_id), api_hash, connection_retries=-1, retry_delay=3)
    client.start()
    bot = client.get_entity(target)
    if not getattr(bot, "bot", False):
        sys.exit(f"{target} n'est pas un bot Telegram : rien ne sera envoyé")
    print(f"Telegram connecté : les CA iront à @{bot.username} ({bot.first_name}"
          f"{', compte vérifié' if bot.verified else ''}). Vérifie que c'est bien ton bot Trojan.")
    # run the client's loop in the background so pings keep the connection open between callouts
    threading.Thread(target=client.loop.run_forever, daemon=True).start()
    tg.update(client=client, bot=bot)


def price_run(c):
    """Price now / price at the callout, from Jupiter's price API (last trade, no RPC); None if unavailable."""
    mint = c["coinMint"]
    try:
        p = http(f"https://lite-api.jup.ag/price/v3?ids={mint}").get(mint) or {}
    except Exception as e:
        p = {"error": e}
    if p.get("usdPrice") and c.get("calloutPriceUsd"):
        return p["usdPrice"] / c["calloutPriceUsd"]
    print(f"  prix indisponible ({p.get('error', 'coin inconnu de Jupiter')}) : envoi sans contrôle --max-run", flush=True)
    return None


def forward(c, rec, run, seen_at, state):
    """Send the CA to the bot; its Auto Buy does the rest."""
    mint, age = c["coinMint"], rec.get("coin_age_days")
    age_s = f", coin créé il y a {age * 24:.0f} h" if age is not None else ""
    run_s = age_s + (f", prix x{run:.2f} vs le call" if run else "")
    if not LIVE:
        print(f"  SIMULATION : enverrait {mint} à @{tg['bot'].username}{run_s}", flush=True)
        return log({**rec, "type": "forward", "simulated": True})
    cl = tg["client"]
    fut = asyncio.run_coroutine_threadsafe(cl.send_message(tg["bot"], mint), cl.loop)
    try:
        fut.result(8)
    except Exception as e:
        fut.cancel()   # else the message would go out whenever Telegram is back, buying a stale call
        print(f"  CA NON ENVOYÉ à Trojan : Telegram injoignable ({type(e).__name__}). La reconnexion continue toute "
              f"seule ; vérifie ta connexion internet", flush=True)
        return log({**rec, "type": "forward", "error": f"telegram {type(e).__name__}: {e}"})
    sent_at = time.time()
    state["sent"] += 1
    print(f"  CA envoyé à @{tg['bot'].username} {sent_at - seen_at:.1f} s après détection "
          f"({sent_at - c['createdAt'] / 1000:.1f} s après le call){run_s}", flush=True)
    log({**rec, "type": "forward", "sent_at": sent_at, "callout_to_send_s": round(sent_at - c["createdAt"] / 1000, 2)})


# ---------- repost: quote the COPY callers' callouts on the user's pump.fun profile

def pump_call(method, path, fields=None):
    """Request on pump.fun's API as the user (token file re-read each time); (HTTP status, body text)."""
    token = open(PUMP_TOKEN).read().strip()
    headers = {"Authorization": f"Bearer {token}", "Cookie": f"auth_token={token}", "Origin": "https://pump.fun",
               "User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    data = None
    if fields is not None:   # the callout routes take multipart form fields
        b = f"----callout{int(time.time() * 1000)}"
        data = "".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in fields.items())
        data = (data + f"--{b}--\r\n").encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={b}"
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15, context=SSL) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def setup_copy(copies):
    """Check the wallet file, the session token and that they match; on any problem the repost stays off."""
    for path, what in ((PUMP_WALLET, "la clé privée de ton wallet pump.fun"), (PUMP_TOKEN, "ton jeton de session pump.fun")):
        if not os.path.exists(path):
            return print(f"repost désactivé : crée {os.path.normpath(path)} avec {what}")
    kp = load_keypair(PUMP_WALLET)
    status, body = pump_call("GET", "/auth/my-profile")
    if status != 200:
        return print(f"repost désactivé : pump.fun refuse ton jeton (HTTP {status}), recopie le cookie auth_token dans "
                     f"{os.path.normpath(PUMP_TOKEN)}")
    me = json.loads(body)
    if me.get("address") != str(kp.pubkey()):
        return print(f"repost désactivé : la clé du fichier ouvre le wallet {kp.pubkey()}, mais ta session pump.fun est "
                     f"celle du wallet {me.get('address')}")
    bal = rpc("getBalance", [str(kp.pubkey())])["value"] / 1e9
    exp = time.strftime("%d/%m %H:%M", time.localtime(me["exp"])) if me.get("exp") else "?"
    print(f"repost des calls de {', '.join(copies.values())} : {COPY_USD:g} $ par coin avec ton wallet pump.fun "
          f"{kp.pubkey()} ({bal:.4f} SOL), jeton valable jusqu'au {exp}")
    if bal < 0.03:
        print("  attention : moins de 0.03 SOL sur ce wallet, les achats du repost risquent d'échouer")
    pump.update(kp=kp, copy=set(copies))
    if LIVE and (COPY_TP or COPY_SL):
        load_copy_book(kp)
        threading.Thread(target=exit_loop, args=(kp, copy_positions, COPY_TP, COPY_SL, None, save_copy_book),
                         daemon=True).start()
        rules = ", ".join(s for s in (COPY_TP and f"TP x{COPY_TP:g}", COPY_SL and f"SL -{(1 - COPY_SL) * 100:.0f} %") if s)
        print(f"  ventes automatiques du wallet pump.fun : {rules}"
              + (f" ; {len(copy_positions)} position(s) reprise(s)" if copy_positions else ""))


def save_copy_book():
    with lock:
        json.dump(copy_positions, open(COPY_BOOK, "w"))


def load_copy_book(kp):
    """Positions left by an earlier run and still held are watched again."""
    if not os.path.exists(COPY_BOOK):
        return
    for p in json.load(open(COPY_BOOK)):
        try:
            p["amount"] = sum(a for _, _, a in token_accounts(str(kp.pubkey()), p["mint"]))
        except Exception:
            pass   # keep the saved amount; the exit loop refreshes it if a sale fails
        if p.get("amount"):
            copy_positions.append(p)
    save_copy_book()


def copy_callout(c, label):
    """Buy COPY_USD of the coin with the pump.fun wallet, wait POST_DELAY s, post our own callout on it."""
    mint, kp, text = c["coinMint"], pump["kp"], random.choice(CALLOUT_TEXTS)
    try:
        sol_usd = http(f"https://lite-api.jup.ag/price/v3?ids={SOL}")[SOL]["usdPrice"]
        lamports = int(COPY_USD / sol_usd * 1e9)
        tip = max(100_000, min(TIP, lamports // 50))   # the usual Jito tip, at most 2 % of a small buy
        tx = swap(quote(SOL, mint, lamports), kp, tip)
        if not LIVE:
            print(f"  SIMULATION repost : achèterait {COPY_USD:g} $ du coin avec ton wallet pump.fun, puis {POST_DELAY:g} s "
                  f"après publierait sur ton profil : « {text} »", flush=True)
            return log({"type": "repost", "mint": mint, "text": text, "simulated": True})
        err = send(tx, True)
        landed = None if err else confirm(str(tx.signatures[0]))
        if not landed or landed[1]:
            why = err or (landed and landed[1]) or "pas inclus dans un bloc"
            print(f"\n{now_str()} repost {mint[:8]} : achat de {COPY_USD:g} $ non passé ({why}), pas de callout", flush=True)
            return log({"type": "repost", "mint": mint, "error": str(why)})
        print(f"\n{now_str()} ACHETÉ {COPY_USD:g} $ de {mint[:8]} avec ton wallet pump.fun (call de {label}), callout "
              f"dans {POST_DELAY:g} s", flush=True)
        time.sleep(POST_DELAY)
        for _ in range(6):   # pump.fun's holding check can lag the chain by a few seconds
            status, body = pump_call("POST", "/callout/create", {"coinMint": mint, "thesis": text})
            if status < 300 or "INSUFFICIENT_BALANCE" not in body:
                break
            time.sleep(2)
        if status < 300:
            print(f"\n{now_str()} CALLOUT PUBLIÉ sur ton profil : {mint} « {text} »", flush=True)
        elif status == 401:
            print(f"\n{now_str()} repost {mint[:8]} : jeton pump.fun expiré, recopie le cookie auth_token dans "
                  f"{os.path.normpath(PUMP_TOKEN)} (pas besoin de relancer)", flush=True)
        else:
            print(f"\n{now_str()} repost {mint[:8]} refusé par pump.fun (HTTP {status}) : {body[:200]}", flush=True)
        log({"type": "repost", "mint": mint, "text": text, "status": status, "response": body[:500]})
        if COPY_TP or COPY_SL:   # watched only once the callout is out: pump.fun wants the coin held to post it
            held = 0
            for _ in range(3):
                try:
                    held = sum(a for _, _, a in token_accounts(str(kp.pubkey()), mint))
                except Exception:
                    pass
                if held:
                    break
                time.sleep(2)
            if not held:
                print(f"  attention : solde de {mint[:8]} illisible, pas de TP / SL automatique sur cette position", flush=True)
            else:
                with lock:
                    copy_positions.append({"mint": mint, "amount": held, "cost": lamports / 1e9, "t": time.time(),
                                           "caller": label, "tip": tip})
                save_copy_book()
    except Exception as e:
        print(f"\n{now_str()} repost {mint[:8]} : échec ({e})", flush=True)
        log({"type": "repost", "mint": mint, "error": str(e)})


def console():
    """Commands typed in the window while the script runs: pause / reprise of the repost, etat."""
    for line in sys.stdin:
        cmd = line.strip().lower()
        if cmd in ("pause", "stop"):
            pump["paused"] = True
            print("repost EN PAUSE : plus d'achat avec le wallet pump.fun ni de callout sur ton profil (ces calls vont "
                  "à Trojan comme les autres, les ventes TP / SL continuent) ; tape « reprise » pour le relancer", flush=True)
        elif cmd in ("reprise", "start"):
            pump["paused"] = False
            print("repost RELANCÉ", flush=True)
        elif cmd in ("etat", "état", "status"):
            with lock:
                n = len(copy_positions)
            print(f"repost {'EN PAUSE' if pump.get('paused') else 'actif'} ; {n} position(s) du wallet pump.fun suivie(s)",
                  flush=True)
        elif cmd:
            print("commandes : pause, reprise, etat", flush=True)


# ---------- --alerts: a Telegram message per callout of the accounts a pump.fun user follows

def setup_alerts(username):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return print("alertes désactivées : définis TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID (ceux de ton bot d'alertes)")
    try:
        if not username:
            raise SystemExit("aucun compte donné après --alerts")
        wallet, _ = resolve(username)
    except SystemExit as e:   # an unknown name (a renamed profile) must not stop the bot
        if not pump.get("kp"):
            return print(f"alertes désactivées : {e} ; donne plutôt ton wallet, il ne change pas si tu renommes ton profil")
        wallet = str(pump["kp"].pubkey())   # the pump.fun account of the repost wallet: the user's own
        print(f"{username + ' introuvable sur pump.fun (profil renommé ?) : ' if username else ''}alertes pour les "
              f"comptes suivis par ton propre compte, wallet {wallet}")
        username = "ton compte"
    alerts.update(token=token, chat=chat, owner=wallet, follows={}, seen=set())
    refresh_follows()
    print(f"alertes Telegram pour les {len(alerts['follows'])} comptes suivis par {username}")


def refresh_follows():
    rows = api(f"/following/v3/following/{alerts['owner']}?limit=500")
    if rows is not None:
        alerts["follows"] = {u["address"]: u["username"] for u in rows}
    alerts["t"] = time.time()


def fmt_usd(v):
    return "?" if v is None else f"${v / 1e6:.2f}M" if v >= 1e6 else f"${v / 1e3:.1f}k" if v >= 1e3 else f"${v:.0f}"


def send_alert(c):
    mint = c["coinMint"]
    name = f"{c.get('name') or ''} (${c['symbol']})" if c.get("symbol") else mint[:6] + "…"
    mc = f"💰 MC au call : <b>{fmt_usd(c.get('marketCap'))}</b>"
    if c.get("mcap_now"):
        mc += f" · maintenant {fmt_usd(c['mcap_now'])}"
    lines = [f"📣 <b>{html.escape(c.get('author_name') or '?')}</b> a callé <b>{html.escape(name)}</b>"]
    if c.get("thesis"):
        lines.append(f"💬 « {html.escape(c['thesis'])} »")
    lines += [mc, f"📄 CA : <code>{html.escape(mint)}</code>",
              f'<a href="https://pump.fun/coin/{mint}">pump.fun</a> · <a href="https://dexscreener.com/solana/{mint}">'
              f'DexScreener</a> · {time.strftime("%H:%M:%S", time.gmtime(c["createdAt"] / 1000))} UTC']
    r = post_quiet(f"https://api.telegram.org/bot{alerts['token']}/sendMessage",
                   {"chat_id": alerts["chat"], "text": "\n".join(lines), "parse_mode": "HTML",
                    "disable_web_page_preview": True})
    if not r.get("ok"):
        print(f"  alerte Telegram non envoyée : {r.get('description') or r.get('error')}", flush=True)


def notify(feed):
    """Alert each new callout, in the feed just read, of an account the --alerts user follows."""
    if time.time() - alerts["t"] > 600:   # follows and unfollows are picked up every 10 min
        refresh_follows()
    for c in feed:
        if c["author"] in alerts["follows"] and c["calloutId"] not in alerts["seen"]:
            alerts["seen"].add(c["calloutId"])
            if time.time() - c["createdAt"] / 1000 < 600:
                threading.Thread(target=send_alert, args=(c,), daemon=True).start()


# ---------- strategy: buy on callout, optional exits

def on_callout(c, label, kp, funded, state):
    mint, seen_at = c["coinMint"], time.time()
    delay, mc = seen_at - c["createdAt"] / 1000, c.get("marketCap") or 0
    rec = {"type": "callout", "caller": label, "callout_id": c["calloutId"], "mint": mint,
           "created_at": c["createdAt"] / 1000, "seen_at": seen_at, "delay_s": round(delay, 2), "mcap_at_callout": mc,
           "thesis": c.get("thesis")}
    print(f"\n{now_str()} CALLOUT {label} -> {mint} | mcap {mc:,.0f} $ | vu {delay:.1f} s après", flush=True)
    skip = ("pas un coin Solana" if c.get("chain") or c.get("perp") or not is_solana(mint) else
            f"vu trop tard ({delay:.0f} s > {MAX_AGE:.0f} s)" if delay > MAX_AGE else
            "déjà tenté sur ce coin" if mint in state["tried"] else
            "market cap hors limites" if (MIN_MCAP and mc < MIN_MCAP) or (MAX_MCAP and mc > MAX_MCAP) else
            f"budget atteint ({MAX_BUYS} achats)" if MAX_BUYS and state["sent"] >= MAX_BUYS else None)
    if skip:
        print(f"  ignoré : {skip}", flush=True)
        return log({**rec, "skipped": skip})
    state["tried"].add(mint)
    t0 = time.time()
    try:
        if SEND_TO:
            with ThreadPoolExecutor(2) as ex:   # coin age and price at once
                fa = ex.submit(coin_age_days, mint) if MAX_COIN_AGE else None
                fr = ex.submit(price_run, c) if MAX_RUN else None
                age, run = fa and fa.result(), fr and fr.result()
            rec.update({"coin_age_days": age, "run_since_callout": run})
            skip = age_skip(age)
            if skip:
                print(f"  ignoré : {skip}", flush=True)
                return log({**rec, "skipped": skip})
            copied = c.get("author") in pump.get("copy", ()) and not pump.get("paused")
            if copied:   # bought with the pump.fun wallet (and Trojan if asked); --max-run does not stop it
                if LIVE:
                    state["sent"] += 1
                threading.Thread(target=copy_callout, args=(c, label), daemon=True).start()
                if not COPY_TROJAN:
                    return
            skip = run_skip(run)
            if skip:
                print(f"  pas d'envoi à Trojan : {skip}" + (" (le repost se fait quand même)" if copied else ""), flush=True)
                return log({**rec, "skipped": skip})
            return forward(c, rec, run, seen_at, state)
        with ThreadPoolExecutor(3) as ex:
            fq, fs = ex.submit(quote, SOL, mint, AMOUNT), ex.submit(rpc, "getTokenSupply", [mint])
            fa = ex.submit(coin_age_days, mint) if MAX_COIN_AGE else None
            q, dec, age = fq.result(), fs.result()["value"]["decimals"], fa and fa.result()
        tokens = int(q["outAmount"]) / 10 ** dec
        run = (float(q["swapUsdValue"]) / tokens / c["calloutPriceUsd"]
               if q.get("swapUsdValue") and c.get("calloutPriceUsd") else None)
        rec.update({"coin_age_days": age, "run_since_callout": run, "quote_ms": round(1000 * (time.time() - t0))})
        skip = age_skip(age) or run_skip(run)
        if skip:
            print(f"  ignoré : {skip}", flush=True)
            return log({**rec, "skipped": skip})
        tx = swap(q, kp)
        t1 = time.time()
        err = send(tx, funded)
    except Exception as e:
        print(f"  échec : {type(e).__name__} {e}", flush=True)
        return log({**rec, "error": str(e)})
    sig, sent_at = str(tx.signatures[0]), time.time()
    rec.update({"type": "buy", "sig": sig, "build_ms": round(1000 * (t1 - t0)),
                "sent_at": sent_at, "callout_to_send_s": round(sent_at - c["createdAt"] / 1000, 2)})
    run_s = f"x{run:.2f}" if run else "?"
    if not LIVE:
        print(f"  SIMULATION : {AMOUNT / 1e9} SOL -> {tokens:,.0f} tokens, prix {run_s} vs le call, prêt "
              f"{sent_at - seen_at:.1f} s après détection ({sent_at - c['createdAt'] / 1000:.1f} s après le call), "
              f"simulation : {err or 'OK'}", flush=True)
        return log({**rec, "simulated": True, "sim_error": err})
    state["sent"] += 1
    if err:
        print(f"  envoi refusé : {err}", flush=True)
        return log({**rec, "error": err})
    print(f"  envoyé {sent_at - seen_at:.1f} s après détection ({sent_at - c['createdAt'] / 1000:.1f} s après le "
          f"call), prix {run_s} vs le call : https://solscan.io/tx/{sig}", flush=True)
    threading.Thread(target=settle, args=(rec, kp, mint, dec, label), daemon=True).start()


def settle(rec, kp, mint, dec, label):
    """Wait for the buy to land, then hand the position to the exit thread."""
    landed = confirm(rec["sig"])
    if not landed or landed[1]:
        why = "pas inclus dans un bloc" if not landed else landed[1]
        print(f"\n{now_str()} achat {mint[:8]} non passé : {why}", flush=True)
        return log({**rec, "error": str(why)})
    held = 0
    for _ in range(5):  # the RPC can lag the confirmation by a moment
        try:
            held = sum(a for _, _, a in token_accounts(str(kp.pubkey()), mint))
        except Exception:
            pass
        if held:
            break
        time.sleep(1)
    print(f"\n{now_str()} ACHETÉ {mint[:8]} : {held / 10 ** dec:,.0f} tokens pour {AMOUNT / 1e9} SOL (slot {landed[0]})",
          flush=True)
    log({**rec, "slot": landed[0], "tokens_raw": held})
    if held and (TP or SL or HOLD):
        with lock:
            positions.append({"mint": mint, "amount": held, "cost": AMOUNT / 1e9, "t": time.time(), "caller": label})


def exit_loop(kp, book, tp, sl, hold, save=None):
    """Sell each position of book (held by kp) at x tp, at x sl or after hold s, from Jupiter sell quotes."""
    while True:
        time.sleep(max(3, len(book)))   # at most about one quote a second
        with lock:
            todo = list(book)
        for p in todo:
            try:
                q = quote(p["mint"], SOL, p["amount"])
                mult, held_s = int(q["outAmount"]) / 1e9 / p["cost"], time.time() - p["t"]
                why = ("take-profit" if tp and mult >= tp else "stop-loss" if sl and mult <= sl else
                       "durée max" if hold and held_s >= hold else None)
                if not why:
                    continue
                tx = swap(q, kp, p.get("tip", TIP))
                err = send(tx, True)
                landed = None if err else confirm(str(tx.signatures[0]))
                if landed and not landed[1]:
                    with lock:
                        book.remove(p)
                    if save:
                        save()
                    print(f"\n{now_str()} VENDU {p['mint'][:8]} ({why}) : x{mult:.2f} après {held_s:.0f} s, "
                          f"{int(q['outAmount']) / 1e9:.4f} SOL pour {p['cost']:.4f} SOL (call de {p['caller']})", flush=True)
                    log({"type": "sell", "mint": p["mint"], "caller": p["caller"], "reason": why, "multiple": mult,
                         "held_s": held_s, "sig": str(tx.signatures[0]), "slot": landed[0]})
                    close_empty_accounts(kp, p["mint"])
                else:
                    p["amount"] = sum(a for _, _, a in token_accounts(str(kp.pubkey()), p["mint"]))
                    if p["amount"] == 0:   # sold elsewhere
                        with lock:
                            book.remove(p)
                        if save:
                            save()
                    print(f"  vente {p['mint'][:8]} non passée ({err or (landed and landed[1]) or 'pas incluse'}), "
                          f"nouvel essai", flush=True)
            except Exception as e:
                print(f"  suivi {p['mint'][:8]} : {e}", flush=True)


def pump_blocked():
    """True while pump.fun answers scripts with Cloudflare's challenge (HTTP 403, since October 2026) instead of data."""
    try:
        http(API + "/home-feed/new")
    except urllib.error.HTTPError as e:
        return e.code == 403 and e.headers.get("cf-mitigated") == "challenge"
    except Exception:
        pass
    return False


def follow_wallets_meanwhile():
    """pump.fun closed to scripts: run wallet_follow.py (next to this script) on the callers' wallets, which sends
    the CA of each coin they buy to Trojan and exits with code 3 once pump.fun answers again; the callouts then
    take over. --max-run is not passed on: there it is measured against the caller's own buy price."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wallet_follow.py")
    if not os.path.exists(path):
        sys.exit(f"pump.fun bloque les scripts (protection Cloudflare) : callouts illisibles. Télécharge "
                 f"wallet_follow.py dans {os.path.dirname(path)} pour suivre les achats de tes callers en attendant")
    opts = [o for o in ("--live", "--alerts") if o in sys.argv] + ["--until-pump-fun"]
    opts += [x for o in ("--max-coin-age", "--send-to", "--rpc-http") if o in sys.argv[:-1] for x in (o, arg(o, ""))]
    wallets = [n.strip() for n in arg("--callers", "").split(",") if is_solana(n.strip())]
    if wallets:
        opts += ["--wallets", ",".join(wallets)]
    while pump_blocked():
        print("pump.fun bloque les scripts (protection Cloudflare) : callouts, repost et alertes de callouts "
              "impossibles. En attendant : suivi des achats de tes callers sur la blockchain (wallet_follow.py), "
              "retour automatique aux callouts dès que pump.fun répond de nouveau\n", flush=True)
        code = subprocess.call([sys.executable, path, *opts])
        if code != 3:
            sys.exit(code)


def main():
    if "--new-wallet" in sys.argv:
        path, kp = arg("--new-wallet", "bot.json"), Keypair()
        if os.path.exists(path):
            sys.exit(f"{path} existe déjà")
        json.dump(list(bytes(kp)), open(path, "w"))
        return print(f"nouveau wallet {kp.pubkey()} écrit dans {path} : envoie-lui seulement ce que le bot peut dépenser")
    if SEND_TO and not REPOST_ONLY and pump_blocked():
        follow_wallets_meanwhile()
    names = [] if REPOST_ONLY else [n.strip() for n in arg("--callers", "").split(",") if n.strip()] or CALLERS
    kp_path = arg("--keypair", None)
    if LIVE and not kp_path and not SEND_TO:
        sys.exit("--live demande un --keypair (un wallet dédié au bot)")
    kp = load_keypair(kp_path) if kp_path else Keypair()
    os.makedirs(DATA, exist_ok=True)
    if REPOST_ONLY:
        print("mode REPOST SEUL : achats avec ton wallet pump.fun et callouts sur ton profil, sans Telegram ni Trojan (le "
              "script Trojan déjà lancé n'est pas touché)" + ("" if LIVE else " | SIMULATION : rien n'est acheté ni publié"))
    elif SEND_TO:
        print("mode TELEGRAM RÉEL : chaque CA retenu est envoyé au bot" if LIVE else
              "mode TELEGRAM SIMULATION : rien n'est envoyé au bot (--live pour envoyer)")
        telegram(SEND_TO)
        print(f"montant, slippage et ventes : ceux réglés dans le bot | envois {BUDGET}")
    else:
        print("mode RÉEL : les achats sont envoyés" if LIVE else "mode SIMULATION : rien n'est envoyé (--live pour acheter)")
        if kp_path:
            bal = rpc("getBalance", [str(kp.pubkey())])["value"] / 1e9
            print(f"wallet {kp.pubkey()} : {bal:.4f} SOL")
            if LIVE and bal < (AMOUNT + TIP) / 1e9 + 0.01:
                sys.exit("solde insuffisant pour un achat (montant + tip + ~0.01 SOL de frais et de rent)")
        exits = ", ".join(s for s in (TP and f"take-profit x{TP:g}", SL and f"stop-loss x{SL:g}",
                                      HOLD and f"durée max {HOLD:g} s") if s)
        print(f"{AMOUNT / 1e9} SOL par call, slippage {SLIPPAGE / 100:g} %, tip Jito {TIP / 1e9} SOL, achats {BUDGET}, "
              f"sorties : {exits or 'aucune (achat seulement)'}")
    labels = dict(resolve(n) for n in names)   # wallet -> username
    copy_names = [] if "--no-copy" in sys.argv or not SEND_TO else (
        [n.strip() for n in arg("--copy", "").split(",") if n.strip()] or COPY)
    copies = dict((n, labels[n]) if n in labels else resolve(n) for n in copy_names)
    labels.update(copies)   # a copied caller is followed too
    if copies:
        setup_copy(copies)
    if ALERTS_USER is not None and not REPOST_ONLY:
        setup_alerts(ALERTS_USER)
    if REPOST_ONLY and not pump.get("copy"):
        sys.exit("repost impossible (voir ci-dessus) : rien à faire en mode --repost-only")
    seen = set()
    print("lecture des 20 derniers calls de chaque caller...", flush=True)
    for wallet, label in labels.items():
        # paced like the startup lookups, not like the slow backstop; a caller whose list fails here is fine:
        # its old callouts are skipped by age in the loop
        cs = callouts(wallet, 20, "other")
        seen |= {c["calloutId"] for c in cs or []}
        print(f"  {label} : {summary(cs) if cs is not None else 'statistiques indisponibles (pump.fun ralentit)'}",
              flush=True)
    if REPOST_ONLY:
        print(f"surveillance de {len(labels)} caller(s) par leurs profils, chacun relu toutes les "
              f"~{len(labels) * GAP['list']:.1f} s | journal {LOG}", flush=True)
    else:
        ids = {c["calloutId"] for c in feed_callouts()}
        seen |= ids
        if alerts:
            alerts["seen"] |= ids   # no alert for callouts posted before the start
        print(f"surveillance de {len(labels)} caller(s) : flux de tous les callouts toutes les {GAP['feed']:.2f} s (un "
              f"call est vu ~1 s après sa publication), chaque profil relu toutes les "
              f"~{len(labels) * GAP['list']:.0f} s en secours | journal {LOG}", flush=True)
    if LIVE and not SEND_TO and (TP or SL or HOLD):
        threading.Thread(target=exit_loop, args=(kp, positions, TP, SL, HOLD), daemon=True).start()
    if pump.get("copy"):
        threading.Thread(target=console, daemon=True).start()
        print("pendant que le script tourne, tape « pause » ou « reprise » puis Entrée pour arrêter / relancer le repost "
              "sur ton profil, « etat » pour voir où il en est", flush=True)
    state, wallets, k = {"tried": set(), "sent": 0}, list(labels), 0
    while True:
        # whichever source is due first: the all-users feed, or the next caller's own list
        if not REPOST_ONLY and next_call.get("feed", 0) <= next_call.get("list", 0):
            feed = feed_callouts()
            new = [c for c in feed if c["author"] in labels]
        else:
            feed, new = None, callouts(wallets[k % len(wallets)], 5) or []
            k += 1
        for c in sorted(new, key=lambda c: c["createdAt"]):
            if c["calloutId"] not in seen:
                seen.add(c["calloutId"])
                if time.time() - c["createdAt"] / 1000 < 600:   # older ones: a list that failed at startup
                    on_callout(c, labels[c["author"]], kp, bool(kp_path), state)
        if alerts and feed:   # after the buys, so the follows refresh never delays one
            notify(feed)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        with lock:
            left = [p["mint"] for p in positions + copy_positions]
        print(f"\narrêt{' — positions encore ouvertes : ' + ', '.join(left) if left else ''}"
              f"{' (celles du wallet pump.fun seront reprises au prochain lancement)' if copy_positions else ''}")
