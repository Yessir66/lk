#!/usr/bin/env python3
"""
Paper-trading bot for the "flow" algorithm (F1 in flow_report.py): no wallet, no keys, no real orders.

Listens to a followed wallet's trades (PumpPortal websocket, subscribeAccountTrade). When it buys on the
pump.fun bonding curve, opens a paper position STAKE $ two seconds later at the then spot price (plus our
price impact), follows every trade of the token (swap-api.pump.fun, polled every POLL s) and applies:
  1. at +2 min: fewer than 50 new wallets bought since entry -> sell all
  2. price >= x1.5 -> sell half
  3. after 5 min: new-wallet inflow (per minute) < 30% of its peak -> sell the rest
  4. at +5 min: price < x0.85 -> sell all;  5. 45 min -> sell all
  F1R (default, --no-runner for F1): the exhaustion exit (3) only applies below x3 and during the first 45 min;
  once the price has reached x3, what is left is sold 50% under its high; everything is sold at 3 h.
Paper fills: spot at the next poll x (1 - 2% slippage - impact), 2.25% fee per side, 0.21 $ gas per transaction.
"Form" filter: every signal is paper-traded, but a position counts as TAKEN only if the last FORM_N closed
paper trades sum > 0 (seeded with the last FORM_N backtest results, --seed file). The summary reports both.
Journal: data/paper/journal.jsonl (one line per event), data/paper/summary.json.

Usage: flow_bot.py <wallet> [--stake 50] [--hours 12]
"""
import argparse
import asyncio
import calendar
import collections
import json
import os
import time
import urllib.request

import websockets

FEE, SLIP, GAS, POLL = 0.0225, 0.02, 0.21, 2.0
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "paper")
H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
POSITIONS = {}
RUNNER, RUN_ABOVE, RUN_TRAIL = True, 3.0, 0.5    # F1R: once x3 is reached, the rest runs on a 50% trailing stop, up to 3 h
FORM_N = 15
CLOSED_PNL = []          # chronological results of closed paper trades (seeded from the backtest)


def form_active():
    last = CLOSED_PNL[-FORM_N:]
    return len(last) < FORM_N or sum(last) > 0


def write_summary():
    closed = [p for p in POSITIONS.values() if p.state == "closed" and hasattr(p, "pnl")]
    by_day, by_day_f = collections.defaultdict(float), collections.defaultdict(float)
    for p in closed:
        d = time.strftime("%d/%m", time.gmtime(p.t_trigger + 7200))
        by_day[d] += p.pnl
        if getattr(p, "taken", True):
            by_day_f[d] += p.pnl
    json.dump({"positions_cloturees": len(closed), "ouvertes": sum(p.state == "open" for p in POSITIONS.values()),
               "pnl_F1_brut": round(sum(p.pnl for p in closed), 2),
               "pnl_F1_filtre_forme": round(sum(p.pnl for p in closed if getattr(p, "taken", True)), 2),
               "positions_prises_avec_filtre": sum(1 for p in closed if getattr(p, "taken", True)),
               "filtre_forme_actif_maintenant": form_active(), "somme_15_derniers": round(sum(CLOSED_PNL[-FORM_N:]), 2),
               "par_jour_brut": {d: round(v, 2) for d, v in by_day.items()},
               "par_jour_filtre": {d: round(v, 2) for d, v in by_day_f.items()}},
              open(os.path.join(OUT, "summary.json"), "w"), indent=1)


def log(ev, **kw):
    os.makedirs(OUT, exist_ok=True)
    rec = dict(t=time.time(), when=time.strftime("%d/%m %H:%M:%S", time.gmtime(time.time() + 7200)), ev=ev, **kw)
    with open(os.path.join(OUT, "journal.jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(rec["when"], ev, {k: v for k, v in kw.items() if k != "trades"}, flush=True)


def get(url):
    for k in range(3):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=15).read())
        except Exception:
            time.sleep(1 + k)
    return None


