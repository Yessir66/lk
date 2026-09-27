#!/usr/bin/env python3
"""
Trade-by-trade price path of every position the studied wallet held: all pump.fun trades from a few
seconds before its buy to 15 s after its last sell (its own buys/sells included), to study its exit
rules and to replay positions in the backtester.

Usage: fetch_position_paths.py [--shard K/N]
Output: data/position_paths/<mint>.json {mint, buy_bt, last_sell_bt, complete, trades}
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from fetch_early_trades import get  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
OUT = os.path.join(DATA, "position_paths")
BEFORE_S, AFTER_S, MAX_PAGES = 5, 15, 12


def parse(t):
    return {"slot": int(t["slotIndexId"][:12]), "idx": int(t["slotIndexId"][12:]), "ts": t["timestamp"],
            "user": t["userAddress"], "type": t["type"],
            "sol": float(t["amountSol"]) if t.get("amountSol") is not None else 0.0,
            "tokens": float(t["baseAmount"]) if t.get("baseAmount") is not None else 0.0,
            "price": float(t["fillPriceSol"]) if t.get("fillPriceSol") else None}


def fetch_path(mint, t_from, t_to):
    cursor = f"{'9' * 22}-{int(t_to * 1000)}"
    trades, complete = [], False
    for _ in range(MAX_PAGES):
        d = get(f"https://swap-api.pump.fun/v2/coins/{mint}/trades?limit=100&cursor={cursor}")
        if not d or "trades" not in d:
            break
        trades += [parse(t) for t in d["trades"]]
        oldest = int(d["trades"][-1]["slotIndexId"][:12]) if d["trades"] else None
        if not d["pagination"]["hasMore"] or (d["trades"] and int(d["pagination"]["nextCursor"].split("-")[1]) / 1000 < t_from):
            complete = True
            break
        cursor = d["pagination"]["nextCursor"]
        time.sleep(0.3)
    trades.sort(key=lambda t: (t["slot"], t["idx"]))
    return trades, complete


def main():
    os.makedirs(OUT, exist_ok=True)
    k, n = tuple(map(int, sys.argv[sys.argv.index("--shard") + 1].split("/"))) if "--shard" in sys.argv else (0, 1)
    ledger = json.load(open(os.path.join(DATA, "wallet_ledger.json")))
    todo = sorted(m for m, v in ledger.items() if v["status"] == "bought" and v.get("last_sell_bt")
                  and not os.path.exists(os.path.join(OUT, f"{m}.json")))[k::n]
    print(f"{len(todo)} positions à récupérer", flush=True)
    for i, m in enumerate(todo, 1):
        v = ledger[m]
        try:
            tr, complete = fetch_path(m, v["first_buy_bt"] - BEFORE_S, v["last_sell_bt"] + AFTER_S)
            json.dump({"mint": m, "buy_bt": v["first_buy_bt"], "last_sell_bt": v["last_sell_bt"],
                       "complete": complete, "trades": tr}, open(os.path.join(OUT, f"{m}.json"), "w"))
        except Exception as e:
            print(f"  ERREUR {m}: {e}", flush=True)
        if i % 100 == 0:
            print(f"  {i}/{len(todo)}", flush=True)
        time.sleep(0.3)
    print("done", flush=True)


if __name__ == "__main__":
    main()
