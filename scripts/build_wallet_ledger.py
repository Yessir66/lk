#!/usr/bin/env python3
"""
Per-mint ledger of the wallet's decisions, rebuilt from the raw tx cache:
first buy (slot/time/sig/SOL), sells, realized PnL, and buy attempts that
landed but failed (decided-to-buy tokens that were never actually bought).

Output: data/wallet_ledger.json
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from analyze import parse_tx, WALLET  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
TX_DIR = os.path.join(DATA_DIR, "tx")
BOT_SELL_PROGRAM = "6HSS4x6UpesV8aPYmteEqSmEgkNwAD4gb6fyRDJUvMC6"
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


def main():
    ledger = {}
    failed_buys = []
    for fn in os.listdir(TX_DIR):
        tx = json.load(open(os.path.join(TX_DIR, fn)))
        if not tx or not tx.get("meta"):
            continue
        sig = fn[:-5]
        slot, bt = tx.get("slot"), tx.get("blockTime")
        if tx["meta"].get("err") is None:
            ev = parse_tx(sig, tx, WALLET)
            if not ev:
                continue
            e = ledger.setdefault(ev["mint"], {"buys": [], "sells": []})
            rec = {"sig": sig, "slot": slot, "bt": bt, "sol": ev["quote_amount_sol"]}
            if ev["side"] == "BUY":
                e["buys"].append(rec)
            elif ev["side"] == "SELL":
                e["sells"].append(rec)
            continue

        ixs = tx["transaction"]["message"]["instructions"]
        progs = {ix.get("programId") for ix in ixs}
        if BOT_SELL_PROGRAM in progs:
            continue  # redundant sell copies after the real sell landed
        keys = [k["pubkey"] if isinstance(k, dict) else k for k in tx["transaction"]["message"]["accountKeys"]]
        if PUMP_PROGRAM not in keys and not any(p and p.startswith("FEzQL2") for p in progs):
            continue
        mint = next((k for k in keys if k.endswith("pump")), None)
        if mint:
            failed_buys.append({"mint": mint, "sig": sig, "slot": slot, "bt": bt})

    out = {}
    for mint, e in ledger.items():
        buys = sorted(e["buys"], key=lambda r: r["slot"])
        sells = sorted(e["sells"], key=lambda r: r["slot"])
        if not buys:
            continue
        out[mint] = {
            "status": "bought",
            "first_buy_slot": buys[0]["slot"], "first_buy_bt": buys[0]["bt"], "first_buy_sig": buys[0]["sig"],
            "buy_sol": round(-sum(b["sol"] for b in buys), 6),
            "first_sell_slot": sells[0]["slot"] if sells else None,
            "last_sell_bt": sells[-1]["bt"] if sells else None,
            "sell_sol": round(sum(s["sol"] for s in sells), 6),
            "pnl_sol": round(sum(s["sol"] for s in sells) + sum(b["sol"] for b in buys), 6) if sells else None,
        }
    for f in sorted(failed_buys, key=lambda r: r["slot"]):
        if f["mint"] in out:
            continue
        out[f["mint"]] = {"status": "attempted_failed", "first_buy_slot": f["slot"], "first_buy_bt": f["bt"],
                          "first_buy_sig": f["sig"]}

    json.dump(out, open(os.path.join(DATA_DIR, "wallet_ledger.json"), "w"), indent=1)
    n_b = sum(1 for v in out.values() if v["status"] == "bought")
    n_f = sum(1 for v in out.values() if v["status"] == "attempted_failed")
    pnls = [v["pnl_sol"] for v in out.values() if v.get("pnl_sol") is not None]
    print(f"achetés: {n_b} | tentés-échoués: {n_f} | PnL réalisé total: {sum(pnls):.3f} SOL sur {len(pnls)} trades "
          f"({sum(1 for p in pnls if p > 0)} gagnants)")


if __name__ == "__main__":
    main()
