#!/usr/bin/env python3
"""
Be told when a coin you hand it suddenly rises or falls: each CA you give it is watched for --watch minutes and a
Telegram alert goes out (your alert bot: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) when
  - it rises by --spike % (default 40) within --spike-window s (default 10): the big candle after a slow bleed;
  - it falls by --drop % (default 30) within --drop-window s (default 10): the dump;
  - its price reaches one of --levels (default 2,3,5,10) times its price when you added it (~ your entry), or falls
    to one of --drop-levels (default 0.5,0.25) times that price (-50 %, -75 %).
A rise (fall) alert is sent again 30 s later at the earliest, unless the price has risen (fallen) by --spike %
(--drop %) again meanwhile; each level once.

  price_watch.py [<CA> ...] [--watch 30] [--spike 40] [--spike-window 10] [--drop 30] [--drop-window 10]
                 [--levels 2,3,5,10] [--drop-levels 0.5,0.25] [--rpc-http URL] [--rpc-ws URL]

Only the coins you hand it are watched: the CAs on the command line and those pasted in its window (Enter after each;
a CA already watched gets --watch more minutes); "retire <CA>" stops watching one, "suivis" lists them.
wallet_follow.py imports it so that the CAs typed in its window are watched the same way: download this file next to
it. Same options there.
Price: a coin still on its pump.fun bonding curve is read live from the curve account (accountSubscribe over the
WebSocket, `pip install websockets`, every trade within ~1 s; the account is also read over HTTP as a backstop, every
2 s while the WebSocket is down); the curve is priced in SOL or in the coin written in it (PUMP on some curves),
converted to USD at Jupiter's price. Any other coin, or one that leaves the curve, from Jupiter's price API every 3 s.
Market cap: price x total supply, like GMGN. Without TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID the alerts are only printed.
Output (alone): data/price_watch_<timestamp>.jsonl, one line per alert and per end of watch.
"""
import asyncio
import base64
import collections
import hashlib
import html
import json
import os
import re
import ssl
import struct
import sys
import threading
import time
import urllib.request

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
RPC = arg("--rpc-http", "https://api.mainnet-beta.solana.com")
WS = arg("--rpc-ws", RPC.replace("https://", "wss://").replace("http://", "ws://"))
WATCH_MIN = float(arg("--watch", "30"))                  # 0: off
SPIKE = float(arg("--spike", "40")) / 100                # 0: off
WINDOW = float(arg("--spike-window", "10"))              # s
LEVELS = sorted(float(x) for x in arg("--levels", "2,3,5,10").split(",") if x.strip())
DROP = float(arg("--drop", "30")) / 100                  # 0: off
DROP_WINDOW = float(arg("--drop-window", "10"))          # s
DROP_LEVELS = sorted((float(x) for x in arg("--drop-levels", "0.5,0.25").split(",") if x.strip()), reverse=True)
COOLDOWN = 30                                            # s between two rise (fall) alerts, unless still rising (falling)
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"     # pump.fun's bonding-curve program
CURVE_TAG = hashlib.sha256(b"account:BondingCurve").digest()[:8]
SOL = "So11111111111111111111111111111111111111112"
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
ADDRESS = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")
CA = "/root/.ccr/ca-bundle.crt"
SSL = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()
quotes = {SOL: {"dec": 9, "usd": None, "t": 0}}   # a curve's quote coin (SOL, PUMP...): decimals, USD price


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


def now_str():
    return time.strftime("%H:%M:%S")


def say(s):
    """print, even for a coin name the console cannot show."""
    try:
        print(s, flush=True)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(s.encode(enc, "replace").decode(enc), flush=True)


# ---------- addresses: base58 and the bonding curve's program-derived address

def b58decode(s):
    n = 0
    for c in s:
        n = n * 58 + B58.index(c)
    return b"\0" * (len(s) - len(s.lstrip("1"))) + (n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b"")