def trades_since(mint, t_from):
    """all trades of the token after t_from (unix), oldest first"""
    out, cursor = [], f"{'9' * 22}-{int(time.time() * 1000) + 60000}"
    for _ in range(30):
        d = get(f"https://swap-api.pump.fun/v2/coins/{mint}/trades?limit=100&cursor={cursor}")
        if not d or not d.get("trades"):
            break
        out += d["trades"]
        last = calendar.timegm(time.strptime(d["trades"][-1]["timestamp"][:19], "%Y-%m-%dT%H:%M:%S"))
        if last < t_from or not d["pagination"]["hasMore"]:
            break
        cursor = d["pagination"]["nextCursor"]
    out = [t for t in out if calendar.timegm(time.strptime(t["timestamp"][:19], "%Y-%m-%dT%H:%M:%S")) >= t_from]
    return sorted(out, key=lambda t: t["slotIndexId"])


class Position:
    def __init__(self, mint, wallet_sig, stake):
        self.mint, self.wallet_sig, self.stake = mint, wallet_sig, stake
        self.t_trigger = time.time()
        self.seen_ids, self.seen_users, self.first_buys = set(), set(), []   # first_buys: times of new buyers
        self.left, self.cash, self.ntx, self.peak_new, self.state = 1.0, 0.0, 1, 0, "pending"
        self.tp_done = self.check_done = self.slow_done = False
        self.entry = self.px = None

    def ingest(self, trs):
        for t in trs:
            if t["slotIndexId"] in self.seen_ids:
                continue
            self.seen_ids.add(t["slotIndexId"])
            u, ts = t["userAddress"], calendar.timegm(time.strptime(t["timestamp"][:19], "%Y-%m-%dT%H:%M:%S"))
            if u not in self.seen_users:
                self.seen_users.add(u)
                if t["type"] == "buy":
                    self.first_buys.append(ts)
            if t.get("priceUsd"):
                self.px = float(t["priceUsd"])

    def sell(self, frac, why):
        frac = min(frac, self.left)
        fx = self.px / self.entry * (1 - SLIP - self.impact)
        self.cash += frac * self.stake / (1 + FEE) * fx * (1 - FEE)
        self.left -= frac
        self.ntx += 1
        log("vente", mint=self.mint, part=round(frac, 2), x=round(fx, 3), raison=why, el=int(time.time() - self.t_entry))
        if self.left <= 1e-9:
            self.close()

    def close(self):
        if self.left > 1e-9:
            self.cash += self.left * self.stake / (1 + FEE) * self.px / self.entry * (1 - SLIP - self.impact) * (1 - FEE)
            self.ntx += 1
            self.left = 0.0
        self.pnl = self.cash - self.stake - GAS * self.ntx
        self.state = "closed"
        CLOSED_PNL.append(self.pnl)
        log("cloture", mint=self.mint, pnl=round(self.pnl, 2), prise=getattr(self, "taken", True))
        write_summary()

    def step(self):
        now = time.time()
        el = now - self.t_entry
        x = self.px / self.entry
        new_since = sum(1 for s in self.first_buys if s >= self.t_entry - 1)
        new1 = sum(1 for s in self.first_buys if s > now - 60)
        self.peak_new = max(self.peak_new, new1)
        if not self.check_done and el >= 120:
            self.check_done = True
            if new_since < 50:
                return self.sell(1.0, f"contrôle 2 min: {new_since} nouveaux wallets < 50")
        if not self.tp_done and x >= 1.5:
            self.tp_done = True
            self.sell(0.5, "x1.5")
            if self.state == "closed":
                return
        if not self.slow_done and el >= 300:
            self.slow_done = True
            if x < 0.85:
                return self.sell(1.0, f"5 min sous x0.85 (x{x:.2f})")
        self.peak_x = max(getattr(self, "peak_x", 1.0), x)
        runner = RUNNER and self.peak_x >= RUN_ABOVE
        if (el >= 300 and el < 45 * 60 and self.peak_new >= 20 and new1 < 0.3 * self.peak_new
                and not (RUNNER and x >= RUN_ABOVE)):
            return self.sell(1.0, f"essoufflement: {new1}/min < 30% du pic {self.peak_new}")
        if runner and x <= (1 - RUN_TRAIL) * self.peak_x:
            return self.sell(1.0, f"course: x{x:.2f} < {int(100 * (1 - RUN_TRAIL))}% du plus haut x{self.peak_x:.2f}")
        if el >= (3 * 3600 if RUNNER else 45 * 60):
            return self.sell(1.0, "3 h" if RUNNER else "45 min")


