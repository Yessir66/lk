#!/usr/bin/env python3
"""Fetch raw transaction history for a Solana wallet via public RPC.

Resumable: signatures are cached in data/signatures.json, each parsed
transaction is cached as data/tx/<signature>.json so re-runs only fetch
what's missing.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

RPC_URL = "https://api.mainnet-beta.solana.com"
WALLET = sys.argv[1] if len(sys.argv) > 1 else "AfPWFykWPZZxU2CyF6BcPoY2v8VEkmYS7ZggvELK7Pv1"
MAX_SIGNATURES = int(sys.argv[2]) if len(sys.argv) > 2 else 800

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
TX_DIR = os.path.join(DATA_DIR, "tx")
SIG_FILE = os.path.join(DATA_DIR, "signatures.json")
os.makedirs(TX_DIR, exist_ok=True)


def rpc_call(method, params, retries=6):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    last_err = None
    for attempt in range(retries):
        req = urllib.request.Request(
            RPC_URL, data=payload, headers={"Content-Type": "application/json"}
        )
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


def fetch_all_signatures():
    if os.path.exists(SIG_FILE):
        with open(SIG_FILE) as f:
            sigs = json.load(f)
        if len(sigs) >= MAX_SIGNATURES:
            return sigs[:MAX_SIGNATURES]
    sigs = []
    before = None
    while len(sigs) < MAX_SIGNATURES:
        params = [WALLET, {"limit": 1000}]
        if before:
            params[1]["before"] = before
        batch = rpc_call("getSignaturesForAddress", params)
        if not batch:
            break
        sigs.extend(batch)
        before = batch[-1]["signature"]
        print(f"  fetched {len(sigs)} signatures so far...", flush=True)
        if len(batch) < 1000:
            break
        time.sleep(0.3)
    with open(SIG_FILE, "w") as f:
        json.dump(sigs, f)
    return sigs[:MAX_SIGNATURES]


def fetch_transactions(sigs):
    total = len(sigs)
    done = 0
    failed = 0
    for entry in sigs:
        sig = entry["signature"]
        out_path = os.path.join(TX_DIR, f"{sig}.json")
        done += 1
        if os.path.exists(out_path):
            continue
        try:
            tx = rpc_call(
                "getTransaction",
                [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}],
            )
            with open(out_path, "w") as f:
                json.dump(tx, f)
        except Exception as e:
            failed += 1
            print(f"  ERROR sig={sig}: {e}", flush=True)
        if done % 25 == 0:
            print(f"  fetched tx {done}/{total} ({failed} erreurs)", flush=True)
        time.sleep(0.25)
    print(f"done: {done}/{total} transactions cached ({failed} erreurs)", flush=True)


if __name__ == "__main__":
    print(f"Wallet: {WALLET}", flush=True)
    print("Step 1: fetching signature list...", flush=True)
    sigs = fetch_all_signatures()
    print(f"Total signatures: {len(sigs)}", flush=True)
    print("Step 2: fetching parsed transactions...", flush=True)
    fetch_transactions(sigs)