def b58encode(b):
    n, out = int.from_bytes(b, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


def is_address(s):
    try:
        return bool(ADDRESS.fullmatch(s)) and len(b58decode(s)) == 32
    except ValueError:
        return False


_P = 2 ** 255 - 19
_D = -121665 * pow(121666, _P - 2, _P) % _P


def on_curve(b):
    """Whether 32 bytes are an ed25519 point (a program-derived address must not be)."""
    y = int.from_bytes(b, "little") & ((1 << 255) - 1)
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    return x2 == 0 or pow(x2, (_P - 1) // 2, _P) == 1


def bonding_curve(mint):
    seeds, prog = b"bonding-curve" + b58decode(mint), b58decode(PUMP)
    for bump in range(255, -1, -1):
        h = hashlib.sha256(seeds + bytes([bump]) + prog + b"ProgramDerivedAddress").digest()
        if not on_curve(h):
            return b58encode(h)


def decode_curve(value):
    """(price in quote coins per token, total supply in tokens, complete, quote coin) from a bonding-curve account,
    else None. The quote coin is SOL, or the mint written at byte 83 (PUMP on some curves); the price is None while
    that coin's decimals are unknown (quote_usd learns them)."""
    if not value or value.get("owner") != PUMP:
        return None
    d = base64.b64decode(value["data"][0])
    if len(d) < 49 or d[:8] != CURVE_TAG:
        return None
    vtok, vquote, _, _, supply = struct.unpack_from("<5Q", d, 8)
    quote = b58encode(d[83:115]) if len(d) >= 115 and any(d[83:115]) else SOL
    dec = quotes.get(quote, {}).get("dec")
    return (vquote / 10 ** dec / (vtok / 1e6) if vtok and dec else None), supply / 1e6, bool(d[48]), quote


# ---------- coin data from Jupiter

def token_info(mint):
    try:
        return next((t for t in http(f"https://lite-api.jup.ag/tokens/v2/search?query={mint}") if t.get("id") == mint), {})
    except Exception:
        return None


def coin_name(info):
    return f"{info.get('name') or ''} (${info['symbol']})".strip()


def jup_prices(mints):
    out = {}
    for i in range(0, len(mints), 50):
        try:
            r = http(f"https://lite-api.jup.ag/price/v3?ids={','.join(mints[i:i + 50])}")
            out.update({m: v["usdPrice"] for m, v in r.items() if v and v.get("usdPrice")})
        except Exception:
            pass
    return out


def quote_usd(mint):
    """USD price of a curve's quote coin, from Jupiter at most once a minute; also learns its decimals."""
    q = quotes.setdefault(mint, {"dec": None, "usd": None, "t": 0})
    if time.time() - q["t"] > 60:
        q["t"] = time.time()
        try:
            r = http(f"https://lite-api.jup.ag/price/v3?ids={mint}").get(mint) or {}
            q["usd"], q["dec"] = r.get("usdPrice") or q["usd"], q["dec"] or r.get("decimals")
        except Exception:
            pass
    return q["usd"]


def fmt_usd(v):
    return "?" if v is None else f"${v / 1e6:.2f}M" if v >= 1e6 else f"${v / 1e3:.1f}k" if v >= 1e3 else f"${v:.0f}"


def fmt_dur(s):
    s = int(s)
    return f"{s} s" if s < 60 else f"{s // 60} min {s % 60:02d} s" if s < 3600 else f"{s // 3600} h {s // 60 % 60:02d}"


def telegram_send(text):
    try:
        r = http(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
                 {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True})
        if not r.get("ok"):
            print(f"  alerte Telegram non envoyée : {r.get('description')}", flush=True)
    except Exception as e:
        print(f"  alerte Telegram non envoyée : {type(e).__name__} {e}", flush=True)


def describe():
    moves = ([f"+{SPIKE * 100:g} % en {WINDOW:g} s"] if SPIKE else []) + \
            ([f"-{DROP * 100:g} % en {DROP_WINDOW:g} s"] if DROP else [])
    levels = [f"x{x:g}" for x in LEVELS] + [f"-{(1 - x) * 100:g} %" for x in DROP_LEVELS]
    return ", ".join(moves + ([f"{'/'.join(levels)} depuis l'ajout"] if levels else [])) or "aucune règle"


def sudden(h, t, w, pct, up):
    """For the last price of h (time-sorted (t, price)): (change, s it took, the low it rose from / the high it fell
    from) when it is pct above the low (up) or below the high of the last w s, else None. The time it took counts
    from when the price left that low's (high's) neighbourhood: 0 when it moved in one trade."""
    s = 0
    while s + 1 < len(h) and h[s + 1][0] <= t - w:   # the price in force at the start of the window
        s += 1
    pts = [h[k] for k in range(s, len(h))]
    sign = 1 if up else -1
    i = min(range(len(pts)), key=lambda j: (sign * pts[j][1], -j))   # the low (high), its latest occurrence
    ref, p = pts[i][1], pts[-1][1]
    if i == len(pts) - 1 or not (p >= ref * (1 + pct) if up else p <= ref * (1 - pct)):
        return None
    near = ref * (1 + sign * pct / 2)
    j = max(k for k in range(len(pts) - 1) if sign * pts[k][1] <= sign * near)
    return p / ref - 1, t - pts[j + 1][0], ref


def cooled(c, key, t, p, pct, up):
    """Whether a rise (fall) alert may go out: none in the last COOLDOWN s, or the price has moved by pct again since."""
    last = c[key]
    if last and t - last[0] < COOLDOWN and not (p >= last[1] * (1 + pct) if up else p <= last[1] * (1 - pct)):
        return False
    c[key] = (t, p)
    return True


def took(s):
    return "d'un coup" if s < 1 else f"en {s:.0f} s"


# ---------- the watcher

class Watcher:
    def __init__(self, telegram=True, log=None, minutes=WATCH_MIN):
        self.telegram, self.log, self.minutes = telegram, log or (lambda rec: None), minutes
        self.coins = {}               # mint -> state
        self.lock = threading.Lock()
        self.loop = self.ws = None
        self.ws_state = "pas encore connecté"
        self.rid, self.reqs, self.subs, self.sub_of = 0, {}, {}, {}   # WebSocket requests and subscriptions

    def start(self):
        threading.Thread(target=self._ws_thread, daemon=True).start()
        threading.Thread(target=self._poll_thread, daemon=True).start()
        return self

    def add(self, mint):
        """Watch mint for self.minutes from now; False if it was watched already (it then gets the time again)."""
        with self.lock:
            c = self.coins.get(mint)
            if c:
                c["until"] = time.time() + self.minutes * 60
                return False
            self.coins[mint] = c = {"mint": mint, "name": None, "src": None, "until": time.time() + self.minutes * 60,
                                    "p0": None, "hist": collections.deque(), "level": 0, "dlevel": 1, "spike": None,
                                    "drop": None, "slot": 0}
        threading.Thread(target=self._init, args=(c,), daemon=True).start()
        return True

    def remove(self, mint):
        """Stop watching mint; False if it was not watched."""
        with self.lock:
            c = self.coins.pop(mint, None)
        if c:
            self._end(c)
        return bool(c)

    def listing(self):
        with self.lock:
            coins = list(self.coins.values())
        if not coins:
            return ["aucun coin suivi"]
        out = []
        for c in coins:
            if not c["p0"]:
                out.append(f"  {self._name(c)} : pas encore de prix ({c['mint']})")
                continue
            p = c["hist"][-1][1]
            out.append(f"  {self._name(c)} : MC {fmt_usd(self._mc(c, p))} (x{p / c['p0']:.2f} depuis l'ajout, plus "
                       f"haut x{c['hi'] / c['p0']:.2f}, plus bas x{c['lo'] / c['p0']:.2f}), encore "
                       f"{fmt_dur(max(0, c['until'] - time.time()))} ({c['mint']})")
        return out

    # --- start of a watch: on the bonding curve or not

    def _init(self, c):
        mint, value, slot = c["mint"], None, 0
        curve = bonding_curve(mint)
        for _ in range(3):
            try:
                r = rpc("getMultipleAccounts", [[curve], {"encoding": "base64", "commitment": "processed"}])
                value, slot = r["value"][0], r["context"]["slot"]
                break
            except Exception:
                time.sleep(1)
        cv = decode_curve(value)
        if cv and not cv[0] and not cv[2]:   # quoted in a coin whose decimals are not known yet
            quote_usd(cv[3])
            cv = decode_curve(value)
        if cv and cv[0] and not cv[2]:
            c.update(src="curve", curve=curve, supply=cv[1], unit=cv[3])
            self._on_price(c, cv[0], time.time(), slot)
            self._call(self._sub, mint)
        info = token_info(mint) or {}
        if not c["name"] and info.get("symbol"):
            c["name"] = coin_name(info)
        if c["src"] is None:   # not (or no longer) on a pump.fun curve: Jupiter's price, polled
            supply = info.get("totalSupply") or (info["mcap"] / info["usdPrice"] if info.get("mcap") and
                                                 info.get("usdPrice") else None)
            c.update(src="jup", supply=supply, unit="usd")

    def _name(self, c):
        return c["name"] or c["mint"][:6] + "…"

    def _mc(self, c, p):
        usd = p if c["unit"] == "usd" else p * (quote_usd(c["unit"]) or 0)
        return usd * c["supply"] if usd and c.get("supply") else None

    # --- prices

    def _curve_update(self, c, value, slot):
        cv = decode_curve(value)
        if cv is None or slot < c["slot"]:
            return
        if cv[2]:   # the curve is complete: the coin moves to PumpSwap, follow it through Jupiter from now on
            s = quote_usd(c["unit"])
            if not s:
                return
            with self.lock:
                if c["src"] != "curve":
                    return
                for k in ("p0", "hi", "lo"):
                    if c.get(k):
                        c[k] *= s
                c["hist"] = collections.deque((t, p * s) for t, p in c["hist"])
                for k in ("spike", "drop"):
                    if c[k]:
                        c[k] = (c[k][0], c[k][1] * s)
                c.update(src="jup", unit="usd")
            self._call(self._unsub, c["mint"])
            return say(f"\n{now_str()} {self._name(c)} quitte la courbe pump.fun : suivi par Jupiter")
        if cv[0]:
            self._on_price(c, cv[0], time.time(), slot)

    def _on_price(self, c, p, t, slot=0):
        alert = None
        with self.lock:
            if slot:
                if slot < c["slot"]:
                    return
                c["slot"] = slot
            if not c["p0"]:
                c.update(p0=p, t0=t, hi=p, hi_t=t, lo=p)
            h = c["hist"]
            h.append((t, p))
            while len(h) > 1 and h[1][0] <= t - max(WINDOW, DROP_WINDOW):   # keep the price in force at its start
                h.popleft()
            if p > c["hi"]:
                c["hi"], c["hi_t"] = p, t
            c["lo"] = min(c["lo"], p)
            ev = {"rise": SPIKE and sudden(h, t, WINDOW, SPIKE, True), "drop": DROP and sudden(h, t, DROP_WINDOW, DROP,
                                                                                               False)}
            for k, key, pct, up in (("rise", "spike", SPIKE, True), ("drop", "drop", DROP, False)):
                if ev[k] and not cooled(c, key, t, p, pct, up):
                    ev[k] = None
            level = max((x for x in LEVELS if p >= c["p0"] * x), default=0)
            ev["level"] = level if level > c["level"] else 0
            c["level"] = max(c["level"], level)
            dlevel = min((x for x in DROP_LEVELS if p <= c["p0"] * x), default=1)
            ev["dlevel"] = dlevel if dlevel < c["dlevel"] else 0
            c["dlevel"] = min(c["dlevel"], dlevel)
            if any(ev.values()):
                alert = (dict(c), p, t, ev)
        if alert:
            threading.Thread(target=self._alert, args=alert, daemon=True).start()

    # --- alerts

    def _alert(self, c, p, t, ev):
        if not c["name"]:   # Jupiter may list a brand new coin only after a while
            info = token_info(c["mint"]) or {}
            if info.get("symbol"):
                c["name"] = coin_name(info)
                if c["mint"] in self.coins:
                    self.coins[c["mint"]]["name"] = c["name"]
        name, mint, rise, drop = self._name(c), c["mint"], ev["rise"], ev["drop"]
        down = bool(drop or ev["dlevel"])
        parts = ([f"x{ev['level']:g} depuis l'ajout"] if ev["level"] else []) + \
                ([f"+{rise[0] * 100:.0f} % {took(rise[1])}"] if rise else []) + \
                ([f"-{(1 - ev['dlevel']) * 100:.0f} % depuis l'ajout"] if ev["dlevel"] else []) + \
                ([f"{drop[0] * 100:.0f} % {took(drop[1])}"] if drop else [])
        mc, mc0 = self._mc(c, p), self._mc(c, c["p0"])
        say(f"\n{now_str()} {'CHUTE' if down else 'HAUSSE'} {name} : {' · '.join(parts)} | MC {fmt_usd(mc)} "
            f"({fmt_usd(mc0)} à l'ajout il y a {fmt_dur(t - c['t0'])}, x{p / c['p0']:.2f}) | {mint}")
        self.log({"type": "price_alert", "mint": mint, "name": c["name"], "level": ev["level"] or None,
                  "rise": rise and round(rise[0], 4), "rise_s": rise and round(rise[1], 1),
                  "drop_level": ev["dlevel"] or None, "drop": drop and round(drop[0], 4),
                  "drop_s": drop and round(drop[1], 1), "x_since_added": round(p / c["p0"], 4), "mcap": mc,
                  "mcap_added": mc0, "watched_s": round(t - c["t0"], 1), "source": c["src"], "t": t})
        if not self.telegram:
            return
        icons = "".join(i for i, on in (("🎯", ev["level"]), ("🚀", rise), ("📉", ev["dlevel"]), ("🔻", drop)) if on)
        lines = [f"{icons} <b>{html.escape(name)}</b> : " + " · ".join(f"<b>{html.escape(x)}</b>" for x in parts),
                 f"{'📉' if down else '📈'} MC <b>{fmt_usd(mc)}</b> (x{p / c['p0']:.2f}) · {fmt_usd(mc0)} à l'ajout il y "
                 f"a {fmt_dur(t - c['t0'])}" + (f" · creux {fmt_usd(self._mc(c, rise[2]))} juste avant" if rise else "")
                 + (f" · sommet {fmt_usd(self._mc(c, drop[2]))} juste avant" if drop else ""),
                 f"📄 CA : <code>{html.escape(mint)}</code>",
                 f'<a href="https://gmgn.ai/sol/token/{mint}">GMGN</a> · <a href="https://pump.fun/coin/{mint}">pump.fun'
                 f'</a> · <a href="https://dexscreener.com/solana/{mint}">DexScreener</a>']
        telegram_send("\n".join(lines))

    def _end(self, c):
        self._call(self._unsub, c["mint"])
        if not c["p0"]:
            return say(f"{now_str()} fin du suivi de {self._name(c)} : aucun prix lu")
        last = c["hist"][-1][1]
        say(f"{now_str()} fin du suivi de {self._name(c)} : {fmt_usd(self._mc(c, c['p0']))} à l'ajout, plus haut "
            f"{fmt_usd(self._mc(c, c['hi']))} (x{c['hi'] / c['p0']:.2f}) {fmt_dur(c['hi_t'] - c['t0'])} après, "
            f"plus bas x{c['lo'] / c['p0']:.2f}, fin {fmt_usd(self._mc(c, last))} (x{last / c['p0']:.2f})")
        self.log({"type": "watch_end", "mint": c["mint"], "name": c["name"], "x_high": round(
            c["hi"] / c["p0"], 4), "high_after_s": round(c["hi_t"] - c["t0"], 1), "x_end": round(last / c["p0"], 4),
                  "x_low": round(c["lo"] / c["p0"], 4)})

    # --- WebSocket: one accountSubscribe per bonding curve

    def _call(self, fn, mint):
        if self.loop:
            asyncio.run_coroutine_threadsafe(fn(mint), self.loop)

    async def _sub(self, mint):
        c = self.coins.get(mint)
        if not self.ws or not c or c["src"] != "curve" or mint in self.sub_of or mint in self.reqs.values():
            return
        self.rid += 1
        self.reqs[self.rid] = mint
        await self.ws.send(json.dumps({"jsonrpc": "2.0", "id": self.rid, "method": "accountSubscribe",
                                       "params": [c["curve"], {"encoding": "base64", "commitment": "processed"}]}))

    async def _unsub(self, mint):
        sid = self.sub_of.pop(mint, None)
        if sid is not None:
            self.subs.pop(sid, None)
            if self.ws:
                await self.ws.send(json.dumps({"jsonrpc": "2.0", "id": 0, "method": "accountUnsubscribe",
                                               "params": [sid]}))

    def _ws_thread(self):
        try:
            import websockets
        except ImportError:
            self.ws_state = "absent (pip install websockets)"
            return

        async def run():
            self.loop, fails = asyncio.get_running_loop(), 0
            while True:
                try:
                    async with websockets.connect(WS, ssl=SSL if WS.startswith("wss") else None, max_size=None,
                                                  ping_interval=20, ping_timeout=20) as ws:
                        self.ws, self.reqs, self.subs, self.sub_of = ws, {}, {}, {}
                        for mint in list(self.coins):
                            await self._sub(mint)
                        self.ws_state, fails = "connecté", 0
                        async for msg in ws:
                            m = json.loads(msg)
                            if "id" in m:   # a subscription's answer
                                mint = self.reqs.pop(m["id"], None)
                                if mint and isinstance(m.get("result"), int):
                                    if mint in self.coins:
                                        self.subs[m["result"]], self.sub_of[mint] = mint, m["result"]
                                    else:   # its watch ended meanwhile
                                        await ws.send(json.dumps({"jsonrpc": "2.0", "id": 0, "method":
                                                                  "accountUnsubscribe", "params": [m["result"]]}))
                                continue
                            p = m.get("params") or {}
                            c = self.coins.get(self.subs.get(p.get("subscription")))
                            if c and c["src"] == "curve":
                                self._curve_update(c, p["result"]["value"], p["result"]["context"]["slot"])
                except Exception:
                    fails += 1   # the public endpoint drops connections now and then: reconnect, HTTP covers the gap
                    self.ws_state = "reconnexion"
                self.ws = None
                await asyncio.sleep(min(fails, 10))

        asyncio.run(run())

    # --- HTTP: curve backstop, Jupiter prices, end of the watches

    def _poll_thread(self):
        k = 0
        while True:
            time.sleep(1)
            k += 1
            with self.lock:
                ended = [c for c in self.coins.values() if c["until"] < time.time()]
                for c in ended:
                    del self.coins[c["mint"]]
                curves = [c for c in self.coins.values() if c["src"] == "curve"]
                jups = [c for c in self.coins.values() if c["src"] == "jup"]
            for c in ended:
                try:
                    self._end(c)
                except Exception as e:
                    print(f"  fin du suivi {c['mint'][:8]} : {type(e).__name__} {e}", flush=True)
            if curves and k % (2 if self.ws_state != "connecté" else 15) == 0:
                for i in range(0, len(curves), 100):
                    try:
                        r = rpc("getMultipleAccounts", [[c["curve"] for c in curves[i:i + 100]],
                                                        {"encoding": "base64", "commitment": "processed"}])
                        for c, v in zip(curves[i:i + 100], r["value"]):
                            self._curve_update(c, v, r["context"]["slot"])
                    except Exception:
                        pass
            if jups and k % 3 == 0:
                prices, t = jup_prices([c["mint"] for c in jups]), time.time()
                for c in jups:
                    if c["mint"] in prices:
                        self._on_price(c, prices[c["mint"]], t)


def command(w, line):
    """Answer to a line typed in the window if it is a price-watch command (a CA, retire <CA>, suivis), else None."""
    words = line.split()
    if len(words) == 1 and is_address(words[0]):
        return (f"suivi de {words[0]} pendant {w.minutes:g} min" if w.add(words[0]) else
                f"{words[0]} déjà suivi : encore {w.minutes:g} min")
    if len(words) == 2 and words[0].lower() in ("retire", "retirer") and is_address(words[1]):
        return f"{words[1]} n'est plus suivi" if w.remove(words[1]) else f"{words[1]} n'était pas suivi"
    if line.lower() in ("suivis", "liste", "list"):
        return "\n".join(w.listing())
    return None


def main():
    os.makedirs(DATA, exist_ok=True)
    path = os.path.normpath(os.path.join(DATA, f"price_watch_{int(time.time())}.jsonl"))
    lock = threading.Lock()

    def log(rec):
        with lock, open(path, "a") as f:
            f.write(json.dumps(rec) + "\n")

    tg = bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))
    if not WATCH_MIN:
        sys.exit("--watch 0 : rien à surveiller")
    w = Watcher(telegram=tg, log=log).start()
    print(f"alerte si un coin suivi fait {describe()} ; chaque CA suivi {WATCH_MIN:g} min | "
          f"{'alertes Telegram actives' if tg else 'alertes affichées ici seulement (TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID pour Telegram)'}"
          f" | journal {path}", flush=True)
    for ca in sys.argv[1:]:
        if is_address(ca):
            w.add(ca)
            print(f"suivi de {ca}", flush=True)
    print("colle un CA puis Entrée pour le surveiller, « retire <CA> » pour l'arrêter, « suivis » pour la liste",
          flush=True)
    for line in sys.stdin:
        answer = command(w, line.strip())
        if answer:
            print(answer, flush=True)
        elif line.strip():
            print("colle un CA (adresse du coin), « retire <CA> » ou « suivis »", flush=True)
    while True:   # no console (stdin closed): keep watching
        time.sleep(3600)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\narrêt")
