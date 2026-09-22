#!/usr/bin/env python3
"""
Reconstruct the DECISION-MAKING behaviour of a Solana wallet from its raw
on-chain transaction history (no price/PnL — only *when*, *how much*,
*how often*, and *through which venue* the wallet trades, so the pattern
can be reproduced).

Reads cached transactions from data/tx/*.json (produced by
fetch_wallet_txs.py), reconstructs buy/sell events per token, enriches
each token with its pool-creation time (DexScreener) to measure "sniper
delay", and prints a behavioural report + writes data/report.json.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone

WALLET = sys.argv[1] if len(sys.argv) > 1 else "AfPWFykWPZZxU2CyF6BcPoY2v8VEkmYS7ZggvELK7Pv1"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
TX_DIR = os.path.join(DATA_DIR, "tx")

WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
QUOTE_MINTS = {WSOL, USDC, USDT}

PROGRAMS = {
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "pump.fun (bonding curve)",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": "pump.fun AMM (migrated)",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "Raydium AMM v4",
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": "Raydium CPMM",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "Jupiter aggregator",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB": "Jupiter aggregator v4",
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "Meteora DLMM",
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB": "Meteora pools",
    "MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG": "Moonshot",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "Orca Whirlpool",
}


def load_transactions():
    files = sorted(os.listdir(TX_DIR)) if os.path.isdir(TX_DIR) else []
    txs = []
    for fn in files:
        with open(os.path.join(TX_DIR, fn)) as f:
            data = json.load(f)
        if data:
            txs.append((fn[:-5], data))
    return txs


def account_index(tx, wallet):
    keys = tx["transaction"]["message"]["accountKeys"]
    for i, k in enumerate(keys):
        pk = k["pubkey"] if isinstance(k, dict) else k
        if pk == wallet:
            return i
    return None


def collect_program_ids(tx):
    ids = set()
    msg = tx["transaction"]["message"]
    for ix in msg.get("instructions", []):
        pid = ix.get("programId")
        if pid:
            ids.add(pid)
    for inner in (tx.get("meta") or {}).get("innerInstructions", []) or []:
        for ix in inner.get("instructions", []):
            pid = ix.get("programId")
            if pid:
                ids.add(pid)
    return ids


def parse_tx(sig, tx, wallet):
    if not tx or not tx.get("meta") or tx["meta"].get("err") is not None:
        return None
    meta = tx["meta"]
    block_time = tx.get("blockTime")
    idx = account_index(tx, wallet)
    if idx is None:
        return None

    deltas = defaultdict(float)  # mint -> ui amount delta (positive = received)

    pre_bal = meta.get("preBalances", [])
    post_bal = meta.get("postBalances", [])
    if idx < len(pre_bal) and idx < len(post_bal):
        lamports_delta = post_bal[idx] - pre_bal[idx]
        if idx == 0:
            lamports_delta += meta.get("fee", 0)
        deltas[WSOL] += lamports_delta / 1e9

    def index_by_owner(entries):
        out = {}
        for e in entries or []:
            if e.get("owner") == wallet:
                out[e["accountIndex"]] = e
        return out

    pre_tb = index_by_owner(meta.get("preTokenBalances"))
    post_tb = index_by_owner(meta.get("postTokenBalances"))
    all_idx = set(pre_tb) | set(post_tb)
    for i in all_idx:
        pre_e = pre_tb.get(i)
        post_e = post_tb.get(i)
        mint = (post_e or pre_e)["mint"]
        pre_amt = float(pre_e["uiTokenAmount"]["uiAmountString"]) if pre_e and pre_e["uiTokenAmount"]["uiAmountString"] else 0.0
        post_amt = float(post_e["uiTokenAmount"]["uiAmountString"]) if post_e and post_e["uiTokenAmount"]["uiAmountString"] else 0.0
        deltas[mint] += post_amt - pre_amt

    deltas = {m: d for m, d in deltas.items() if abs(d) > 1e-9}
    if len(deltas) < 2:
        return None

    quote_delta = sum(d for m, d in deltas.items() if m in QUOTE_MINTS)
    asset_deltas = {m: d for m, d in deltas.items() if m not in QUOTE_MINTS}
    if not asset_deltas:
        return None

    mint, amt = max(asset_deltas.items(), key=lambda kv: abs(kv[1]))
    if quote_delta < 0 and amt > 0:
        side = "BUY"
    elif quote_delta > 0 and amt < 0:
        side = "SELL"
    else:
        side = "SWAP"

    programs = collect_program_ids(tx)
    venue = next((PROGRAMS[p] for p in programs if p in PROGRAMS), "unknown/other")

    return {
        "sig": sig,
        "blockTime": block_time,
        "slot": tx.get("slot"),
        "side": side,
        "mint": mint,
        "token_amount": amt,
        "quote_amount_sol": quote_delta,
        "venue": venue,
        "num_other_legs": len(asset_deltas) - 1,
    }


def fetch_pair_created_at(mint, cache):
    if mint in cache:
        return cache[mint]
    url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
        pairs = data.get("pairs") or []
        created = min((p["pairCreatedAt"] for p in pairs if p.get("pairCreatedAt")), default=None)
        cache[mint] = created / 1000 if created else None
    except Exception:
        cache[mint] = None
    return cache[mint]


def fmt_ts(ts):
    if ts is None:
        return "?"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def human_dur(seconds):
    if seconds is None:
        return "?"
    seconds = abs(seconds)
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds/60:.1f}min"
    if seconds < 86400:
        return f"{seconds/3600:.1f}h"
    return f"{seconds/86400:.1f}j"


def main():
    print(f"Wallet analyse: {WALLET}")
    raw = load_transactions()
    print(f"{len(raw)} transactions en cache")

    events = []
    skipped = 0
    for sig, tx in raw:
        ev = parse_tx(sig, tx, WALLET)
        if ev:
            events.append(ev)
        else:
            skipped += 1
    events.sort(key=lambda e: e["blockTime"] or 0)
    print(f"{len(events)} évènements de swap identifiés ({skipped} tx ignorées: transferts/échecs/non-swap)")

    per_token = defaultdict(lambda: {"buys": [], "sells": []})
    for e in events:
        if e["side"] == "BUY":
            per_token[e["mint"]]["buys"].append(e)
        elif e["side"] == "SELL":
            per_token[e["mint"]]["sells"].append(e)

    mints = list(per_token.keys())
    print(f"{len(mints)} tokens distincts échangés")

    cache_path = os.path.join(DATA_DIR, "pair_created_cache.json")
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            cache = json.load(f)

    print("Récupération de la date de création de pool (DexScreener) pour chaque token...")
    for i, mint in enumerate(mints):
        fetch_pair_created_at(mint, cache)
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(mints)}")
            with open(cache_path, "w") as f:
                json.dump(cache, f)
        time.sleep(0.15)
    with open(cache_path, "w") as f:
        json.dump(cache, f)

    # ---- per-token behavioural stats ----
    token_stats = {}
    sniper_delays = []
    hold_durations = []
    position_sizes_sol = []
    reentries = 0
    scaled_out = 0
    single_shot_sell = 0
    never_sold = 0

    for mint, d in per_token.items():
        buys = sorted(d["buys"], key=lambda e: e["blockTime"] or 0)
        sells = sorted(d["sells"], key=lambda e: e["blockTime"] or 0)
        if not buys:
            continue
        first_buy = buys[0]
        pool_created = cache.get(mint)
        sniper_delay = (first_buy["blockTime"] - pool_created) if pool_created else None
        if sniper_delay is not None:
            sniper_delays.append(sniper_delay)

        first_buy_cost = abs(first_buy["quote_amount_sol"])
        position_sizes_sol.append(first_buy_cost)

        if len(buys) > 1:
            reentries += 1
        if len(sells) > 1:
            scaled_out += 1
        elif len(sells) == 1:
            single_shot_sell += 1
        if not sells:
            never_sold += 1

        hold = None
        if sells:
            hold = sells[0]["blockTime"] - first_buy["blockTime"]
            hold_durations.append(hold)

        venues_buy = defaultdict(int)
        for b in buys:
            venues_buy[b["venue"]] += 1

        token_stats[mint] = {
            "num_buys": len(buys),
            "num_sells": len(sells),
            "first_buy_time": fmt_ts(first_buy["blockTime"]),
            "pool_created_time": fmt_ts(pool_created),
            "sniper_delay_s": sniper_delay,
            "first_position_sol": round(first_buy_cost, 4),
            "hold_to_first_sell_s": hold,
            "still_holding": len(sells) == 0,
            "buy_venues": dict(venues_buy),
        }

    # ---- clustering: how many distinct new tokens bought within the same 5-min window (parallel sniping) ----
    first_buy_times = sorted(
        per_token[m]["buys"][0]["blockTime"]
        for m in per_token if per_token[m]["buys"]
    )
    clusters = 0
    i = 0
    cluster_sizes = []
    while i < len(first_buy_times):
        j = i
        while j + 1 < len(first_buy_times) and first_buy_times[j + 1] - first_buy_times[i] <= 300:
            j += 1
        size = j - i + 1
        if size > 1:
            clusters += 1
            cluster_sizes.append(size)
        i = j + 1

    # ---- venue usage overall ----
    venue_counts = defaultdict(int)
    for e in events:
        if e["side"] == "BUY":
            venue_counts[e["venue"]] += 1

    def stats(vals):
        if not vals:
            return None
        vals = sorted(vals)
        n = len(vals)
        mean = sum(vals) / n
        median = vals[n // 2]
        return {"n": n, "mean": mean, "median": median, "min": vals[0], "max": vals[-1]}

    report = {
        "wallet": WALLET,
        "total_swap_events": len(events),
        "distinct_tokens": len(mints),
        "tokens_never_sold_pct": round(100 * never_sold / max(len(mints), 1), 1),
        "tokens_reentered_pct": round(100 * reentries / max(len(mints), 1), 1),
        "tokens_scaled_out_pct": round(100 * scaled_out / max(len(mints), 1), 1),
        "tokens_single_shot_sell_pct": round(100 * single_shot_sell / max(len(mints), 1), 1),
        "sniper_delay_seconds": stats(sniper_delays),
        "hold_duration_seconds": stats(hold_durations),
        "position_size_sol": stats(position_sizes_sol),
        "parallel_buy_clusters_5min": clusters,
        "avg_tokens_per_cluster": (sum(cluster_sizes) / len(cluster_sizes)) if cluster_sizes else None,
        "buy_venue_distribution": dict(venue_counts),
        "per_token": token_stats,
    }

    with open(os.path.join(DATA_DIR, "report.json"), "w") as f:
        json.dump(report, f, indent=2, default=str)

    # ---- human readable summary ----
    print("\n" + "=" * 70)
    print("RAPPORT COMPORTEMENTAL — schéma de décision d'achat/vente")
    print("=" * 70)
    print(f"Tokens distincts tradés         : {len(mints)}")
    print(f"Événements buy/sell détectés    : {len(events)}")
    print(f"Jamais revendu (still holding)  : {never_sold} ({report['tokens_never_sold_pct']}%)")
    print(f"Ré-achète le même token         : {reentries} ({report['tokens_reentered_pct']}%)")
    print(f"Sort en plusieurs fois (scale)  : {scaled_out} ({report['tokens_scaled_out_pct']}%)")
    print(f"Sort en une fois (dump unique)  : {single_shot_sell} ({report['tokens_single_shot_sell_pct']}%)")

    if report["sniper_delay_seconds"]:
        s = report["sniper_delay_seconds"]
        print(f"\nDélai pool-création -> 1er achat : médiane {human_dur(s['median'])}, "
              f"moyenne {human_dur(s['mean'])}, min {human_dur(s['min'])}, max {human_dur(s['max'])} (n={s['n']})")
    if report["hold_duration_seconds"]:
        h = report["hold_duration_seconds"]
        print(f"Durée de détention avant 1ère vente : médiane {human_dur(h['median'])}, "
              f"moyenne {human_dur(h['mean'])} (n={h['n']})")
    if report["position_size_sol"]:
        p = report["position_size_sol"]
        print(f"Taille de position (SOL, 1er achat) : médiane {p['median']:.3f}, "
              f"moyenne {p['mean']:.3f}, min {p['min']:.3f}, max {p['max']:.3f} (n={p['n']})")
    print(f"\nAchats groupés (≥2 nouveaux tokens en <5min) : {clusters} clusters, "
          f"taille moy {report['avg_tokens_per_cluster']:.1f} tokens/cluster" if cluster_sizes else "\nPas de sniping groupé détecté")
    print("\nVenues utilisées pour les achats :")
    for v, c in sorted(venue_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {v:30s} {c}")

    print(f"\nRapport JSON complet : data/report.json")


if __name__ == "__main__":
    main()
