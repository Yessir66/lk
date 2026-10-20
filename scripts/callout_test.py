#!/usr/bin/env python3
"""
F1R and F1 (flow_backtest.py) on one token with the entry at a chosen time instead of a wallet buy (a callout).

Fetches all trades of the token (swap-api.pump.fun) from 5 min before the first entry to 45 min after the last
one (or now), then for each entry time prints every sale of each algorithm and the $ result for a 50 $ buy.

Usage: callout_test.py <mint> <YYYY-MM-DD> <HH:MM:SS French time> [<HH:MM:SS> ...]
"""
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
         "fin": "fin des données"}
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
    mint, day, hours = sys.argv[1], sys.argv[2], sys.argv[3:]
    ets = sorted(calendar.timegm(time.strptime(f"{day} {h}", "%Y-%m-%d %H:%M:%S")) - TZ for h in hours)
    tr, ok = fetch(mint, ets[0] - 300, min(int(time.time()), ets[-1] + B.HORIZON))
    fr = lambda s: time.strftime("%H:%M:%S", time.gmtime(s + TZ))
    print(f"{mint}: {len(tr)} trades {'' if ok else '(INCOMPLET) '}de {fr(B.ts(tr[0]['timestamp']))} à {fr(B.ts(tr[-1]['timestamp']))}")
    os.makedirs("data/callout", exist_ok=True)
    for et in ets:
        k = max(i for i, t in enumerate(tr) if B.ts(t["timestamp"]) <= et)
        f = f"data/callout/{mint}_{et}.json"
        json.dump({"mint": mint, "bt": et, "sig": tr[k]["tx"], "complete": True, "trades": tr}, open(f, "w"))
        w = B.Window(f, "none")
        if not w.ok:
            print(f"\n### Entrée {fr(et)} : pas assez de données")
            continue
        p = B.Path(w, stake=50.0)
        mc0 = p.entry * 1e9
        top = int(np.nanargmax(p.x))
        print(f"\n### Entrée {fr(et)} (achat à {fr(p.t0)}, MC {mc0 / 1e3:.1f}k$ avec notre impact {p.impact:.1%}, "
              f"{'courbe pump.fun' if w.venue == 'pump' else w.venue}) — plus haut ensuite x{p.x[top]:.2f} à {fr(p.t0 + top)}")
        print(f"    nouveaux acheteurs à +2 min : {B.cum_new(p, 120):.0f}")
        for lab, (rule, extra) in RULES.items():
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
