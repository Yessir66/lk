#!/usr/bin/env python3
"""
Copy bot for wallet 7BNaxx6K: sends the token address (CA) to the user's Trojan bot on Telegram, which buys it
with its Autobuy setting (5 $). Rules and setup: analyses/trojan_autobuy_guide.md.

1. Detection: polls the wallet's signatures on public Solana RPCs (1-2 s after its trade), whatever it pays with
   (SOL, USDC, PUMP, CARDS, TTWO...). PumpPortal's account feed does not report this wallet's trades.
2. A signal needs ALL of:
   - its FIRST buy of the token: the last 2 h of its history are read at startup (a token created <= 2 h before
     the buy cannot have been bought earlier than that) and a token it already held is ignored;
   - token created <= 2 h before that buy (Jupiter token API: first pool time, checked equal to the backtest's
     pump.fun criterion on 743 tokens) and launched on pump.fun or stonkfun (the backtested universe);
   - market cap at its price >= 45 000 $ (its fill in USD x 1e9 supply, as in the backtest);
   - its price not more than 50 % below the token's high so far (pump.fun minute candles; skipped if unavailable);
   - the signal is less than 15 s old, the CA was never sent before, and fewer than 40 signals today.
3. --live: sends the CA to Trojan with Telethon (the user's own Telegram account; api_id/api_hash and the
   session file stay on the user's PC, trojan_config.json and *.session are gitignored). Without --live: the
   same detection, journal only (dry run).
4. Exits: the take profits (half at x1.5, the rest at x2) and the -50 % stop are set in Trojan. The bot follows
   the price (Jupiter, every 2 s) and for the exits Trojan cannot do on its own sells everything:
   - -30 % within the first minute after the wallet's buy;
   - x1.8 reached: 5 s later;
   - back to the entry price once x1.5 was reached;
   - 5 min after the wallet's buy, price between -20 % and +20 % of the entry;
   - 20 min after the wallet's buy.
   (The first three user rules took the 19-21/10 backtest from -5.4 % to about 0 % a trade, every day better.)
   For each it sends an alert to the user's Telegram "Saved Messages", or sells through Trojan's buttons when "vente_auto" is set to true in the config: "Sell"
   under its message for the token -> sell panel, "Swap" and "100 %" selected -> "SELL". It also journals what the
   rule would have given (paper, backtest costs: 2.25 % fee a side, 2 % slippage on sales) to compare with the
   real trades. Trojan's replies are journaled (text and buttons).
Journal: data/trojan/journal.jsonl ; summary data/trojan/resume.json ; CAs sent data/trojan/envoyes.json.

Usage: trojan_autobuy.py [--live] [--config trojan_config.json] [--test-telegram] [--test-vente <CA>]
  --test-vente sells 100 % of that token through Trojan's buttons (to check the sell flow on a real position).
"""
import argparse
import asyncio
import calendar
import collections
import concurrent.futures
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from copy_fetch import NOT_MEME, WSOL, parse_tx  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(ROOT, "data", "trojan")
H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Content-Type": "application/json"}
FEE, SLIP = 0.0225, 0.02
# tokens it pays with (>= 5 different counterparts in its 18-22/10 history), never signals
QUOTES = NOT_MEME | {
    "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn", "CARDSccUMFKoPRZxt5vt3ksUbxEFEcnZ3H2pd3dKxYjp",
    "SPCXxcqXj6e5dJDVNovHN8744zkbhM2bYudU45BimGb", "TTWofwAge91oFhZs7kpQdyrVRkmevgM88xijGvQFbKo",
    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh", "A7bdiYdS5GjqGFtxf17ppRHtDKPkkRqbKtR27dxvQXaS",
    "DJTu7vi8norVzdVAffgvb39VP7wjKeTsgaMBJrzfxvoF", "ysPp2Bt2j2UQu2tZwtSJz7JxyrmQ8GJcNJE5NyDoS5a",
    "6s2umN1sqM8mS8ERRmt9cqcFwAoWu5ofma7DN3KfFiue", "Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh",
    "WXMRyRZhsa19ety5erZhHg4N3xj3EVN92u94422teJp", "XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB",
    "6GmAFSYs4gk3FDao5FzzySQpPZaWsa4rUJHacpMpUNgx", "XspzcW1PRtgf6Wj92HCiZdjzKCyFekVD8P5Ueh3dRMX",
    "PEPEqnuuCDbBC89p1u9vpnP1KQ2oj1xTcQBsjt9X55m", "HsRpHQn6VbyMs5b5j5SV6xQ2VvpvvCCzu19GjytVSCoz", WSOL}
