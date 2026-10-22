#!/usr/bin/env python3
"""Wallet 7BNaxx6K: is the user's 2 h criterion useful? Same rules on first buys of tokens created <= 2 h before
(w7b_sample.py) and > 2 h before (w7b_old.py, random sample)."""
import glob, json, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
import w7b_eval as E
OLD = os.path.join(os.path.dirname(E.D), "sample3j_old")


def run(P, Ef, levels=((2.0, 1.0),), stop=0.5, tmax=20, be=None):
    left, cash, k, lvl, ntx = 1.0, 0.0, 0, Ef * (1 - stop) if stop else 0.0, 1
    val = E.STAKE / (1 + E.FEE)
    for i, (o, h, l, c) in enumerate(P, 1):
        if l <= lvl:
            cash += left * val * min(o, lvl) / Ef * (1 - E.SLIP) * (1 - E.FEE); left = 0; ntx += 1; break
        while k < len(levels) and h >= levels[k][0] * Ef:
            f = min(levels[k][1], left); cash += f * val * levels[k][0] * (1 - E.SLIP) * (1 - E.FEE); left -= f; ntx += 1; k += 1
            if be: lvl = max(lvl, be * Ef)
        if left <= 1e-9: break
        if tmax and i >= tmax:
            cash += left * val * c / Ef * (1 - E.SLIP) * (1 - E.FEE); left = 0; ntx += 1; break
    if left > 1e-9:
        cash += left * val * P[-1][3] / Ef * (1 - E.SLIP) * (1 - E.FEE); ntx += 1
    return cash - E.STAKE - E.GAS * ntx


RULES = {"x2, 20 min, stop -50 %": dict(),
         "MC>=45k, moitié x1,5 + moitié x2, stop au prix d'achat après x1,5, 20 min, stop -50 %": dict(mc=45e3, levels=((1.5, .5), (2.0, .5)), be=1.0),
         "MC>=45k, tout à x1,5, 20 min, stop -50 %": dict(mc=45e3, levels=((1.5, 1.0),))}


def load(d, dev):
    rows = [json.load(open(f)) for f in glob.glob(os.path.join(d, "*.json"))]
    rows = [r for r in rows if r.get("status") == "ok" and r.get("candles") and r["mint"] not in dev]
    for r in rows:
        r["P"] = E.path(r)
    return rows


dev = set(E.WIN) | set(E.LOSS)
for lab, rows in (("créés <= 2 h avant son achat", load(E.D, dev)), ("créés > 2 h avant (échantillon au hasard)", load(OLD, dev))):
    x2 = sum(r["P"][:, 1].max() >= 2 * r["fill"] for r in rows)
    mc = np.array([r["fill"] * 1e9 for r in rows])
    print(f"\n=== Tokens {lab} : {len(rows)} ; x2 en 30 min : {x2} ({x2 / len(rows):.0%}) ; MC médiane {np.median(mc) / 1e3:.0f} k$")
    for name, kw in RULES.items():
        kw = dict(kw); m = kw.pop("mc", 0)
        for ref in ("fill", "copy"):
            v = [run(r["P"], r[ref] * (1.01 if ref == "copy" else 1), **kw) for r in rows if r["fill"] * 1e9 >= m and r.get(ref)]
            print(f"  {name:80s} {'son prix' if ref == 'fill' else 'copie   '} {sum(v):+6.0f}$ sur {len(v):3d} ({sum(v) / max(len(v), 1):+.2f}$/token)")
