#!/usr/bin/env python3
"""
Turn the first seconds of each sampled launch into features, at several
horizons h (slots after creation, S0 = slot of the creator's first buy,
which is always the first trade).

Leakage control: the wallet's own trades are always excluded, and a
positive is kept for horizon h only if the wallet's buy landed strictly
after S0 + h (so nothing the wallet caused, and nothing copy-traders of
the wallet did, can leak into its features).

Output: data/early_features.json  (one row per (mint, h))
"""
import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
WALLET = "AfPWFykWPZZxU2CyF6BcPoY2v8VEkmYS7ZggvELK7Pv1"
HORIZONS = [0, 1, 2, 3, 5, 7]
SUPPLY = 1_000_000_000


def window_features(trades, creator, s0, h):
    w = [t for t in trades if s0 <= t["slot"] <= s0 + h and t["user"] != WALLET]
    dev_first = next((t for t in trades if t["user"] == creator and t["type"] == "buy"), None)
    buys = [t for t in w if t["type"] == "buy" and t["user"] != creator]
    sells = [t for t in w if t["type"] == "sell" and t["user"] != creator]
    by_buyer = defaultdict(float)
    for t in buys:
        by_buyer[t["user"]] += t["sol"]
    sol_buys = sum(t["sol"] for t in buys)
    sol_sells = sum(t["sol"] for t in sells)
    prices = [t["price"] for t in w if t.get("price")]
    s0_buys = [t for t in buys if t["slot"] == s0]
    first_nondev = min((t["slot"] - s0 for t in buys), default=h + 1)
    dev_sol = dev_first["sol"] if dev_first else 0.0
    return {
        "dev_buy_sol": dev_sol,
        "dev_buy_pct": (dev_first["tokens"] / SUPPLY * 100) if dev_first else 0.0,
        "n_buys_s0": len(s0_buys),
        "sol_buys_s0": sum(t["sol"] for t in s0_buys),
        "n_buys": len(buys),
        "n_buyers": len(by_buyer),
        "sol_buys": sol_buys,
        "n_sells": len(sells),
        "sol_sells": sol_sells,
        "dev_sold": int(any(t["type"] == "sell" and t["user"] == creator for t in w)),
        "max_buy_sol": max((t["sol"] for t in buys), default=0.0),
        "top_buyer_share": (max(by_buyer.values()) / sol_buys) if sol_buys > 0 else 0.0,
        "net_sol": dev_sol + sol_buys - sol_sells,
        "price_change_pct": ((prices[-1] / prices[0] - 1) * 100) if len(prices) >= 2 and prices[0] else 0.0,
        "n_active_slots": len({t["slot"] for t in w}),
        "first_nondev_buy_offset": first_nondev,
        "small_buy_frac": (sum(1 for t in buys if t["sol"] < 0.05) / len(buys)) if buys else 0.0,
        "buyers": sorted(by_buyer),
        "buy_sizes": [round(t["sol"], 4) for t in buys],
    }


def recent_features(trades, creator, s0, d, span=2):
    """What happened in the last `span` slots before the decision slot S0+d."""
    lo = s0 + max(0, d - span)
    w = [t for t in trades if lo <= t["slot"] <= s0 + d and t["user"] not in (WALLET, creator)]
    buys = [t for t in w if t["type"] == "buy"]
    sells = [t for t in w if t["type"] == "sell"]
    return {
        "rec_n_buys": len(buys),
        "rec_sol_buys": sum(t["sol"] for t in buys),
        "rec_n_sells": len(sells),
        "rec_sol_sells": sum(t["sol"] for t in sells),
        "rec_max_buy": max((t["sol"] for t in buys), default=0.0),
    }


LATENCY = 2  # slots between the wallet's decision and its buy landing (assumed minimum)


def main():
    sample = {r["mint"]: r for r in json.load(open(os.path.join(DATA_DIR, "early_sample.json")))}
    ledger = json.load(open(os.path.join(DATA_DIR, "wallet_ledger.json")))
    rows, skipped_leak, no_data = [], defaultdict(int), 0
    aligned = []
    for fn in os.listdir(os.path.join(DATA_DIR, "early_trades")):
        d = json.load(open(os.path.join(DATA_DIR, "early_trades", fn)))
        r = sample.get(d["mint"])
        if not r or not d["trades"] or not d["complete"]:
            no_data += 1
            continue
        tr = d["trades"]
        s0 = tr[0]["slot"]
        label = int(r["status"] in ("bought", "attempted_failed"))
        bot_buy = next((t for t in tr if t["user"] == WALLET and t["type"] == "buy"), None)
        if label and bot_buy:
            k = bot_buy["slot"] - s0
        elif label and d["mint"] in ledger:
            k = ledger[d["mint"]]["first_buy_slot"] - s0
        else:
            k = None
        meta = {"mint": d["mint"], "creator": r["creator"], "created_ts": r["created_ts"],
                "label": label, "k": k, "s0": s0,
                "hour_utc": datetime.fromtimestamp(r["created_ts"], tz=timezone.utc).hour}
        aligned.append((tr, meta))
        for h in HORIZONS:
            if label and (k is None or k <= h):
                skipped_leak[h] += 1
                continue
            f = window_features(tr, r["creator"], s0, h)
            f.update(meta)
            f["h"] = h
            rows.append(f)
    json.dump(rows, open(os.path.join(DATA_DIR, "early_features.json"), "w"))
    print(f"{len(rows)} lignes (mint x horizon) | tokens sans données complètes: {no_data}")
    print("positifs exclus par horizon (bot déjà entré à ou avant S0+h):", dict(skipped_leak))

    # Decision-aligned rows: everything visible up to the wallet's (assumed) decision slot
    # d = k - LATENCY. Skipped launches get a d drawn from the positives' distribution so both
    # classes are observed over windows of the same length.
    pos_d = sorted(max(0, m["k"] - LATENCY) for _, m in aligned if m["label"] and m["k"] is not None)
    out = []
    for tr, m in aligned:
        if m["label"]:
            if m["k"] is None:
                continue
            d = max(0, m["k"] - LATENCY)
        else:
            d = pos_d[int(hashlib.sha256(m["mint"].encode()).hexdigest(), 16) % len(pos_d)]
        f = window_features(tr, m["creator"], m["s0"], d)
        f.update(recent_features(tr, m["creator"], m["s0"], d))
        f.update(m)
        f["d"] = d
        out.append(f)
    json.dump(out, open(os.path.join(DATA_DIR, "aligned_features.json"), "w"))
    print(f"{len(out)} lignes alignées sur la décision (d = k - {LATENCY}), "
          f"positifs={sum(1 for f in out if f['label'])}")


if __name__ == "__main__":
    main()
