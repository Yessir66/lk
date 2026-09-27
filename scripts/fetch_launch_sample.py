#!/usr/bin/env python3
"""
Early trades for the unbiased launch sample (sample_launches.py) and for every token the studied
wallet bought, so both come from the same full-universe fetch.

  fetch_launch_sample.py mints  [--shard K/N] [--rpc URL]   resolve each sampled create tx to its mint
  fetch_launch_sample.py trades [--shard K/N]               pump.fun trades from creation to +WINDOW_S

Sampled launches are anchored at their create time; the wallet's tokens at its buy time (it buys a
median ~4 s after creation, so creation+0..~16 s is covered). Output: data/full_universe/<mint>.json
{mint, source, anchor_bt, complete, trades}; data/launch_sample_mints.jsonl for the mint resolution.
"""
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import fetch_leader_trades as FL  # noqa: E402
from fetch_universe_trades import fetch  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
OUT = os.path.join(DATA, "full_universe")
WSOL = "So11111111111111111111111111111111111111112"
WINDOW_S = 12


def shard():
    return tuple(map(int, sys.argv[sys.argv.index("--shard") + 1].split("/"))) if "--shard" in sys.argv else (0, 1)


def mints():
    if "--rpc" in sys.argv:
        FL.RPC = sys.argv[sys.argv.index("--rpc") + 1]
    k, n = shard()
    census = json.load(open(os.path.join(DATA, "launch_census.json")))
    out_p = os.path.join(DATA, f"launch_sample_mints.s{k}.jsonl")
    done = {json.loads(l)["sig"] for p in glob.glob(os.path.join(DATA, "launch_sample_mints*.jsonl")) for l in open(p)}
    todo = [s for s in census["sample"][k::n] if s["sig"] not in done]
    print(f"{len(todo)} créations à résoudre", flush=True)
    with open(out_p, "a") as f:
        for i, s in enumerate(todo, 1):
            tx = FL.rpc("getTransaction", [s["sig"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}])
            mint = None
            if tx:
                ms = {b["mint"] for b in tx["meta"].get("postTokenBalances") or [] if b["mint"] != WSOL}
                mint = sorted(ms)[0] if len(ms) == 1 else None
            f.write(json.dumps({**s, "mint": mint}) + "\n")
            if i % 200 == 0:
                f.flush()
                print(f"  {i}/{len(todo)}", flush=True)
            time.sleep(0.1)
    print("done", flush=True)


def trades():
    k, n = shard()
    os.makedirs(OUT, exist_ok=True)
    jobs = {}
    for p in glob.glob(os.path.join(DATA, "launch_sample_mints*.jsonl")):
        for l in open(p):
            s = json.loads(l)
            if s["mint"]:
                jobs[s["mint"]] = ("sample", s["bt"])
    ledger = json.load(open(os.path.join(DATA, "wallet_ledger.json")))
    extra = os.path.join(DATA, "blind", "wallet_buys.json")
    buys = {m: v["first_buy_bt"] for m, v in ledger.items()}
    if os.path.exists(extra):
        buys.update({m: b["bt"] for m, b in json.load(open(extra))["buys"].items() if m not in buys})
    for m, bt in buys.items():
        if m not in jobs:
            jobs[m] = ("wallet", bt)
    todo = sorted((m, src, bt) for m, (src, bt) in jobs.items() if not os.path.exists(os.path.join(OUT, f"{m}.json")))[k::n]
    print(f"{len(jobs)} tokens ({sum(1 for s, _ in jobs.values() if s == 'sample')} échantillon), "
          f"{len(todo)} à récupérer dans cette part", flush=True)
    for i, (m, src, bt) in enumerate(todo, 1):
        try:
            d = fetch(m, bt)  # up to 6 pages of 100 trades back to creation
            d["source"] = src
            json.dump(d, open(os.path.join(OUT, f"{m}.json"), "w"))
        except Exception as e:
            print(f"  ERREUR {m}: {e}", flush=True)
        if i % 200 == 0:
            print(f"  {i}/{len(todo)}", flush=True)
        time.sleep(0.3)
    print("done", flush=True)


if __name__ == "__main__":
    {"mints": mints, "trades": trades}[sys.argv[1]]()
