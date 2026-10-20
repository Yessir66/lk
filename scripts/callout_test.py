#!/usr/bin/env python3
"""
F1R and F1 (flow_backtest.py) on one token with the entry at a chosen time instead of a wallet buy (a callout).

Fetches all trades of the token (swap-api.pump.fun) from 5 min before the first entry to 45 min after the last
one (or now), then for each entry time prints every sale of each algorithm and the $ result for a 50 $ buy.

Usage: callout_test.py <mint> <YYYY-MM-DD> <HH:MM:SS French time> [<HH:MM:SS> ...]
       callout_test.py <mint> --wallet <wallet> [--hours 6]   entry 2 s after the wallet's first buy of the token
                                                               (trades of the last --hours hours)
"""
import argparse
import calendar
import json
import os
import sys
import time
import urllib.request

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import flow_backtest as B  # noqa: E402

TZ = 7200
H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
NAMES = {"tp": "50 % à x1,5", "mask0": "contrôle 2 min (< 50 nouveaux acheteurs)", "stop": "stop suiveur -50 % / fin",
         "fin": "fin des données", "wallet": "le wallet a vendu >= 50 % de ses tokens"}
RULES = {"F1R": (B.runner_rule(), {"mask1": "lent (< x0,85 à 5 min)",
                                   "mask2": "essoufflement du flux (< 30 % du pic, sauf au-dessus de x3)"}),
         "F1": (B.flow_rule(), {"mask1": "essoufflement du flux (< 30 % du pic)", "mask2": "lent (< x0,85 à 5 min)"})}


def get(url):
    for k in range(6):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=30).read())
        except Exception:
            time.sleep(2 + 3 * k)
    return None


def fetch(mint, a, b):
    """all trades of mint between unix times a and b, oldest first"""
    cursor, tr, ok = f"{'9' * 22}-{b * 1000}", [], True
    for _ in range(3000):
        d = get(f"https://swap-api.pump.fun/v2/coins/{mint}/trades?limit=100&cursor={cursor}")
        if not d or "trades" not in d:
            ok = False
            break
        tr += d["trades"]
        if not d["trades"] or not d["pagination"]["hasMore"]:
            break
        cursor = d["pagination"]["nextCursor"]
        if int(cursor.split("-")[1]) / 1000 < a:
            break
        time.sleep(0.25)
    tr = [t for t in tr if a <= B.ts(t["timestamp"]) <= b]
    return sorted({t["tx"] + t["slotIndexId"]: t for t in tr}.values(), key=lambda t: t["slotIndexId"]), ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mint")
    ap.add_argument("day", nargs="?")
    ap.add_argument("times", nargs="*")
    ap.add_argument("--wallet")
    ap.add_argument("--hours", type=float, default=6.0)
    a = ap.parse_args()
    mint = a.mint
    fr = lambda s: time.strftime("%H:%M:%S", time.gmtime(s + TZ))
    if a.wallet:
        now = int(time.time())
        tr, ok = fetch(mint, now - int(a.hours * 3600), now)
        buys = [t for t in tr if t["userAddress"] == a.wallet and t["type"] == "buy"]
        if not buys:
            raise SystemExit(f"aucun achat de {a.wallet} sur {mint} dans les {a.hours:g} dernières heures")
        entries = [(B.ts(buys[0]["timestamp"]), buys[0]["tx"])]
        print(f"Achat du wallet : {fr(entries[0][0])}, {float(buys[0]['amountSol']):.2f} SOL, "
              f"MC {float(buys[0]['priceUsd']) * 1e9 / 1e3:.1f}k$ ({buys[0]['program']})")
    else:
        ets = sorted(calendar.timegm(time.strptime(f"{a.day} {h}", "%Y-%m-%d %H:%M:%S")) - TZ for h in a.times)
        tr, ok = fetch(mint, ets[0] - 300, min(int(time.time()), ets[-1] + B.HORIZON))
        entries = [(et, tr[max(i for i, t in enumerate(tr) if B.ts(t["timestamp"]) <= et)]["tx"]) for et in ets]
    print(f"{mint}: {len(tr)} trades {'' if ok else '(INCOMPLET) '}de {fr(B.ts(tr[0]['timestamp']))} à {fr(B.ts(tr[-1]['timestamp']))}")
    os.makedirs("data/callout", exist_ok=True)
    for et, sig in entries:
        f = f"data/callout/{mint}_{et}.json"
        json.dump({"mint": mint, "bt": et, "sig": sig, "complete": True, "trades": tr}, open(f, "w"))
        w = B.Window(f, a.wallet or "none")
        if not w.ok:
            print(f"\n### Entrée {fr(et)} : pas assez de données")
            continue
        p = B.Path(w, stake=50.0)
        mc0 = p.entry * 1e9
        top = int(np.nanargmax(p.x))
        print(f"\n### Entrée {fr(et)} (achat à {fr(p.t0)}, MC {mc0 / 1e3:.1f}k$ avec notre impact {p.impact:.1%}, "
              f"{'courbe pump.fun' if w.venue == 'pump' else w.venue}) — plus haut ensuite x{p.x[top]:.2f} à {fr(p.t0 + top)}")
        print(f"    nouveaux acheteurs à +2 min : {B.cum_new(p, 120):.0f}")
        rules = dict(RULES)
        if a.wallet:
            rules["copier ses ventes"] = (dict(wallet=(0.5, 1.0)), {})
            rules["F1R + ses ventes"] = (dict(B.runner_rule(), wallet=(0.5, 1.0)), RULES["F1R"][1])
        for lab, (rule, extra) in rules.items():
            log = []
            pnl = B.evaluate(p, **rule, log=log)
            print(f"  {lab}: résultat {pnl:+.2f}$ sur 50$")
            for i, frac, fill, kind in log:
                print(f"     {fr(p.t0 + i)}  vend {frac:.0%}  prix x{p.x[i]:.2f} (MC {p.x[i] * mc0 / 1e3:.1f}k$), "
                      f"exécuté x{fill:.2f} — {dict(NAMES, **extra).get(kind, kind)}")
        best = 50 * (p.x[top] * (1 - B.SLIP - p.impact) * (1 - p.fee) / (1 + p.fee) - 1) - 2 * B.GAS
        print(f"  (vente parfaite au plus haut : {best:+.0f}$)")


if __name__ == "__main__":
    main()
