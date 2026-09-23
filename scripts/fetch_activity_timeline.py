#!/usr/bin/env python3
"""
For each token, reconstruct the transaction-count timeline on its
bonding curve in fixed time windows after creation (1s/2s/3s/5s/10s),
using only getSignaturesForAddress (cheap: blockTime comes for free,
no getTransaction needed). Used to test whether the wallet buys once a
fixed activity threshold (distinct wallets / tx volume) is reached,
rather than reacting to content or a fixed elapsed-time delay.

Resumable cache: data/activity_timeline/<mint>.json
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(__file__))
from analyze import parse_tx, WALLET as DEFAULT_WALLET, load_transactions  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
META_DIR = os.path.join(DATA_DIR, "coin_meta")
TIMELINE_DIR = os.path.join(DATA_DIR, "activity_timeline")
os.makedirs(TIMELINE_DIR, exist_ok=True)

RPC_URL = "https://api.mainnet-beta.solana.com"
WALLET = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_WALLET
WINDOWS = [1, 2, 3, 5, 10, 20]


def rpc_call(method, params, retries=6):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    last_err = None
    for attempt in range(retries):
        req = urllib.request.Request(RPC_URL, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
                if "error" in body:
                    last_err = body["error"]
                    time.sleep(min(1.5 ** attempt, 15))
                    continue
                return body["result"]
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            time.sleep(min(2 ** attempt, 20) if e.code == 429 else min(1.5 ** attempt, 15))
        except Exception as e:
            last_err = str(e)
            time.sleep(min(1.5 ** attempt, 15))
    raise RuntimeError(f"RPC call {method} failed after retries: {last_err}")


def get_first_buys():
    raw = load_transactions()
    events = []
    for sig, tx in raw:
        ev = parse_tx(sig, tx, WALLET)
        if ev:
            events.append(ev)
    events.sort(key=lambda e: e["blockTime"] or 0)
    first_buy = {}
    for e in events:
        if e["side"] == "BUY" and e["mint"] not in first_buy:
            first_buy[e["mint"]] = e
    return first_buy


def process_mint(mint, first_buy_event, created_ts):
    out_path = os.path.join(TIMELINE_DIR, f"{mint}.json")
    if os.path.exists(out_path):
        return
    meta_path = os.path.join(META_DIR, f"{mint}.json")
    meta = json.load(open(meta_path))
    bonding_curve = meta.get("bonding_curve")
    if not bonding_curve:
        return

    sigs = rpc_call("getSignaturesForAddress", [bonding_curve, {"before": first_buy_event["sig"], "limit": 1000}])
    block_times = [s["blockTime"] for s in sigs if s.get("blockTime")]

    counts = {}
    for w in WINDOWS:
        cutoff = created_ts + w
        counts[str(w)] = sum(1 for bt in block_times if bt <= cutoff)

    result = {
        "mint": mint,
        "created_timestamp": created_ts,
        "first_buy_blockTime": first_buy_event["blockTime"],
        "entry_delay_s": first_buy_event["blockTime"] - created_ts,
        "total_prior_tx_capped": len(sigs) >= 1000,
        "total_prior_tx": len(sigs),
        "counts_by_window": counts,
    }
    with open(out_path, "w") as f:
        json.dump(result, f)


def main():
    first_buys = get_first_buys()
    print(f"{len(first_buys)} tokens à traiter")
    done, failed = 0, 0
    for mint, ev in first_buys.items():
        meta_path = os.path.join(META_DIR, f"{mint}.json")
        if not os.path.exists(meta_path):
            continue
        meta = json.load(open(meta_path))
        if "__error__" in meta or not meta.get("created_timestamp"):
            continue
        created_ts = meta["created_timestamp"] / 1000
        try:
            process_mint(mint, ev, created_ts)
        except Exception as e:
            failed += 1
            print(f"  ERROR mint={mint}: {e}", flush=True)
        done += 1
        if done % 20 == 0:
            print(f"  {done}/{len(first_buys)} ({failed} erreurs)", flush=True)
        time.sleep(0.15)
    print(f"done ({failed} erreurs)")


if __name__ == "__main__":
    main()
