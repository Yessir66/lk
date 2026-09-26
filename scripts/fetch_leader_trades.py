#!/usr/bin/env python3
"""Fetch a wallet's successful swaps over the study window and record (mint, side, slot, time, SOL).
Usage: fetch_leader_trades.py <wallet>   (needs data/leader_<prefix>_sigs.json from getSignaturesForAddress)"""
import json, os, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(__file__))
from analyze import parse_tx  # noqa: E402

RPC = "https://api.mainnet-beta.solana.com"
W = sys.argv[1]
DATA = os.path.join(os.path.dirname(__file__), "..", "data")
OUT = os.path.join(DATA, f"leader_{W[:8]}_trades.jsonl")


def rpc(m, p):
    for a in range(6):
        try:
            req = urllib.request.Request(RPC, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                b = json.load(r)
                if "error" in b:
                    time.sleep(min(2 ** a, 15)); continue
                return b["result"]
        except Exception:
            time.sleep(min(2 ** a, 15))
    return None


sigs = [s for s in json.load(open(os.path.join(DATA, f"leader_{W[:8]}_sigs.json"))) if s["err"] is None]
done = set()
if os.path.exists(OUT):
    done = {json.loads(l)["sig"] for l in open(OUT)}
with open(OUT, "a") as f:
    for i, s in enumerate(sigs, 1):
        if s["signature"] in done:
            continue
        tx = rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}])
        ev = parse_tx(s["signature"], tx, W) if tx else None
        f.write(json.dumps({"sig": s["signature"], "slot": s["slot"], "bt": s["blockTime"],
                            "side": ev["side"] if ev else None, "mint": ev["mint"] if ev else None,
                            "sol": ev["quote_amount_sol"] if ev else None}) + "\n")
        if i % 250 == 0:
            f.flush(); print(f"{i}/{len(sigs)}", flush=True)
        time.sleep(0.2)
print("done", flush=True)
