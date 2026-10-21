#!/usr/bin/env python3
"""Rules with a take profit at x2 on the large sample of wallet 7BNaxx6K (w7b_sample.py): minute candles of
pump.fun, low before high inside a minute, from the minute after its buy to 30 min. Reference = its fill price
(or the copy price, first trade >= 2 s later). 50 $ per token, 2.25 % fee a side, 2 % slippage on exits, gas."""
import glob
import json
import os
import sys
import time

import numpy as np

D = os.path.join(os.path.dirname(__file__), "..", "..", "data", "wallet_7BNaxx6K", "sample3j")
exec(open(os.path.join(os.path.dirname(__file__), "w7b_lists.py")).read())
FEE, SLIP, GAS, STAKE = 0.0225, 0.02, 0.21, 50.0
RULES = {"RÈGLE FIGÉE : x2, sinon vente à 20 min, stop -60 %": dict(stop=0.6, tmax=20),
         "sans stop (x2 ou prix à 30 min)": {},
         "stop -30 %": dict(stop=0.3), "stop -40 %": dict(stop=0.4), "stop -50 %": dict(stop=0.5),
         "x2, sinon vente à 10 min": dict(tmax=10), "x2, sinon vente à 15 min": dict(tmax=15),
         "x2, sinon vente à 20 min (sans stop)": dict(tmax=20), "x2, sinon vente à 30 min": dict(tmax=30),
         "x2, sinon vente à 20 min, stop -40 %": dict(stop=0.4, tmax=20)}


def path(r):
    m0 = r["bt"] // 60 * 60
    c = {int(x[0]): x[1:] for x in r["candles"]}
    before = [t for t in sorted(c) if t <= m0]
    last = c[before[-1]][3] if before else None
    out = []
    for t in range(m0 + 60, m0 + 30 * 60 + 60, 60):
        if t in c:
            out.append(c[t]); last = c[t][3]
        elif last is not None:
            out.append([last] * 4)
    return np.array(out, float)


def run(P, E, stop=None, tmax=None):
    lvl = E * (1 - stop) if stop else 0.0
    for k, (o, h, l, c) in enumerate(P, 1):
        if l <= lvl:
            x = min(o, lvl) / E; break
        if h >= 2 * E:
            x = 2.0; break
        if tmax and k >= tmax:
            x = c / E; break
    else:
        x = P[-1][3] / E
    return STAKE * (x * (1 - SLIP) * (1 - FEE) / (1 + FEE) - 1) - 2 * GAS, x


def report(rows, ref, title):
    rows = [r for r in rows if r.get(ref)]
    if not rows:
        return
    P = [(path(r), r[ref] * (1.01 if ref == "copy" else 1.0)) for r in rows]
    hit = sum(any(h >= 2 * E for _, h, _, _ in p) for p, E in P)
    print(f"\n=== {title} : {len(rows)} tokens, x2 atteint en 30 min : {hit}")
    print(f"  {'règle':52s} {'total':>8s} {'/token':>7s} {'gagnants':>9s} {'x2 vendus':>9s}  1re moitié / 2e moitié")
    half = len(P) // 2
    for name, kw in RULES.items():
        v = [run(p, E, **kw) for p, E in P]
        pnl = np.array([a for a, _ in v])
        print(f"  {name:52s} {pnl.sum():+7.0f}$ {pnl.mean():+6.2f}$ {int((pnl > 0).sum()):>4d}/{len(pnl):<4d} "
              f"{sum(x >= 2 for _, x in v):>5d}     {pnl[:half].sum():+6.0f}$ / {pnl[half:].sum():+6.0f}$")


def main():
    rows = [json.load(open(f)) for f in glob.glob(os.path.join(D, "*.json"))]
    ok = sorted([r for r in rows if r.get("status") == "ok" and r.get("candles")], key=lambda r: r["bt"])
    dev = set(WIN) | set(LOSS)
    st = {}
    for r in rows:
        if r["mint"] not in dev:
            st[r.get("status")] = st.get(r.get("status"), 0) + 1
    print("tokens vérifiés (hors mise au point) :", st)
    report([r for r in ok if r["mint"] in dev], "fill", "Tokens de mise au point, bougies (à comparer au calcul trade par trade)")
    test = [r for r in ok if r["mint"] not in dev]
    report(test, "fill", "TEST, prix d'achat du wallet")
    report(test, "copy", "TEST, prix de copie (premier trade 2 s après lui, +1 %)")
    days = sorted({time.strftime("%d/%m", time.gmtime(r["bt"] + 7200)) for r in test})
    for d in days:
        report([r for r in test if time.strftime("%d/%m", time.gmtime(r["bt"] + 7200)) == d], "fill", f"TEST du {d}")


if __name__ == "__main__":
    main()
