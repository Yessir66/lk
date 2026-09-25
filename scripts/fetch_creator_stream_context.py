#!/usr/bin/env python3
"""
The controlled experiment we were missing: for ONE creator's full
stream of launches (bought AND not-bought, from the same time window),
reconstruct the on-chain activity in the first few seconds of each
token — dev buy, distinct early buyers, transaction count — and compare
the two groups. Everything up to now compared bought tokens to each
other; this compares bought vs skipped from the identical source.

Usage: fetch_creator_stream_context.py <creator_address>
Resumable cache: data/stream_context/<creator>/<mint>.json
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
LAUNCHES_FILE = os.path.join(DATA_DIR, "top_creator_launches.json")
RPC_URL = "https://api.mainnet-beta.solana.com"

EARLY_WINDOW_S = 5  # how many seconds after creation to analyze


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


def parse_generic_deltas(tx):
    if not tx or not tx.get("meta") or tx["meta"].get("err") is not None:
        return None
    meta = tx["meta"]
    owner_token_deltas = defaultdict(lambda: defaultdict(float))

    def index_by_owner(entries):
        out = {}
        for e in entries or []:
            out[e["accountIndex"]] = e
        return out

    pre_tb = index_by_owner(meta.get("preTokenBalances"))
    post_tb = index_by_owner(meta.get("postTokenBalances"))
    for i in set(pre_tb) | set(post_tb):
        pre_e, post_e = pre_tb.get(i), post_tb.get(i)
        e = post_e or pre_e
        owner = e.get("owner")
        mint = e["mint"]
        pre_amt = float(pre_e["uiTokenAmount"]["uiAmountString"]) if pre_e and pre_e["uiTokenAmount"]["uiAmountString"] else 0.0
        post_amt = float(post_e["uiTokenAmount"]["uiAmountString"]) if post_e and post_e["uiTokenAmount"]["uiAmountString"] else 0.0
        d = post_amt - pre_amt
        if owner and abs(d) > 1e-9:
            owner_token_deltas[owner][mint] += d

    pre_bal, post_bal = meta.get("preBalances", []), meta.get("postBalances", [])
    fee = meta.get("fee", 0)
    return owner_token_deltas, pre_bal, post_bal, fee


def process_token(coin, out_dir, wallet):
    mint = coin["mint"]
    out_path = os.path.join(out_dir, f"{mint}.json")
    if os.path.exists(out_path):
        return
    bonding_curve = coin.get("bonding_curve")
    creator = coin.get("creator")
    created_ts = coin["created_timestamp"] / 1000
    if not bonding_curve:
        return

    sigs = rpc_call("getSignaturesForAddress", [bonding_curve, {"limit": 1000}])
    early_sigs = [s for s in sigs if s.get("blockTime") and s["blockTime"] <= created_ts + EARLY_WINDOW_S]
    early_sigs.sort(key=lambda s: s.get("slot", 0))

    dev_buy_sol = None
    distinct_buyers = set()
    wallet_bought = False
    for s in early_sigs[:20]:
        tx = rpc_call("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}])
        parsed = parse_generic_deltas(tx)
        time.sleep(0.15)
        if not parsed:
            continue
        owner_token_deltas, pre_bal, post_bal, fee = parsed
        for owner, deltas in owner_token_deltas.items():
            token_delta = deltas.get(mint, 0)
            if token_delta > 1e-9:
                distinct_buyers.add(owner)
                if owner == creator and dev_buy_sol is None:
                    dev_buy_sol = "detected"
                if owner == wallet:
                    wallet_bought = True

    result = {
        "mint": mint,
        "created_timestamp": created_ts,
        "early_tx_count": len(early_sigs),
        "early_tx_capped": len(sigs) >= 1000,
        "distinct_early_buyers": len(distinct_buyers),
        "dev_buy_detected": dev_buy_sol is not None,
        "wallet_bought_this_token": wallet_bought,
    }
    with open(out_path, "w") as f:
        json.dump(result, f)


def main():
    creator = sys.argv[1] if len(sys.argv) > 1 else "C2TFeiRyzzApUxvjrfaXF1RLfad2SfaHvSqGaEPmmxfv"
    wallet = sys.argv[2] if len(sys.argv) > 2 else "AfPWFykWPZZxU2CyF6BcPoY2v8VEkmYS7ZggvELK7Pv1"

    launches = json.load(open(LAUNCHES_FILE))
    coins = launches.get(creator)
    if not coins:
        print(f"Pas de données de lancement en cache pour {creator}")
        return

    out_dir = os.path.join(DATA_DIR, "stream_context", creator)
    os.makedirs(out_dir, exist_ok=True)

    print(f"{len(coins)} tokens de {creator} à analyser (fenêtre {EARLY_WINDOW_S}s après création)")
    done, failed = 0, 0
    for coin in coins:
        try:
            process_token(coin, out_dir, wallet)
        except Exception as e:
            failed += 1
            print(f"  ERROR mint={coin['mint']}: {e}", flush=True)
        done += 1
        if done % 10 == 0:
            print(f"  {done}/{len(coins)} ({failed} erreurs)", flush=True)
        time.sleep(0.1)
    print(f"done ({failed} erreurs)")


if __name__ == "__main__":
    main()
