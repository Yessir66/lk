#!/usr/bin/env python3
"""
Step 3a: which exit rules survive ordinary execution? Entries = the studied wallet's own buys, landing
DELAY slots after it at a random position in the slot (same random draws for every rule: common random
numbers), size 0.5 SOL, calibrated costs. Rules are chosen on the oldest 60% of positions and checked on
the newest 40%.
Output: data/exit_search.json
"""
import hashlib
import itertools
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import backtest as B  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
FEE, FIXED, SIZE = 0.0125, 0.001, 0.5
MAX_SLOTS = 80


def rules():
    out = [{"kind": "hold", "n": n} for n in (3, 5, 7, 10, 15, 25, 40, 60)]
    out += [{"kind": "tpsl", "tp": tp, "sl": sl, "max_slots": mx}
            for tp, sl, mx in itertools.product((0.1, 0.2, 0.3, 0.5, 1.0), (0.05, 0.1, 0.2, 0.3), (25, 50, 75))]
    out += [{"kind": "trail", "trail": tr, "min_slots": mn, "max_slots": mx}
            for tr, mn, mx in itertools.product((0.05, 0.1, 0.15, 0.2, 0.3), (0, 3, 7), (25, 50, 75))]
    return out


def name(r):
    if r["kind"] == "hold":
        return f"durée {r['n']} slots"
    if r["kind"] == "tpsl":
        return f"TP +{int(100 * r['tp'])}% / SL -{int(100 * r['sl'])}% / max {r['max_slots']}"
    return f"suiveur -{int(100 * r['trail'])}% (après {r['min_slots']}) / max {r['max_slots']}"


class Case:
    """One entry: curve, our fill, per-slot prices while holding, deterministic random in-slot positions."""

    def __init__(self, mint, path, entry_slot, delay):
        self.curve = B.Curve(path["trades_ex"])
        self.mint = mint
        self.land = entry_slot + delay
        self.delay = delay
        rng = self._rng("entry")
        vt = self.curve.vt_at(self.land, self.curve.random_idx(self.land, rng))
        self.tokens, _ = B.buy(vt, SIZE, FEE)
        self.entry_px = SIZE / self.tokens
        self.last = path["trades_ex"][-1]["slot"]
        self.px = {s: B.price(self.curve.vt_at(s) - self.tokens) for s in range(self.land + 1, min(self.last, self.land + MAX_SLOTS) + 1)}

    def _rng(self, tag):
        return np.random.default_rng(int(hashlib.sha256(f"{self.mint}{tag}".encode()).hexdigest()[:12], 16))

    def pnl(self, rule):
        peak, decide = self.entry_px, None
        for s in range(self.land + 1, min(self.last, self.land + MAX_SLOTS) + 1):
            px = self.px[s]
            peak = max(peak, px)
            held = s - self.land
            k = rule["kind"]
            if (k == "hold" and held >= rule["n"]) or \
               (k == "tpsl" and (px >= self.entry_px * (1 + rule["tp"]) or px <= self.entry_px * (1 - rule["sl"])
                                 or held >= rule["max_slots"])) or \
               (k == "trail" and ((px <= peak * (1 - rule["trail"]) and held >= rule["min_slots"]) or held >= rule["max_slots"])):
                decide = s
                break
        if decide is None:
            decide = min(self.last, self.land + MAX_SLOTS)
        ex = decide + self.delay
        vt = self.curve.vt_at(ex, self.curve.random_idx(ex, self._rng(f"exit{ex}")))
        out, _ = B.sell(vt - self.tokens, self.tokens, FEE)
        return out - SIZE - 2 * FIXED


def main():
    ledger = json.load(open(os.path.join(DATA, "wallet_ledger.json")))
    base = []
    for m, v in ledger.items():
        path = B.load_path(m, exclude={B.WALLET})
        if not path or v["status"] != "bought":
            continue
        b = next((t for t in path["trades"] if t["user"] == B.WALLET and t["type"] == "buy"), None)
        if b and path["trades_ex"] and path["trades_ex"][-1]["slot"] > b["slot"] + 20:
            base.append((v["first_buy_bt"], m, path, b["slot"]))
    base.sort()
    cut = int(len(base) * 0.6)
    R = rules()
    report = {"n": len(base), "cut_time": base[cut][0], "delays": {}}
    for delay in (1, 2):
        cases = [Case(m, p, s, delay) for _, m, p, s in base]
        P = np.array([[c.pnl(r) for r in R] for c in cases])  # cases x rules
        tr, te = P[:cut], P[cut:]
        order = np.argsort(-tr.sum(0))
        print(f"\n=== retard +{delay} slot(s), position aléatoire | {len(cases)} entrées du wallet "
              f"(train {cut}, test {len(cases) - cut}) ===")
        print(f"  {'règle de sortie':40s} {'train SOL':>9s} {'/trade':>8s} | {'test SOL':>8s} {'/trade':>8s} {'gagn.':>5s}")
        rows = []
        for j in list(order[:8]) + [R.index({"kind": "hold", "n": 7})]:
            rows.append({"rule": name(R[j]), "train_total": round(float(tr[:, j].sum()), 2),
                         "test_total": round(float(te[:, j].sum()), 2), "test_mean": round(float(te[:, j].mean()), 4),
                         "test_win": round(float((te[:, j] > 0).mean()), 3)})
            print(f"  {name(R[j]):40s} {tr[:, j].sum():9.2f} {tr[:, j].mean():+8.4f} | {te[:, j].sum():8.2f} "
                  f"{te[:, j].mean():+8.4f} {100 * (te[:, j] > 0).mean():4.0f}%")
        best = order[0]
        boot = [te[np.random.default_rng(i).integers(0, len(te), len(te)), best].mean() for i in range(1000)]
        print(f"  meilleure règle sur train -> test: {te[:, best].mean():+.4f} SOL/trade, IC95 "
              f"[{np.percentile(boot, 2.5):+.4f}, {np.percentile(boot, 97.5):+.4f}]")
        report["delays"][str(delay)] = rows
    json.dump(report, open(os.path.join(DATA, "exit_search.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
