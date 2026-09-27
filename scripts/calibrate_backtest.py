#!/usr/bin/env python3
"""
Calibration of backtest.py: replay the studied wallet's own positions (same buy slot, same size, same
sell slot, its own trades removed from the path) and compare the simulated PnL with its real PnL
(wallet balance change from the ledger, all fees and tips included). Fits the fee/fixed-cost setting
that reproduces it.
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import backtest as B  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")


def main():
    ledger = json.load(open(os.path.join(DATA, "wallet_ledger.json")))
    cases = []
    for m, v in ledger.items():
        path = B.load_path(m, exclude={B.WALLET})
        if not path or v["status"] != "bought" or v.get("pnl_sol") is None:
            continue
        mine = [t for t in path["trades"] if t["user"] == B.WALLET]
        b = next((t for t in mine if t["type"] == "buy"), None)
        s = next((t for t in mine if t["type"] == "sell"), None)
        if not b or not s or sum(1 for t in mine if t["type"] == "buy") > 1:
            continue  # single buy / single sell positions only
        cases.append((m, path, b, s, v))
    print(f"{len(cases)} positions du wallet rejouables")
    real = np.array([c[4]["pnl_sol"] for c in cases])
    pump = np.array([c[3]["sol"] - c[2]["sol"] for c in cases])  # pump.fun amounts (net of pump.fun fee)
    print(f"PnL réel (solde du wallet): total {real.sum():.3f} SOL | selon les montants pump.fun: {pump.sum():.3f} SOL "
          f"-> coûts hors pump.fun ~{(pump - real).mean():.4f} SOL par position")
    for fee in (0.0, 0.01, 0.0125):
        for fixed in (0.0, 0.001, 0.002):
            sim = []
            for m, path, b, s, v in cases:
                r = B.simulate(path, b["slot"], c_size(b, fee), {"kind": "hold", "n": s["slot"] - b["slot"]}, 0, fee, fixed,
                               entry_idx=b["idx"], exit_idx=s["idx"])
                sim.append(r["pnl"] if r else np.nan)
            sim = np.array(sim)
            ok = ~np.isnan(sim)
            print(f"  frais {fee:.4f} + fixe {fixed:.3f}: simulé {sim[ok].sum():8.3f} SOL vs réel {real[ok].sum():8.3f} | "
                  f"écart moyen {np.mean(sim[ok] - real[ok]):+.4f} | corrélation {np.corrcoef(sim[ok], real[ok])[0, 1]:.3f}")


def c_size(b, fee):
    """SOL the wallet put in, fee included (pump.fun amountSol is what reached the curve)."""
    return b["sol"] * (1 + fee)


if __name__ == "__main__":
    main()
