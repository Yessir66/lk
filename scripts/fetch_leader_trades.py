#!/usr/bin/env python3
"""Fetch a wallet's successful swaps over the study window and record (mint, side, slot, time, SOL).
Usage: fetch_leader_trades.py <wallet> [--cluster-heads GAP_S]
  (needs data/leader_<prefix>_sigs.json from fetch_wallet_sigs.py)
--shard K/N: only process every N-th signature starting at K, writing leader_<prefix>_trades.sK.jsonl
(shard 0 writes the plain file); readers glob leader_<prefix>_trades*.jsonl. --rpc URL: endpoint to use.
--cluster-heads: only fetch the first transaction of each burst (txs more than GAP_S seconds after the
previous one). A buy opens each burst and the sells follow within seconds, so this finds ~80% of the
buys with ~1/3 of the requests (checked on 4yFAz7dp's full history: gap 2 s -> 1031 of 1267 buys)."""
import glob, json, os, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(__file__))
from analyze import parse_tx  # noqa: E402

RPC = "https://api.mainnet-beta.solana.com"
if "--rpc" in sys.argv:  # e.g. https://solana-mainnet.gateway.tatum.io to run a second shard in parallel
    RPC = sys.argv[sys.argv.index("--rpc") + 1]
DATA = os.path.join(os.path.dirname(__file__), "..", "data")


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


if __name__ == "__main__":
    W = sys.argv[1]
    k, n = map(int, sys.argv[sys.argv.index("--shard") + 1].split("/")) if "--shard" in sys.argv else (0, 1)
    OUT = os.path.join(DATA, f"leader_{W[:8]}_trades.jsonl" if k == 0 else f"leader_{W[:8]}_trades.s{k}.jsonl")
    sigs = [s for s in json.load(open(os.path.join(DATA, f"leader_{W[:8]}_sigs.json"))) if s["err"] is None]
    if "--cluster-heads" in sys.argv:
        gap = float(sys.argv[sys.argv.index("--cluster-heads") + 1])
        sigs.sort(key=lambda s: (s["slot"], s["signature"]))
        sigs = [s for i, s in enumerate(sigs) if i == 0 or s["blockTime"] - sigs[i - 1]["blockTime"] > gap]
    sigs = sigs[k::n]
    done = {json.loads(l)["sig"] for p in glob.glob(os.path.join(DATA, f"leader_{W[:8]}_trades*.jsonl")) for l in open(p)}
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