async def follow(pos):
    await asyncio.sleep(2)
    seed = await asyncio.to_thread(trades_since, pos.mint, int(pos.t_trigger) - 300)
    pos.ingest(seed)
    if pos.px is None:
        log("abandon", mint=pos.mint, raison="pas de prix")
        pos.state = "closed"
        pos.pnl = 0.0
        return
    pos.entry_spot, pos.t_entry = pos.px, time.time()
    mine = [t for t in seed if t["tx"] == pos.wallet_sig]
    wallet_px = float(mine[0]["priceUsd"]) if mine and mine[0].get("priceUsd") else None
    pos.impact = 0.005                      # ~50 $ on a ~20 k$ curve; refined below if the SOL reserve is known
    pos.entry = pos.entry_spot * (1 + pos.impact)
    pos.state = "open"
    pos.taken = form_active()
    log("entree", mint=pos.mint, mc=round(pos.entry_spot * 1e9), ecart_wallet=round(pos.entry_spot / wallet_px - 1, 3) if wallet_px else None,
        prise=pos.taken, somme_15_derniers=round(sum(CLOSED_PNL[-FORM_N:]), 2))
    while pos.state == "open":
        await asyncio.sleep(POLL)
        trs = await asyncio.to_thread(trades_since, pos.mint, int(time.time()) - 20)
        pos.ingest(trs)
        pos.step()


async def main(wallet, stake, hours):
    positions, tasks = POSITIONS, set()
    t_end = time.time() + hours * 3600
    log("demarrage", wallet=wallet, mise=stake)
    while time.time() < t_end:
        try:
            async with websockets.connect("wss://pumpportal.fun/api/data", open_timeout=20, ping_interval=20) as ws:
                await ws.send(json.dumps({"method": "subscribeAccountTrade", "keys": [wallet]}))
                while time.time() < t_end:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=600))
                    if msg.get("traderPublicKey") != wallet:
                        continue
                    mint, kind, pool = msg.get("mint"), msg.get("txType"), msg.get("pool", "pump")
                    log("wallet", mint=mint, type=kind, sol=msg.get("solAmount"), pool=pool)
                    if kind != "buy":
                        continue
                    if pool != "pump":
                        log("ignore", mint=mint, raison=f"achat hors courbe pump.fun ({pool})")
                        continue
                    if mint in positions and positions[mint].state != "closed":
                        continue
                    positions[mint] = Position(mint, msg.get("signature"), stake)
                    task = asyncio.create_task(follow(positions[mint]))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
        except asyncio.TimeoutError:
            continue
        except Exception as e:
            log("reconnexion", erreur=str(e)[:120])
            await asyncio.sleep(5)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("wallet")
    ap.add_argument("--stake", type=float, default=50.0)
    ap.add_argument("--hours", type=float, default=12.0)
    ap.add_argument("--test-mint", help="dry run of the position logic on one token, as if the wallet had just bought it")
    ap.add_argument("--seed", help="json list of the latest backtest results (oldest first) to start the form filter")
    ap.add_argument("--no-runner", action="store_true", help="plain F1 (everything sold within 45 min)")
    a = ap.parse_args()
    RUNNER = not a.no_runner
    if a.seed:
        CLOSED_PNL.extend(json.load(open(a.seed)))
    if a.test_mint:
        OUT = os.path.join(OUT, "test")
        POSITIONS[a.test_mint] = Position(a.test_mint, None, a.stake)
        asyncio.run(follow(POSITIONS[a.test_mint]))
    else:
        asyncio.run(main(a.wallet, a.stake, a.hours))
