#!/usr/bin/env python3
"""
Reconstruct the on-chain state of each bonding curve at the exact moment
the wallet bought: how many transactions (dev buy, other snipers) already
happened on that token before our wallet's buy, and how big the
creator's own initial buy was. This is the "hidden filter" check for
tokens whose creator only appears once in our sample — was the wallet
reacting to early traction (a dev buy, other buyers) rather than / in
addition to a known-creator whitelist?

Resumable cache: data/pre_buy/<mint>.json
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from analyze import parse_tx, WALLET as DEFAULT_WALLET, load_transactions  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
TX_DIR = os.path.join(DATA_DIR, "tx")
META_DIR = os.path.join(DATA_DIR, "coin_meta")
PRE_BUY_DIR = os.path.join(DATA_DIR, "pre_buy")
os.makedirs(PRE_BUY_DIR, exist_ok=True)

RPC_URL = "https://api.mainnet-beta.solana.com"
WALLET = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_WALLET
MAX_PRIOR_TO_FETCH = 12  # cap per-token detailed parsing cost


def rpc_call(method, params, retries=6):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for attempt in range(retries):
        req = urllib.request.Request(RPC_URL, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
                if "error" in body:
                    raise RuntimeError(body["error"])
                return body["result"]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(min(2 ** attempt, 20))
                continue
            raise
        except Exception:
            time.sleep(min(1.5 ** attempt, 15))
    raise RuntimeError(f"RPC call {method} failed after retries")


def get_first_buys():
    """mint -> first BUY event (with signature) for our wallet, oldest first."""
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


def parse_generic_deltas(tx):
    """Return {owner: {mint: delta_ui}} for every token-account owner with
    a nonzero balance change in this transaction, plus native SOL deltas
    keyed by each account's own pubkey under mint 'SOL_NATIVE'."""
    if not tx or not tx.get("meta") or tx["meta"].get("err") is not None:
        return None, None
    meta = tx["meta"]
    keys = tx["transaction"]["message"]["accountKeys"]
    pubkeys = [k["pubkey"] if isinstance(k, dict) else k for k in keys]

    sol_deltas = defaultdict(float)
    pre_bal, post_bal = meta.get("preBalances", []), meta.get("postBalances", [])
    fee = meta.get("fee", 0)
    for i, pk in enumerate(pubkeys):
        if i < len(pre_bal) and i < len(post_bal):
            d = post_bal[i] - pre_bal[i]
            if i == 0:
                d += fee
            if d != 0:
                sol_deltas[pk] += d / 1e9

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

    return sol_deltas, owner_token_deltas


def analyze_prior_tx(tx, mint, creator):
    """Classify one prior transaction on the bonding curve: who traded,
    how much SOL, and whether it was the creator's own buy."""
    sol_deltas, owner_token_deltas = parse_generic_deltas(tx)
    if sol_deltas is None:
        return None
    buyers = []
    for owner, deltas in owner_token_deltas.items():
        token_delta = deltas.get(mint, 0)
        if abs(token_delta) < 1e-9:
            continue
        sol_side = sol_deltas.get(owner, 0)
        side = "BUY" if token_delta > 0 else "SELL"
        buyers.append({"owner": owner, "side": side, "sol": abs(sol_side), "is_creator": owner == creator})
    return buyers


def process_mint(mint, first_buy_event):
    out_path = os.path.join(PRE_BUY_DIR, f"{mint}.json")
    if os.path.exists(out_path):
        return
    meta_path = os.path.join(META_DIR, f"{mint}.json")
    if not os.path.exists(meta_path):
        return
    meta = json.load(open(meta_path))
    if "__error__" in meta:
        return
    bonding_curve = meta.get("bonding_curve")
    creator = meta.get("creator")
    if not bonding_curve:
        return

    sigs = rpc_call("getSignaturesForAddress", [bonding_curve, {"before": first_buy_event["sig"], "limit": 100}])
    prior_count = len(sigs)
    result = {
        "mint": mint,
        "creator": creator,
        "prior_tx_count": prior_count,
        "prior_events": [],
        "dev_buy_sol": None,
        "distinct_prior_buyers": 0,
    }

    to_fetch = sigs[:MAX_PRIOR_TO_FETCH]
    to_fetch.reverse()  # oldest first among the fetched subset
    distinct_buyers = set()
    for s in to_fetch:
        tx = rpc_call("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        events = analyze_prior_tx(tx, mint, creator)
        time.sleep(0.2)
        if not events:
            continue
        for ev in events:
            result["prior_events"].append(ev)
            if ev["side"] == "BUY":
                distinct_buyers.add(ev["owner"])
                if ev["is_creator"] and result["dev_buy_sol"] is None:
                    result["dev_buy_sol"] = ev["sol"]
    result["distinct_prior_buyers"] = len(distinct_buyers)

    with open(out_path, "w") as f:
        json.dump(result, f)


def main():
    first_buys = get_first_buys()
    print(f"{len(first_buys)} tokens à traiter")
    done = 0
    for mint, ev in first_buys.items():
        process_mint(mint, ev)
        done += 1
        if done % 10 == 0:
            print(f"  {done}/{len(first_buys)}", flush=True)
        time.sleep(0.15)
    print("done")


if __name__ == "__main__":
    main()
