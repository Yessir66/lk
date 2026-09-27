#!/usr/bin/env python3
"""
What is the studied wallet's edge worth for someone slower? Replay its own positions (same tokens,
same size, same holding time) but landing DELAY slots after it, at the best or the worst position
inside the slot, with the calibrated costs (1.25% fee per side, 0.001 SOL per transaction).
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import backtest as B  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
FEE, FIXED = 0.0125, 0.001


def main():
    ledger = json.load(open(os.path.join(DATA, "wallet_ledger.json")))
    cases = []
    for m, v in ledger.items():
        path = B.load_path(m, exclude={B.WALLET})
        if not path or v["status"] != "bought":
            continue
        mine = [t for t in path["trades"] if t["user"] == B.WALLET]
        b = next((t for t in mine if t["type"] == "buy"), None)
        s = next((t for t in mine if t["type"] == "sell"), None)
        if b and s and sum(1 for t in mine if t["type"] == "buy") == 1:
            cases.append((path, b, s))
    print(f"{len(cases)} positions du wallet rejouées (mise identique, même durée de détention)")
    out = {}
    rng = np.random.default_rng(0)
    for label, kw in (("sa position réelle dans le slot", "actual"), ("position aléatoire (moy. 5 tirages)", "random")):
        for delay in (0, 1, 2, 4, 8):
            pnl = []
            for path, b, s in cases:
                size = b["sol"] * (1 + FEE)
                rule = {"kind": "hold", "n": s["slot"] - b["slot"]}
                if kw == "actual":
                    if delay:
                        continue
                    r = B.simulate(path, b["slot"], size, rule, 0, FEE, FIXED, entry_idx=b["idx"], exit_idx=s["idx"])
                    if r:
                        pnl.append(r["pnl"])
                else:
                    draws = [B.simulate(path, b["slot"] + delay, size, rule, 0, FEE, FIXED, in_slot="random", rng=rng)
                             for _ in range(5)]
                    draws = [d["pnl"] for d in draws if d]
                    if draws:
                        pnl.append(float(np.mean(draws)))
            if not pnl:
                continue
            p = np.array(pnl)
            out[f"{label} / +{delay}"] = {"n": len(p), "total": round(float(p.sum()), 2), "mean": round(float(p.mean()), 4)}
            print(f"  {label:32s} retard +{delay} slots: PnL total {p.sum():8.2f} SOL | par trade {p.mean():+.4f} | "
                  f"gagnants {100 * (p > 0).mean():.0f}%")
    json.dump(out, open(os.path.join(DATA, "latency_study.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
