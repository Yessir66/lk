#!/usr/bin/env python3
"""Wallet 7BNaxx6K, 2 days: entry filters (its buy size, MC, what it paid with, hour) x exits, chosen on 20/10 and
checked on 21/10 (and the reverse)."""
import glob, itertools, json, os, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import w7b_eval as E
import w7b_sample as WS


def run(P, Ef, tp=2.0, stop=None, tmax=None):
    lvl = Ef * (1 - stop) if stop else 0.0
    for k, (o, h, l, c) in enumerate(P, 1):
        if l <= lvl: x = min(o, lvl) / Ef; break
        if h >= tp * Ef: x = tp; break
        if tmax and k >= tmax: x = c / Ef; break
    else:
        x = P[-1][3] / Ef
    return E.STAKE * (x * (1 - E.SLIP) * (1 - E.FEE) / (1 + E.FEE) - 1) - 2 * E.GAS


rows = [json.load(open(f)) for f in glob.glob(os.path.join(E.D, "*.json"))]
dev = set(E.WIN) | set(E.LOSS)
rows = sorted([r for r in rows if r.get("status") == "ok" and r.get("candles") and r["mint"] not in dev], key=lambda r: r["bt"])
first, _ = WS.first_buys(1792454400)        # 20/10 00:00 UTC
for r in rows:
    r["P"] = E.path(r); fb = first[r["mint"]]
    r.update(mc=r["fill"] * 1e9, usd=r["fill"] * fb["q"], sol=fb["paid"] == "SOL", hour=int(time.strftime("%H", time.gmtime(r["bt"] + 7200))),
             day=time.strftime("%d", time.gmtime(r["bt"] + 7200)), top=(r["P"][:, 1].max() / r["fill"]))
d20 = [r for r in rows if r["day"] == "20"]; d21 = [r for r in rows if r["day"] == "21"]
print(f"20/10 : {len(d20)} tokens ({sum(r['top'] >= 2 for r in d20)} x2) ; 21/10 : {len(d21)} ({sum(r['top'] >= 2 for r in d21)} x2)")
q = lambda v, p: np.percentile([r[v] for r in d20], p)
FILT = {"tous": lambda r: True, "payé en SOL": lambda r: r["sol"], "payé avec un autre token": lambda r: not r["sol"]}
for v in ("usd", "mc"):
    for p in (33, 50, 67):
        FILT[f"{v} >= {q(v, p):.0f}"] = (lambda r, v=v, t=q(v, p): r[v] >= t)
        FILT[f"{v} < {q(v, p):.0f}"] = (lambda r, v=v, t=q(v, p): r[v] < t)
FILT["8h-20h"] = lambda r: 8 <= r["hour"] < 20
FILT["20h-8h"] = lambda r: not (8 <= r["hour"] < 20)
EXITS = {f"TP x{tp} stop {st} sortie {tm} min": (tp, st, tm) for tp, st, tm in itertools.product((1.3, 1.5, 2.0), (None, 0.3), (5, 10, 20))}
res = []
for fn, f in FILT.items():
    for en, (tp, st, tm) in EXITS.items():
        a = [run(r["P"], r["fill"], tp, st, tm) for r in d20 if f(r)]
        b = [run(r["P"], r["fill"], tp, st, tm) for r in d21 if f(r)]
        if len(a) >= 10 and len(b) >= 10:
            res.append((sum(a), sum(b), len(a), len(b), fn, en))
print(f"{len(res)} combinaisons ; positives les deux jours : {sum(1 for a, b, *_ in res if a > 0 and b > 0)}")
print("meilleures sur le 20/10, vérifiées le 21/10 :")
for a, b, na, nb, fn, en in sorted(res, key=lambda x: -x[0])[:8]:
    print(f"  {fn:28s} {en:30s} 20/10 {a:+5.0f}$ ({na})  21/10 {b:+5.0f}$ ({nb})")
print("meilleures sur le 21/10, vérifiées le 20/10 :")
for a, b, na, nb, fn, en in sorted(res, key=lambda x: -x[1])[:5]:
    print(f"  {fn:28s} {en:30s} 21/10 {b:+5.0f}$ ({nb})  20/10 {a:+5.0f}$ ({na})")
