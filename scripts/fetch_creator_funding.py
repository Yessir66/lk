#!/usr/bin/env python3
"""
For every distinct token creator our wallet has bought from, find who
funded that creator wallet (its earliest on-chain transaction). If many
"one-off" creators turn out to share a funding wallet with "repeat"
creators, that's a single internally-computable rule (check the
creator's funding source, not the creator's own track record) that
would explain both groups without needing any external signal or
content evaluation — testing the user's point that the ~2-3s entry
delay leaves no room for a multi-hop external call chain.

Resumable cache: data/creator_funding/<creator>.json
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
META_DIR = os.path.join(DATA_DIR, "coin_meta")
FUNDING_DIR = os.path.join(DATA_DIR, "creator_funding")
os.makedirs(FUNDING_DIR, exist_ok=True)

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


def oldest_signature(address, max_pages=4):
    """Paginate backward to find the oldest signature we can reach
    (capped at max_pages * 1000 to bound cost for very active wallets)."""
    before = None
    last_batch = []
    for _ in range(max_pages):
        params = [address, {"limit": 1000}]
        if before:
            params[1]["before"] = before
        batch = rpc_call("getSignaturesForAddress", params)
        if not batch:
            break
        last_batch = batch
        if len(batch) < 1000:
            return batch[-1], True  # exhausted, true oldest found
        before = batch[-1]["signature"]
        time.sleep(0.2)
    return (last_batch[-1] if last_batch else None), False


def find_funder(address, oldest_sig):
    """Parse the oldest reachable transaction and identify the SOL
    source that funded this address (its own balance increase, paid by
    someone else's decrease)."""
    tx = rpc_call("getTransaction", [oldest_sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}])
    if not tx or not tx.get("meta"):
        return None
    meta = tx["meta"]
    keys = tx["transaction"]["message"]["accountKeys"]
    pubkeys = [k["pubkey"] if isinstance(k, dict) else k for k in keys]
    pre_bal, post_bal = meta.get("preBalances", []), meta.get("postBalances", [])
    fee = meta.get("fee", 0)

    deltas = {}
    for i, pk in enumerate(pubkeys):
        if i < len(pre_bal) and i < len(post_bal):
            d = post_bal[i] - pre_bal[i]
            if i == 0:
                d += fee
            deltas[pk] = deltas.get(pk, 0) + d

    target_delta = deltas.get(address)
    if target_delta is None or target_delta <= 0:
        return {"funder": None, "reason": "target not credited in its oldest tx (likely self-created / fee payer)"}

    payers = [(pk, -d) for pk, d in deltas.items() if pk != address and d < 0]
    if not payers:
        return {"funder": None, "reason": "no counterparty debit found"}
    payers.sort(key=lambda kv: -kv[1])
    funder, amount_lamports = payers[0]
    return {"funder": funder, "amount_sol": amount_lamports / 1e9}


def main():
    report = json.load(open(os.path.join(DATA_DIR, "report.json")))
    mints = list(report["per_token"].keys())
    creators = set()
    for mint in mints:
        p = os.path.join(META_DIR, f"{mint}.json")
        if os.path.exists(p):
            d = json.load(open(p))
            if "__error__" not in d and d.get("creator"):
                creators.add(d["creator"])

    creators = sorted(creators)
    print(f"{len(creators)} créateurs distincts à tracer")
    done = 0
    for c in creators:
        out_path = os.path.join(FUNDING_DIR, f"{c}.json")
        done += 1
        if os.path.exists(out_path):
            continue
        try:
            sig_entry, exhausted = oldest_signature(c)
            if not sig_entry:
                result = {"creator": c, "error": "no signatures found"}
            else:
                funding = find_funder(c, sig_entry["signature"])
                result = {
                    "creator": c,
                    "oldest_signature": sig_entry["signature"],
                    "oldest_blockTime": sig_entry.get("blockTime"),
                    "history_exhausted": exhausted,
                    **(funding or {}),
                }
        except Exception as e:
            result = {"creator": c, "error": str(e)}
        with open(out_path, "w") as f:
            json.dump(result, f)
        if done % 10 == 0:
            print(f"  {done}/{len(creators)}", flush=True)
        time.sleep(0.2)
    print("done")


if __name__ == "__main__":
    main()
