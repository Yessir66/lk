#!/usr/bin/env python3
"""
For every token a sniper wallet bought (data/leader_<prefix>_trades.jsonl), fetch all pump.fun trades
from creation up to AFTER_S seconds after the sniper's buy (swap-api, forged keyset cursor walking
backwards). This is a complete, unsampled universe: each token is labelled by whether the studied wallet
(or a sibling) bought it, and the early trades give the features.

Usage: fetch_universe_trades.py <wallet> [<wallet> ...]
Output: data/universe_trades/<mint>.json  {mint, anchor_bt, complete, trades}
"""
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from fetch_early_trades import get  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
OUT = os.path.join(DATA, "universe_trades")
AFTER_S = 12
MAX_PAGES = 6


def fetch(mint, anchor_bt):
    cursor = f"{'9' * 22}-{int((anchor_bt + AFTER_S) * 1000)}"
    trades, complete = [], False
    for _ in range(MAX_PAGES):
        d = get(f"https://swap-api.pump.fun/v2/coins/{mint}/trades?limit=100&cursor={cursor}")
        if not d or "trades" not in d:
            break
        for t in d["trades"]:
            trades.append({"slot": int(t["slotIndexId"][:12]), "idx": int(t["slotIndexId"][12:]), "ts": t["timestamp"],
                           "user": t["userAddress"], "type": t["type"],
                           "sol": float(t["amountSol"]) if t.get("amountSol") is not None else 0.0,
                           "tokens": float(t["baseAmount"]) if t.get("baseAmount") is not None else 0.0,
                           "price": float(t["fillPriceSol"]) if t.get("fillPriceSol") else None})
        if not d["pagination"]["hasMore"]:
            complete = True
            break
        cursor = d["pagination"]["nextCursor"]
        time.sleep(0.3)
    trades.sort(key=lambda t: (t["slot"], t["idx"]))
    return {"mint": mint, "anchor_bt": anchor_bt, "complete": complete, "trades": trades}


def main():
    os.makedirs(OUT, exist_ok=True)
    first = {}
    for w in sys.argv[1:]:
        for line in (l for p in glob.glob(os.path.join(DATA, f"leader_{w[:8]}_trades*.jsonl")) for l in open(p)):
            t = json.loads(line)
            if t["side"] == "BUY" and t["mint"] and (t["mint"] not in first or t["bt"] < first[t["mint"]]):
                first[t["mint"]] = t["bt"]
    todo = [(m, bt) for m, bt in sorted(first.items(), key=lambda kv: kv[1])
            if not os.path.exists(os.path.join(OUT, f"{m}.json"))]
    print(f"{len(first)} tokens, {len(todo)} à récupérer", flush=True)
    for i, (m, bt) in enumerate(todo, 1):
        try:
            json.dump(fetch(m, bt), open(os.path.join(OUT, f"{m}.json"), "w"))
        except Exception as e:
            print(f"  ERREUR {m}: {e}", flush=True)
        if i % 100 == 0:
            print(f"  {i}/{len(todo)}", flush=True)
        time.sleep(0.3)
    print("done", flush=True)


if __name__ == "__main__":
    main()