DEFAULTS = {
    "api_id": 0, "api_hash": "", "trojan_bot": "solana_trojanbot",
    "wallet": "7BNaxx6KdUYrjACNQZ9He26NBFoFxujQMAfNLnArLGH5",
    "mise_usd": 5, "mc_min_usd": 45000, "age_max_min": 120, "baisse_max_sous_ath": 0.5,
    "launchpads": ["pump.fun", "stonkfun"], "max_signaux_par_jour": 40, "retard_max_s": 15,
    "tp1": 1.5, "tp2": 2.0, "stop": 0.5, "sortie_min": 20,
    "stop_1re_minute": 0.3, "vente_totale_a": 1.8, "delai_vente_totale_s": 5, "plage_5min": 0.2,
    "vente_auto": False,
    "alertes_achat": True, "frais_fixes_usd_par_tx": 0.2,
    "rpc": [], "rpc_publics": ["https://solana-rpc.publicnode.com", "https://api.mainnet-beta.solana.com"],
}


def now_s():
    return time.strftime("%d/%m %H:%M:%S", time.localtime())


def log(ev, **kw):
    os.makedirs(OUT, exist_ok=True)
    rec = dict(t=round(time.time(), 2), when=now_s(), ev=ev, **kw)
    with open(os.path.join(OUT, "journal.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(rec["when"], ev, {k: v for k, v in kw.items() if k not in ("boutons",)}, flush=True)


def http(url, body=None, timeout=10):
    req = urllib.request.Request(url, json.dumps(body).encode() if body else None, H)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


class Rpc:
    """round robin over endpoints; one that fails or answers 429 rests 10 s"""
    def __init__(self, urls):
        self.urls, self.rest, self.k = urls, {}, 0

    def call(self, method, params, tries=6):
        for _ in range(tries):
            live = [u for u in self.urls if self.rest.get(u, 0) <= time.time()] or self.urls
            u = live[self.k % len(live)]
            self.k += 1
            try:
                r = http(u, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
                if "result" in r:
                    return r["result"]
            except Exception:
                pass
            self.rest[u] = time.time() + 10
            time.sleep(0.2)
        return None


def jup_tokens(mints):
    """Jupiter token API: {mint: info (usdPrice, mcap, launchpad, firstPool.createdAt, symbol...)}"""
    for k in range(3):
        try:
            return {x["id"]: x for x in http("https://lite-api.jup.ag/tokens/v2/search?query=" + ",".join(mints))}
        except Exception:
            time.sleep(0.5 + k)
    return {}


def jup_prices(mints):
    try:
        d = http("https://lite-api.jup.ag/price/v3?ids=" + ",".join(mints))
        return {m: float(v["usdPrice"]) for m, v in d.items() if v and v.get("usdPrice")}
    except Exception:
        return {}


def pump_high(mint):
    """highest price so far (USD) from pump.fun minute candles, None if unavailable"""
    try:
        c = http(f"https://swap-api.pump.fun/v1/coins/{mint}/candles?interval=1m&limit=1000", timeout=4)
        c = c if isinstance(c, list) else c.get("candles", [])
        return max(float(x["high"]) for x in c) if c else None
    except Exception:
        return None


def iso(s):
    return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))


class Position:
    """paper follow-up of one signal with the rule's exits; real orders are Trojan's"""
    def __init__(self, bot, c, info):
        self.bot, self.c, self.mint, self.sym = bot, c, c["mint"], info.get("symbol", "?")
        self.entry = None
        self.left, self.cash, self.ntx, self.tp1, self.state, self.high = 1.0, 0.0, 1, False, "open", 0.0
        self.t_full, self.check5_done = None, False

    def sell(self, frac, x, why):
        frac = min(frac, self.left)
        self.cash += frac / (1 + FEE) * x * (1 - SLIP) * (1 - FEE)
        self.left -= frac
        self.ntx += 1
        log("sortie_simulee", mint=self.mint, symbole=self.sym, part=round(frac, 2), x=round(x, 3), raison=why,
            min_depuis_son_achat=round((time.time() - self.c["bt"]) / 60, 1))
        if self.left <= 1e-9:
            self.state = "closed"
            cfg = self.bot.cfg
            self.pnl_pct = self.cash - 1
            self.pnl_usd = cfg["mise_usd"] * self.pnl_pct - cfg["frais_fixes_usd_par_tx"] * self.ntx
            log("cloture_simulee", mint=self.mint, symbole=self.sym, pnl_pct=round(100 * self.pnl_pct, 1),
                pnl_usd=round(self.pnl_usd, 2), plus_haut=round(self.high, 2))
            self.bot.write_summary()

    async def step(self, px):
        cfg = self.bot.cfg
        if self.entry is None:
            self.entry = px * 1.01                                   # first price seen after the signal, +1 %
            log("entree_simulee", mint=self.mint, symbole=self.sym, prix=px,
                retard_s=round(time.time() - self.c["bt"], 1))
            return
        x = px / self.entry
        self.high = max(self.high, x)
        el = time.time() - self.c["bt"]
        if x <= 1 - cfg["stop"]:
            return self.sell(1.0, x, f"stop -{cfg['stop']:.0%} (Trojan)")
        if cfg["stop_1re_minute"] and el <= 60 and x <= 1 - cfg["stop_1re_minute"]:
            return self.bot_exit(x, f"-{cfg['stop_1re_minute']:.0%} dans la première minute")
        if not self.tp1 and x >= cfg["tp1"]:
            self.tp1 = True
            self.sell(0.5, cfg["tp1"], f"x{cfg['tp1']:g} : moitié (Trojan)")
            await self.bot.notify(f"{self.sym} x{cfg['tp1']:g} atteint : Trojan a dû vendre la moitié. Le reste se vend "
                                  f"à x{cfg['tp2']:g}, 5 s après x{cfg['vente_totale_a']:g}, ou au prix d'entrée s'il y retombe.\n{self.mint}")
        if self.tp1 and x >= cfg["tp2"]:
            return self.sell(1.0, cfg["tp2"], f"x{cfg['tp2']:g} : le reste (Trojan)")
        if cfg["vente_totale_a"] and self.t_full is None and x >= cfg["vente_totale_a"]:
            self.t_full = time.time()
        if self.t_full and time.time() >= self.t_full + cfg["delai_vente_totale_s"]:
            return self.bot_exit(x, f"x{cfg['vente_totale_a']:g} atteint il y a {cfg['delai_vente_totale_s']} s")
        if self.tp1 and x <= 1.0:
            return self.bot_exit(x, "retombé au prix d'entrée après x1,5")
        if cfg["plage_5min"] and not self.check5_done and el >= 300:
            self.check5_done = True
            if abs(x - 1) < cfg["plage_5min"]:
                return self.bot_exit(x, f"5 min après son achat, toujours entre -{cfg['plage_5min']:.0%} et "
                                        f"+{cfg['plage_5min']:.0%} ({x - 1:+.0%})")
        if el >= 60 * cfg["sortie_min"]:
            return self.bot_exit(x, f"{cfg['sortie_min']} min après son achat sans x{cfg['tp2']:g}")

    def bot_exit(self, x, why):
        """an exit Trojan does not do on its own: sold by the bot in the background (the price loop goes on)"""
        asyncio.create_task(self.bot.exit_now(self, why))
        self.sell(1.0, x, why + " (bot)")


class Bot:
    def __init__(self, cfg, live):
        self.cfg, self.live, self.W = cfg, live, cfg["wallet"]
        self.rpc = Rpc(cfg["rpc"] + cfg["rpc_publics"])
        self.seen_sigs, self.seen_mints, self.pos = set(), set(), {}
        self.stats = collections.Counter()
        self.tg, self.sell_lock = None, asyncio.Lock()
        f = os.path.join(OUT, "envoyes.json")
        self.sent = json.load(open(f)) if os.path.exists(f) else {}          # CA -> time sent

    # ---- Telegram ----
    async def connect(self):
        from telethon import TelegramClient, events
        cfg = self.cfg
        if not cfg["api_id"] or not cfg["api_hash"]:
            sys.exit("api_id / api_hash manquants dans trojan_config.json (voir analyses/trojan_autobuy_guide.md)")
        self.tg = TelegramClient(os.path.join(ROOT, "trojan_bot"), int(cfg["api_id"]), cfg["api_hash"])
        await self.tg.start()                          # first time: asks the phone number and the Telegram code
        self.trojan = await self.tg.get_entity(cfg["trojan_bot"])

        @self.tg.on(events.NewMessage(chats=self.trojan))
        async def on_trojan(ev):
            log("trojan", texte=(ev.raw_text or "")[:700], boutons=[[b.text for b in row] for row in (ev.buttons or [])])

    async def notify(self, text):
        if self.tg:
            try:
                await self.tg.send_message("me", text)
            except Exception as e:
                log("erreur_notification", erreur=str(e))

    async def exit_now(self, p, why):
        """an exit Trojan does not do on its own: sell through its buttons, or alert the user"""
        if not self.tg:
            return
        if self.cfg["vente_auto"]:
            try:
                err, done = await self.sell_all(p.mint)
            except Exception as e:
                err, done = repr(e), ""
            log("vente_trojan", mint=p.mint, raison=why, erreur=err, reponse=done[:300])
            if not err:
                return await self.notify(f"VENDU (bot) {p.sym} : {why}\n{done[:200]}\n{p.mint}")
            why += f" (vente auto impossible : {err})"
        await self.notify(f"VENDS TOUT {p.sym} maintenant : {why}\n{p.mint}")

    def find(self, msgs, mint, pat):
        """newest Trojan message showing the token's CA with a button matching pat -> (message, button)"""
        for m in msgs:
            if m.out or mint not in (m.raw_text or ""):
                continue
            for b in (b for row in (m.buttons or []) for b in row):
                if re.search(pat, b.text or ""):
                    return m, b
        return None, None

    async def wait_for(self, mint, pat, cond=lambda b: True, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            await asyncio.sleep(0.8)
            m, b = self.find(await self.tg.get_messages(self.trojan, limit=60), mint, pat)
            if b and cond(b):
                return m, b
        return None, None

    async def sell_all(self, mint):
        """Trojan's sell flow: "Sell" under its message for the token -> sell panel; "Swap" and "100 %" selected
        (a check mark) -> "SELL". Only buttons of a message showing this CA are clicked. -> (error or None, reply)"""
        async with self.sell_lock:
            m, b = self.find(await self.tg.get_messages(self.trojan, limit=60), mint, r"^(Sell|SELL)$")
            if not b:
                return "aucun message Trojan avec ce CA et un bouton Sell", ""
            if b.text == "Sell":
                await b.click()
                m, b = await self.wait_for(mint, r"^SELL$")
                if not b:
                    return "le panneau de vente ne s'est pas ouvert", ""
            for pat in (r"Swap", r"(^|\s)100\s*%"):
                _, b = self.find([m], mint, pat)
                if b and "✅" not in b.text:
                    await b.click()
                    m, b = await self.wait_for(mint, pat, lambda x: "✅" in x.text)
                    if not b:
                        return f"impossible de sélectionner « {pat} »", ""
            _, b = self.find([m], mint, r"^SELL$")
            if not b:
                return "bouton SELL introuvable", ""
            t0 = time.time()
            await b.click()
            while time.time() < t0 + 20:                 # Trojan's answer: a new message or the panel edited
                await asyncio.sleep(1)
                for r in await self.tg.get_messages(self.trojan, limit=5):
                    txt = r.raw_text or ""
                    if mint in txt and re.search(r"(?i)sell success|fail|error|insufficient", txt):
                        ok = re.search(r"(?i)sell success", txt)
                        return (None if ok else "réponse de Trojan : " + txt[-200:]), txt
            return None, "SELL cliqué, pas de confirmation de Trojan en 20 s : vérifie la position"

    # ---- wallet ----
    def first_buy_candidates(self, sig, tx):
        """its buys in one transaction -> candidates (first buys only); every mint it touches becomes 'seen'"""
        p = parse_tx(self.W, tx)
        if not p or not p["toks"]:
            return []
        held = {b["mint"] for b in tx["meta"].get("preTokenBalances") or []
                if b.get("owner") == self.W and float(b["uiTokenAmount"]["uiAmount"] or 0) > 0}
        paid = {m: -q for m, q in p["toks"].items() if q < 0}
        sol_paid = -p["sol"] if p["sol"] < -0.001 else 0.0
        got = [m for m, q in p["toks"].items() if q > 0 and m not in QUOTES]
        out = []
        if paid or sol_paid:
            for m in got:
                if m in self.seen_mints or m in held:
                    self.stats["rachats ignorés"] += 1
                else:
                    out.append(dict(mint=m, sig=sig, bt=tx["blockTime"], q=p["toks"][m], paid=paid,
                                    sol_paid=sol_paid, single=len(got) == 1))
        self.seen_mints |= set(p["toks"])
        return out

    def get_tx(self, sig):
        for _ in range(15):
            tx = self.rpc.call("getTransaction", [sig, {"commitment": "confirmed", "encoding": "json",
                                                        "maxSupportedTransactionVersion": 0}])
            if tx:
                return tx
            time.sleep(0.3)
        return None

    def seed(self):
        """tokens it touched in the last 2 h: a later buy of one of them is not a first buy"""
        t0, before, sigs = time.time() - 60 * self.cfg["age_max_min"] - 300, None, []
        while True:
            opt = {"limit": 1000, "commitment": "confirmed", **({"before": before} if before else {})}
            page = self.rpc.call("getSignaturesForAddress", [self.W, opt]) or []
            sigs += [s for s in page if (s.get("blockTime") or 0) >= t0]
            if not page or (page[-1].get("blockTime") or 0) < t0:
                break
            before = page[-1]["signature"]
        self.seen_sigs |= {s["signature"] for s in sigs}
        ok = [s["signature"] for s in sigs if not s.get("err")]
        print(f"{now_s()} lecture de son historique des {self.cfg['age_max_min']} dernières min : {len(ok)} transactions",
              flush=True)
        with concurrent.futures.ThreadPoolExecutor(4) as ex:
            for tx in ex.map(self.get_tx, ok):
                if tx:
                    p = parse_tx(self.W, tx)
                    if p:
                        self.seen_mints |= set(p["toks"])
        print(f"{now_s()} {len(self.seen_mints)} tokens déjà touchés : leurs rachats seront ignorés", flush=True)

    async def watch(self):
        last_beat = time.time()
        while True:
            sigs = await asyncio.to_thread(self.rpc.call, "getSignaturesForAddress",
                                           [self.W, {"limit": 50, "commitment": "confirmed"}])
            for s in reversed(sigs or []):
                if s["signature"] in self.seen_sigs:
                    continue
                self.seen_sigs.add(s["signature"])
                self.stats["transactions vues"] += 1
                if s.get("err"):
                    continue
                tx = await asyncio.to_thread(self.get_tx, s["signature"])
                if not tx:
                    log("transaction_illisible", sig=s["signature"])
                    continue
                try:
                    for c in self.first_buy_candidates(s["signature"], tx):
                        asyncio.create_task(self.evaluate(c))
                except Exception as e:
                    log("erreur_lecture", sig=s["signature"], erreur=repr(e))
            if time.time() - last_beat > 600:
                last_beat = time.time()
                print(f"{now_s()} en écoute : {dict(self.stats)}", flush=True)
            await asyncio.sleep(0.6)

    async def evaluate(self, c):
        try:
            await self.check_and_send(c)
        except Exception as e:
            log("erreur_signal", mint=c["mint"], erreur=repr(e))

    async def check_and_send(self, c):
        cfg, m = self.cfg, c["mint"]
        high_task = asyncio.create_task(asyncio.to_thread(pump_high, m)) if cfg["baisse_max_sous_ath"] else None
        info = await asyncio.to_thread(jup_tokens, [m, WSOL] + [q for q in c["paid"] if q != m])
        if m not in info:
            await asyncio.sleep(1.5)
            info.update(await asyncio.to_thread(jup_tokens, [m]))
        t = info.get(m, {})
        why = []
        created = (t.get("firstPool") or {}).get("createdAt") or t.get("createdAt")
        age = (c["bt"] - iso(created)) / 60 if created else None
        px = {k: v.get("usdPrice") for k, v in info.items()}
        if c["single"] and px.get(WSOL) and all(px.get(q) for q in c["paid"]):
            fill = (c["sol_paid"] * px[WSOL] + sum(a * px[q] for q, a in c["paid"].items())) / c["q"]
            mc = fill * 1e9
        else:
            fill, mc = t.get("usdPrice"), t.get("mcap")
        if not t:
            why.append("token inconnu de Jupiter")
        if t and t.get("launchpad") not in cfg["launchpads"]:
            why.append(f"plateforme {t.get('launchpad')}")
        if age is None or age > cfg["age_max_min"]:
            why.append(f"créé {age:.0f} min avant son achat" if age is not None else "date de création inconnue")
        if not mc or mc < cfg["mc_min_usd"]:
            why.append(f"MC {mc / 1e3:.1f} k$" if mc else "MC inconnue")
        high = await high_task if high_task else None
        if high and fill and fill < (1 - cfg["baisse_max_sous_ath"]) * high:
            why.append(f"{1 - fill / high:.0%} sous son plus haut")
        late = time.time() - c["bt"]
        rec = dict(mint=m, symbole=t.get("symbol"), mc_k=round(mc / 1e3, 1) if mc else None,
                   age_min=round(age, 1) if age is not None else None, retard_s=round(late, 1),
                   sous_ath=round(1 - fill / high, 2) if high and fill else None, sig=c["sig"])
        if why:
            self.stats["rejets"] += 1
            return log("rejet", raisons=why, **rec)
        today = time.strftime("%Y-%m-%d", time.localtime())
        if m in self.sent:
            why.append("CA déjà envoyé")
        if sum(time.strftime("%Y-%m-%d", time.localtime(v)) == today for v in self.sent.values()) >= cfg["max_signaux_par_jour"]:
            why.append(f"limite de {cfg['max_signaux_par_jour']} signaux par jour")
        if late > cfg["retard_max_s"]:
            why.append(f"signal trop tardif ({late:.0f} s)")
        if why:
            self.stats["signaux non envoyés"] += 1
            return log("signal_non_envoye", raisons=why, **rec)
        self.sent[m] = time.time()                      # before sending: a CA is never sent twice, even after an error
        json.dump(self.sent, open(os.path.join(OUT, "envoyes.json"), "w"), indent=0)
        if self.live:
            await self.tg.send_message(self.trojan, m)
        self.stats["signaux"] += 1
        log("envoi_trojan" if self.live else "signal_simulation", **rec)
        self.pos[m] = Position(self, c, t)
        if self.cfg["alertes_achat"]:
            await self.notify(f"Envoyé à Trojan : {t.get('symbol')} MC {mc / 1e3:.0f} k$, créé {age:.0f} min avant "
                              f"son achat, envoyé {late:.1f} s après.\n{m}")

    async def track(self):
        while True:
            await asyncio.sleep(2)
            live = [p for p in self.pos.values() if p.state == "open"]
            if live:
                px = await asyncio.to_thread(jup_prices, [p.mint for p in live])
                for p in live:
                    if px.get(p.mint):
                        try:
                            await p.step(px[p.mint])
                        except Exception as e:
                            log("erreur_suivi", mint=p.mint, erreur=repr(e))

    def write_summary(self):
        closed = [p for p in self.pos.values() if p.state == "closed"]
        by_day = collections.defaultdict(list)
        for p in closed:
            by_day[time.strftime("%d/%m", time.localtime(p.c["bt"]))].append(p.pnl_pct)
        json.dump({"compteurs": dict(self.stats), "positions_simulees_cloturees": len(closed),
                   "pnl_moyen_pct_hors_frais_fixes": round(100 * sum(p.pnl_pct for p in closed) / max(len(closed), 1), 2),
                   "pnl_total_usd_simule": round(sum(p.pnl_usd for p in closed), 2),
                   "par_jour_pct_moyen": {d: round(100 * sum(v) / len(v), 2) for d, v in by_day.items()}},
                  open(os.path.join(OUT, "resume.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)

    async def run(self):
        os.makedirs(OUT, exist_ok=True)
        if self.live:
            await self.connect()
        sol = (await asyncio.to_thread(jup_prices, [WSOL])).get(WSOL)
        if sol:
            print(f"{now_s()} SOL = {sol:.2f} $ : {self.cfg['mise_usd']} $ = {self.cfg['mise_usd'] / sol:.4f} SOL "
                  f"(montant à régler dans l'Autobuy de Trojan)", flush=True)
        await asyncio.to_thread(self.seed)
        log("demarrage", mode="RÉEL (envoi à Trojan)" if self.live else "simulation (rien n'est envoyé)",
            wallet=self.W, regles={k: self.cfg[k] for k in ("mc_min_usd", "age_max_min", "baisse_max_sous_ath",
                                                             "tp1", "tp2", "stop", "sortie_min", "stop_1re_minute",
                                                             "vente_totale_a", "plage_5min", "vente_auto")})
        await self.notify("Bot Trojan démarré" + (" (envoi réel)" if self.live else ""))
        await asyncio.gather(self.watch(), self.track())


async def test_telegram(cfg):
    """login, a test message to Saved Messages, and Trojan's last messages with their buttons (nothing is sent to
    Trojan)"""
    b = Bot(cfg, live=True)
    await b.connect()
    await b.notify("Test du bot Trojan : connexion OK")
    print(f"connexion OK ; message de test envoyé dans tes « Messages enregistrés ».\nDerniers messages de {cfg['trojan_bot']} :")
    for m in reversed(await b.tg.get_messages(b.trojan, limit=8)):
        print("-" * 60, f"\n{m.date:%d/%m %H:%M}", (m.raw_text or "")[:500])
        for row in m.buttons or []:
            print("   [boutons]", " | ".join(x.text for x in row))
    await b.tg.disconnect()


async def test_sell(cfg, mint):
    """sells 100 % of one token through Trojan's buttons, as the bot does for its two exits"""
    b = Bot(cfg, live=True)
    await b.connect()
    err, reply = await b.sell_all(mint)
    log("test_vente", mint=mint, erreur=err, reponse=reply[:300])
    print("OK" if not err else f"ÉCHEC : {err}", "\n" + reply)
    await b.tg.disconnect()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="envoie vraiment les CA à Trojan")
    ap.add_argument("--config", default=os.path.join(ROOT, "trojan_config.json"))
    ap.add_argument("--test-telegram", action="store_true")
    ap.add_argument("--test-vente", metavar="CA")
    a = ap.parse_args()
    cfg = dict(DEFAULTS)
    if os.path.exists(a.config):
        cfg.update(json.load(open(a.config, encoding="utf-8-sig")))
    elif a.live or a.test_telegram or a.test_vente:
        sys.exit(f"{a.config} introuvable : copie trojan_config.example.json en trojan_config.json et remplis-le")
    try:
        asyncio.run(test_telegram(cfg) if a.test_telegram else test_sell(cfg, a.test_vente) if a.test_vente
                    else Bot(cfg, a.live).run())
    except KeyboardInterrupt:
        print("arrêt demandé", flush=True)


if __name__ == "__main__":
    main()
