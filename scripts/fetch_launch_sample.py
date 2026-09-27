#!/usr/bin/env python3
"""
Early trades for the unbiased launch sample (sample_launches.py) and for every token the studied
wallet bought, so both come from the same full-universe fetch.

  fetch_launch_sample.py mints  [--shard K/N] [--rpc URL] [--dense]   resolve each sampled create tx to its mint
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


def created_mint(tx):
    """(mint, kind) of a transaction touching the mint authority. The launched mint is the one
    initialised by initializeMint(2); kind is the pump.fun instruction ('CreateV2', ...). Transactions
    without a pump.fun Create (e.g. CreatePool at migration) are not launches: mint None."""
    logs = tx["meta"].get("logMessages") or []
    kinds = [l.split("Instruction: ")[1] for l in logs if "Instruction: Create" in l]
    ixs = list(tx["transaction"]["message"]["instructions"])
    for grp in tx["meta"].get("innerInstructions") or []:
        ixs += grp["instructions"]
    minted = [ix["parsed"]["info"]["mint"] for ix in ixs if isinstance(ix.get("parsed"), dict)
              and ix["parsed"].get("type") in ("initializeMint", "initializeMint2")]
    if not any(k.startswith("Create") and k != "CreatePool" for k in kinds) or len(set(minted)) != 1:
        return None, ",".join(kinds) or "autre"
    return minted[0], ",".join(kinds)


def mints():
    """Resolve sampled creates to mints. Re-running retries only the ones without an answer yet
    (lines with a 'kind' are final: either a mint or 'not a launch')."""
    if "--rpc" in sys.argv:
        FL.RPC = sys.argv[sys.argv.index("--rpc") + 1]
    k, n = shard()
    dense = "--dense" in sys.argv  # the denser test-period sample (sample_launches.py --dense)
    census = json.load(open(os.path.join(DATA, "launch_census_dense.json" if dense else "launch_census.json")))
    out_p = os.path.join(DATA, f"launch_sample_mints{'_dense' if dense else ''}.s{k}.jsonl")
    done = set()
    for p in glob.glob(os.path.join(DATA, "launch_sample_mints*.jsonl")):
        for l in open(p):
            x = json.loads(l)
            if x["mint"] or x.get("kind"):
                done.add(x["sig"])
    todo = [s for s in census["sample"][k::n] if s["sig"] not in done]
    print(f"{len(todo)} créations à résoudre", flush=True)
    with open(out_p, "a") as f:
        for i, s in enumerate(todo, 1):
            tx = FL.rpc("getTransaction", [s["sig"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}])
            mint, kind = created_mint(tx) if tx else (None, None)
            f.write(json.dumps({**s, "mint": mint, "kind": kind}) + "\n")
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
