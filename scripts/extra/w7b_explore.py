#!/usr/bin/env python3
"""Wallet 7BNaxx6K, large sample: what separates the tokens that reach x2 (minute candles after its first buy).
Rules are chosen on the first half of the tokens (by time) and checked on the second half."""
import glob, itertools, json, os, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import w7b_eval as E
import w7b_sample as WS
rows = [json.load(open(f)) for f in glob.glob(os.path.join(E.D, "*.json"))]
dev = set(E.WIN) | set(E.LOSS)
rows = sorted([r for r in rows if r.get("status") == "ok" and r.get("candles") and r["mint"] not in dev], key=lambda r: r["bt"])
first, _ = WS.first_buys(1792540800)        # 21/10 00:00 UTC
for r in rows:
    P = E.path(r); x = P / r["fill"]
    r.update(P=P, mc=r["fill"] * 1e9, paid=first[r["mint"]]["paid"], usd=r["fill"] * first[r["mint"]]["q"],
             c1=x[0, 3], c2=x[1, 3], c3=x[2, 3], c5=x[4, 3], top=x[:, 1].max(), top10=x[:10, 1].max())
y = np.array([r["top"] >= 2 for r in rows])
print(f"{len(rows)} tokens ; x2 en 30 min : {y.sum()} ; x1,5 : {sum(r['top'] >= 1.5 for r in rows)} ; x1,3 : {sum(r['top'] >= 1.3 for r in rows)}")
def auc(a, b):
    a, b = np.array(a, float), np.array(b, float)
    return ((a[:, None] > b[None]).sum() + .5 * (a[:, None] == b[None]).sum()) / (len(a) * len(b))
print("indicateur        médiane x2   médiane pas x2   AUC")
for k in ("mc", "usd", "c1", "c2", "c3", "c5"):
    a = [r[k] for r in rows if r["top"] >= 2]; b = [r[k] for r in rows if r["top"] < 2]
    print(f"  {k:14s} {np.median(a):11.3g} {np.median(b):15.3g} {auc(a, b):6.2f}")
q = {}
for r in rows:
    q.setdefault("SOL" if r["paid"] == "SOL" else r["paid"][:6], []).append(r["top"] >= 2)
print("payé en :", {k: f"{sum(v)}/{len(v)}" for k, v in q.items()})

def run(P, Ef, tp=2.0, stop=None, tmax=None, early=None):
    lvl = Ef * (1 - stop) if stop else 0.0
    for k, (o, h, l, c) in enumerate(P, 1):
        if l <= lvl: x = min(o, lvl) / Ef; break
        if h >= tp * Ef: x = tp; break
        if early and k == early[0] and c < early[1] * Ef: x = c / Ef; break
        if tmax and k >= tmax: x = c / Ef; break
    else:
        x = P[-1][3] / Ef
    return E.STAKE * (x * (1 - E.SLIP) * (1 - E.FEE) / (1 + E.FEE) - 1) - 2 * E.GAS
grid = {}
for tp, stop, tmax, early in itertools.product((1.3, 1.5, 2.0), (None, 0.3), (5, 10, 20, 30), (None, (1, 1.0), (2, 1.0), (2, 1.1), (3, 1.0), (5, 1.1))):
    grid[(tp, stop, tmax, early)] = np.array([run(r["P"], r["fill"], tp, stop, tmax, early) for r in rows])
half = len(rows) // 2
best = sorted(grid, key=lambda k: -grid[k][:half].sum())[:8]
print(f"\nmeilleures règles sur les {half} premiers tokens (vérifiées sur les {len(rows) - half} suivants) :")
for k in best:
    v = grid[k]
    print(f"  TP x{k[0]} stop {k[1]} sortie {k[2]} min contrôle {k[3]} : 1re moitié {v[:half].sum():+5.0f}$  2e moitié {v[half:].sum():+5.0f}$")
print(f"\nrègles positives sur les deux moitiés : {sum(1 for v in grid.values() if v[:half].sum() > 0 and v[half:].sum() > 0)} / {len(grid)}")
print(f"meilleur total sur les 63 : {max(v.sum() for v in grid.values()):+.0f}$ ; médiane des règles {np.median([v.sum() for v in grid.values()]):+.0f}$")

# confirmation entry: wait k minutes, buy at that close only if it is >= theta x its fill, then TP / stop / time exit
def run_conf(P, fill, k, theta, tp, stop, tmax):
    if len(P) <= k or P[k - 1][3] < theta * fill:
        return None
    Ef = P[k - 1][3] * 1.02
    return run(P[k:], Ef, tp, stop, tmax)
cg = {}
for k, theta, tp, stop, tmax in itertools.product((1, 2, 3, 5), (1.0, 1.2, 1.5), (1.5, 2.0), (None, 0.3), (10, 20)):
    cg[(k, theta, tp, stop, tmax)] = [run_conf(r["P"], r["fill"], k, theta, tp, stop, tmax) for r in rows]
tot = lambda v, a, b: sum(x for x in v[a:b] if x is not None)
cnt = lambda v, a, b: sum(x is not None for x in v[a:b])
best = sorted(cg, key=lambda k: -tot(cg[k], 0, half))[:8]
print("\nachat de confirmation (attendre k min, acheter si prix >= theta x son prix) — choisi sur la 1re moitié :")
for k in best:
    v = cg[k]
    print(f"  attendre {k[0]} min, seuil x{k[1]}, TP x{k[2]}, stop {k[3]}, sortie {k[4]} min : "
          f"1re moitié {tot(v, 0, half):+5.0f}$ ({cnt(v, 0, half)} achats)  2e moitié {tot(v, half, None):+5.0f}$ ({cnt(v, half, None)} achats)")
print(f"positives sur les deux moitiés : {sum(1 for v in cg.values() if tot(v, 0, half) > 0 and tot(v, half, None) > 0)} / {len(cg)}")
