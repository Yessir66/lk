#!/usr/bin/env python3
"""
For tokens the wallet did NOT buy, walk the bonding curve's signature
history backward page by page until reaching genesis (the token's very
first transaction — normally the pump.fun "create" instruction, often
bundled with the creator's own initial buy). Tokens that need more than
MAX_PAGES to reach genesis (too much trading history) are abandoned
rather than forced through, per instruction: don't insist on the
heavily-traded ones.

Logs progress token by token, page by page, to stdout (redirect to a
file to keep a full record) and caches per-mint results so re-runs
resume cleanly.

Usage: fetch_skipped_genesis_context.py <creator_address>
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

MAX_PAGES = 8          # abandon a token if genesis isn't reached within this many pages
EARLY_WINDOW_S = 5      # how many seconds after creation to analyze once we have genesis


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
    return owner_token_deltas


def find_genesis_page(bonding_curve, created_ts, mint_label):
    """Walk backward page by page. Returns (page_of_sigs_at_genesis, pages_used, reached)
    where page_of_sigs_at_genesis is oldest-first."""
    before = None
    for page in range(1, MAX_PAGES + 1):
        params = [bonding_curve, {"limit": 1000}]
        if before:
            params[1]["before"] = before
        batch = rpc_call("getSignaturesForAddress", params)
        if not batch:
            print(f"    page {page}: 0 résultat -> génèse déjà dépassée (compte vide)")
            return [], page, True
        oldest_bt = batch[-1].get("blockTime")
        newest_bt = batch[0].get("blockTime")
        print(f"    page {page}: {len(batch)} sigs, de {oldest_bt} à {newest_bt}"
              + (f"  (créé à {created_ts:.0f})" if page == 1 else ""))
        if len(batch) < 1000:
            print(f"    -> page partielle: génèse atteinte à la page {page}")
            return list(reversed(batch)), page, True
        if oldest_bt and oldest_bt <= created_ts + EARLY_WINDOW_S + 5:
            print(f"    -> fenêtre de création atteinte à la page {page}")
            return list(reversed(batch)), page, True
        before = batch[-1]["signature"]
        time.sleep(0.2)
    print(f"    -> abandonné après {MAX_PAGES} pages sans atteindre la création (trop d'historique)")
    return None, MAX_PAGES, False


def process_token(coin, out_dir, idx, total):
    mint = coin["mint"]
    out_path = os.path.join(out_dir, f"{mint}.json")
    if os.path.exists(out_path):
        print(f"[{idx}/{total}] {mint}: déjà en cache, skip")
        return "cached"

    bonding_curve = coin.get("bonding_curve")
    creator = coin.get("creator")
    created_ts = coin["created_timestamp"] / 1000
    print(f"[{idx}/{total}] {mint} (créé à {created_ts:.0f})")

    if not bonding_curve:
        return "no_bonding_curve"

    genesis_sigs, pages_used, reached = find_genesis_page(bonding_curve, created_ts, mint)
    if not reached:
        result = {"mint": mint, "abandoned": True, "pages_tried": pages_used}
        with open(out_path, "w") as f:
            json.dump(result, f)
        return "abandoned"

    early_sigs = [s for s in genesis_sigs if s.get("blockTime") and s["blockTime"] <= created_ts + EARLY_WINDOW_S]

    dev_buy_detected = False
    distinct_buyers = set()
    for s in early_sigs[:20]:
        tx = rpc_call("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}])
        deltas = parse_generic_deltas(tx)
        time.sleep(0.15)
        if not deltas:
            continue
        for owner, dd in deltas.items():
            if dd.get(mint, 0) > 1e-9:
                distinct_buyers.add(owner)
                if owner == creator:
                    dev_buy_detected = True

    result = {
        "mint": mint,
        "abandoned": False,
        "pages_used_to_reach_genesis": pages_used,
        "early_tx_count": len(early_sigs),
        "distinct_early_buyers": len(distinct_buyers),
        "dev_buy_detected": dev_buy_detected,
    }
    with open(out_path, "w") as f:
        json.dump(result, f)
    print(f"    -> OK: {len(early_sigs)} tx dans les {EARLY_WINDOW_S}s, {len(distinct_buyers)} acheteurs distincts, "
          f"dev_buy={dev_buy_detected}")
    return "ok"


def main():
    creator = sys.argv[1] if len(sys.argv) > 1 else "C2TFeiRyzzApUxvjrfaXF1RLfad2SfaHvSqGaEPmmxfv"
    wallet = "AfPWFykWPZZxU2CyF6BcPoY2v8VEkmYS7ZggvELK7Pv1"

    launches = json.load(open(LAUNCHES_FILE))
    coins = launches[creator]

    report = json.load(open(os.path.join(DATA_DIR, "report.json")))
    bought_mints = set(report["per_token"].keys())
    skipped = [c for c in coins if c["mint"] not in bought_mints]

    out_dir = os.path.join(DATA_DIR, "skipped_genesis", creator)
    os.makedirs(out_dir, exist_ok=True)

    print(f"{len(skipped)} tokens IGNORÉS à traiter pour {creator}")
    print(f"(plafond de {MAX_PAGES} pages par token; au-delà -> abandon, pas d'insistance)\n")

    outcomes = defaultdict(int)
    for i, coin in enumerate(skipped, 1):
        outcome = process_token(coin, out_dir, i, len(skipped))
        outcomes[outcome] += 1
        print()

    print("=" * 60)
    print("RÉSUMÉ:", dict(outcomes))


if __name__ == "__main__":
    main()
