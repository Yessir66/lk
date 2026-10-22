#!/usr/bin/env python3
"""Wallet 7BNaxx6K: same data as w7b_sample.py for first buys on tokens created MORE than 2 h before (the ones the
2 h criterion rejects), to see whether the criterion helps. Random sample (fixed seed). Usage: w7b_old.py [--max N]"""
import glob, json, os, random, sys
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import w7b_sample as WS
import flow_backtest as B
from callout_test import get
OUT = os.path.join(WS.D, "sample3j_old")
mx = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else 120
first, _ = WS.first_buys(1792368000)                      # 19/10 00:00 UTC
old = sorted(json.load(open(f))["mint"] for f in glob.glob(os.path.join(WS.OUT, "*.json"))
             if json.load(open(f)).get("status") == "créé plus de 2 h avant")
old = [m for m in old if m in first]
random.Random(1).shuffle(old)
os.makedirs(OUT, exist_ok=True)
print(len(old), "tokens de plus de 2 h ;", min(mx, len(old)), "tirés au hasard", flush=True)
n_ok = 0
for k, m in enumerate(old[:mx], 1):
    f = os.path.join(OUT, m + ".json")
    if not os.path.exists(f):
        fb = first[m]; r = dict(mint=m, bt=fb["bt"])
        near = get(f"{WS.API}/v2/coins/{m}/trades?limit=100&cursor={'9' * 22}-{(fb['bt'] + 8) * 1000}")
        tr = sorted((near or {}).get("trades", []), key=lambda t: t["slotIndexId"])
        mine = [t for t in tr if t["tx"] == fb["sig"]]
        if not mine:
            r["status"] = "pas de données pump.fun"
        else:
            r["fill"] = float(mine[0].get("fillPriceUsd") or mine[0]["priceUsd"])
            after = [t for t in tr if B.ts(t["timestamp"]) >= fb["bt"] + 2 and t["slotIndexId"] > mine[-1]["slotIndexId"]]
            r["copy"] = float(after[0]["priceUsd"]) if after else None
            c = get(f"{WS.API}/v1/coins/{m}/candles?interval=1m&limit=1000")
            c = c if isinstance(c, list) else (c or {}).get("candles", [])
            rows = sorted((int(x["timestamp"]) // (1000 if int(x["timestamp"]) > 1e12 else 1), float(x["open"]), float(x["high"]),
                           float(x["low"]), float(x["close"])) for x in c)
            m0 = fb["bt"] // 60 * 60
            if not rows or rows[0][0] > m0:
                r["status"] = "bougies trop récentes"
            else:
                r["status"] = "ok"; r["candles"] = [x for x in rows if m0 - 120 <= x[0] <= m0 + 31 * 60]
        json.dump(r, open(f, "w"))
    n_ok += json.load(open(f)).get("status") == "ok"
    if k % 20 == 0:
        print(f"  {k}/{min(mx, len(old))} ok {n_ok}", flush=True)
print("fini", n_ok, "ok", flush=True)
