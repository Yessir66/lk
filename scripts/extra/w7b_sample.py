#!/usr/bin/env python3
"""
Wallet 7BNaxx6K, large sample (see analyses/wallet_7BNaxx6K_preregistration.md):
1. its first buy of every token in its transactions (copy_fetch.py txs, data/wallet_7BNaxx6K/tx), whatever it
   paid with (SOL, USDC, PUMP, ...); the first buy must come >= 2 h after the start of the history;
2. the user's criterion: the token was created 2 h or less before that first buy = pump.fun has no trade of the
   token more than 2 h before it;
3. its fill price in USD (its own trade in swap-api.pump.fun), the price of the first trade >= 2 s later (a copy),
   and pump.fun's minute candles (USD) for the 30 min after the buy.
Cached per token in data/wallet_7BNaxx6K/sample3j/. Tokens in random order (fixed seed) so that a partial run is
a fair sample. Usage: w7b_sample.py [--since YYYY-MM-DD (UTC day the history starts)] [--max N]
"""
import calendar
import collections
import glob
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import copy_fetch as C  # noqa: E402
import flow_backtest as B  # noqa: E402
from callout_test import get  # noqa: E402

W = "7BNaxx6KdUYrjACNQZ9He26NBFoFxujQMAfNLnArLGH5"
D = os.path.join(os.path.dirname(__file__), "..", "..", "data", "wallet_7BNaxx6K")
OUT = os.path.join(D, "sample3j")
API = "https://swap-api.pump.fun"
exec(open(os.path.join(os.path.dirname(__file__), "w7b_lists.py")).read())      # WIN, LOSS: tokens used to choose the rule


def first_buys(since=0):
    recs = []
    pairs = collections.defaultdict(set)
    for f in glob.glob(os.path.join(D, "tx", "*.json")):
        p = C.parse_tx(W, json.load(open(f)))
        if p and p["toks"] and p["bt"] >= since:
            recs.append(p)
            for a in p["toks"]:
                pairs[a] |= set(p["toks"]) - {a}
    quotes = {m for m, s in pairs.items() if len(s) >= 5} | C.NOT_MEME
    start = max(since, min(p["bt"] for p in recs))
    first = {}
    for p in sorted(recs, key=lambda p: p["slot"]):
        toks = {m: q for m, q in p["toks"].items() if m not in quotes}
        paid = [m for m, q in p["toks"].items() if m in quotes and q < 0] or (["SOL"] if p["sol"] < -0.001 else [])
        for m, q in toks.items():
            if q > 0 and paid and m not in first:
                first[m] = dict(bt=p["bt"], slot=p["slot"], sig=p["sig"], paid=paid[0], q=q)
    return {m: v for m, v in first.items() if v["bt"] >= start + 7200}, start


def check(m, fb):
    """age check, fill price, copy price, minute candles -> dict (cached)"""
    f = os.path.join(OUT, m + ".json")
    if os.path.exists(f):
        return json.load(open(f))
    r = dict(mint=m, bt=fb["bt"])
    old = get(f"{API}/v2/coins/{m}/trades?limit=1&cursor={'9' * 22}-{(fb['bt'] - 7200) * 1000}")
    if old is None:
        return None                                              # API failure: retried next run
    if old.get("trades"):
        r["status"] = "créé plus de 2 h avant"
    else:
        near = get(f"{API}/v2/coins/{m}/trades?limit=100&cursor={'9' * 22}-{(fb['bt'] + 8) * 1000}")
        tr = sorted((near or {}).get("trades", []), key=lambda t: t["slotIndexId"])
        mine = [t for t in tr if t["tx"] == fb["sig"]]
        if not mine:
            r["status"] = "pas de données pump.fun"
        else:
            r["fill"] = float(mine[0].get("fillPriceUsd") or mine[0]["priceUsd"])
            after = [t for t in tr if B.ts(t["timestamp"]) >= fb["bt"] + 2 and t["slotIndexId"] > mine[-1]["slotIndexId"]]
            r["copy"] = float(after[0]["priceUsd"]) if after else None
            c = get(f"{API}/v1/coins/{m}/candles?interval=1m&limit=1000")
            c = c if isinstance(c, list) else (c or {}).get("candles", [])
            rows = sorted((int(x["timestamp"]) // (1000 if int(x["timestamp"]) > 1e12 else 1),
                           float(x["open"]), float(x["high"]), float(x["low"]), float(x["close"])) for x in c)
            m0 = fb["bt"] // 60 * 60
            if not rows or rows[0][0] > m0:
                r["status"] = "bougies trop récentes"
            else:
                r["status"] = "ok"
                r["candles"] = [x for x in rows if m0 - 120 <= x[0] <= m0 + 31 * 60]
    json.dump(r, open(f, "w"))
    return r


def main():
    os.makedirs(OUT, exist_ok=True)
    mx = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else None
    since = calendar.timegm(time.strptime(sys.argv[sys.argv.index("--since") + 1], "%Y-%m-%d")) if "--since" in sys.argv else 0
    first, start = first_buys(since)
    dev = set(WIN) | set(LOSS)
    todo = sorted(m for m in first if m not in dev)
    random.Random(0).shuffle(todo)
    if mx:
        todo = todo[:mx]
    todo = sorted(dev & set(first)) + todo         # the 10 tokens of the first study: only to check candles vs trades
    print(f"historique depuis {time.strftime('%d/%m %H:%M', time.gmtime(start + 7200))} ; {len(first)} premiers achats "
          f"(dont {len(dev & set(first))} tokens de mise au point exclus) ; {len(todo)} à vérifier", flush=True)
    st = collections.Counter()
    for k, m in enumerate(todo, 1):
        r = check(m, first[m])
        st[r["status"] if r else "échec API"] += 1
        if k % 25 == 0:
            print(f"  {k}/{len(todo)} {dict(st)}", flush=True)
    print("fini", dict(st), flush=True)


if __name__ == "__main__":
    main()
