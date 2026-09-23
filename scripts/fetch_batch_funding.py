#!/usr/bin/env python3
"""
Re-examine each creator's funding transaction (already located by
fetch_creator_funding.py) to see whether it ALSO credited other
addresses in the same transaction — a "batch funding" pattern typical
of a factory that mass-produces throwaway creator wallets ahead of a
wave of launches. If our bought creators disproportionately come from
batch-funded siblings, that's another instantly-checkable (no external
call) signal compatible with a ~2-4s entry delay.

Resumable cache: data/batch_funding/<creator>.json
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
FUND_DIR = os.path.join(DATA_DIR, "creator_funding")
BATCH_DIR = os.path.join(DATA_DIR, "batch_funding")
os.makedirs(BATCH_DIR, exist_ok=True)

RPC_URL = "https://api.mainnet-beta.solana.com"


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


def main():
    creators = []
    for fn in os.listdir(FUND_DIR):
        d = json.load(open(os.path.join(FUND_DIR, fn)))
        if d.get("funder") and d.get("oldest_signature"):
            creators.append(d)

    print(f"{len(creators)} créateurs financés à analyser pour du batch funding")
    done = 0
    for d in creators:
        creator = d["creator"]
        out_path = os.path.join(BATCH_DIR, f"{creator}.json")
        done += 1
        if os.path.exists(out_path):
            continue
        try:
            tx = rpc_call("getTransaction", [d["oldest_signature"],
                          {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}])
            meta = tx["meta"]
            keys = tx["transaction"]["message"]["accountKeys"]
            pubkeys = [k["pubkey"] if isinstance(k, dict) else k for k in keys]
            pre_bal, post_bal = meta.get("preBalances", []), meta.get("postBalances", [])
            fee = meta.get("fee", 0)
            credited = []
            for i, pk in enumerate(pubkeys):
                if i < len(pre_bal) and i < len(post_bal):
                    delta = post_bal[i] - pre_bal[i]
                    if i == 0:
                        delta += fee
                    if delta > 0 and pk != d["funder"]:
                        credited.append(pk)
            result = {
                "creator": creator,
                "funder": d["funder"],
                "signature": d["oldest_signature"],
                "other_recipients_same_tx": [pk for pk in credited if pk != creator],
                "is_batch_funding": len(credited) > 1,
            }
        except Exception as e:
            result = {"creator": creator, "error": str(e)}
        with open(out_path, "w") as f:
            json.dump(result, f)
        if done % 20 == 0:
            print(f"  {done}/{len(creators)}", flush=True)
        time.sleep(0.2)
    print("done")


if __name__ == "__main__":
    main()
