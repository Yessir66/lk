#!/usr/bin/env python3
"""Test of the frozen candidate rule B (analyses/wallet_7BNaxx6K_preregistration.md) on a day never used to choose
it, next to the user's x2 / 20 min rule. Usage: w7b_ruleB.py <day of month, French time, e.g. 19>"""
import glob, json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
import w7b_eval as E


def run(P, Ef, tp=2.0, stop=None, tmax=None):
    lvl = Ef * (1 - stop) if stop else 0.0
    for k, (o, h, l, c) in enumerate(P, 1):
        if l <= lvl: x = min(o, lvl) / Ef; break
        if h >= tp * Ef: x = tp; break
        if tmax and k >= tmax: x = c / Ef; break
    else:
        x = P[-1][3] / Ef
    return E.STAKE * (x * (1 - E.SLIP) * (1 - E.FEE) / (1 + E.FEE) - 1) - 2 * E.GAS


day = sys.argv[1]
rows = [json.load(open(f)) for f in glob.glob(os.path.join(E.D, "*.json"))]
rows = sorted([r for r in rows if r.get("status") == "ok" and r.get("candles") and time.strftime("%d", time.gmtime(r["bt"] + 7200)) == day],
              key=lambda r: r["bt"])
for r in rows:
    r["P"] = E.path(r)
print(f"{day}/10 : {len(rows)} tokens (créés 2 h ou moins avant son premier achat), x2 en 30 min : "
      f"{sum(r['P'][:, 1].max() >= 2 * r['fill'] for r in rows)}")
for lab, ref in (("prix de copie", "copy"), ("prix du wallet", "fill")):
    b = [run(r["P"], r[ref] * (1.01 if ref == "copy" else 1), 1.5, None, 5) for r in rows if r["fill"] * 1e9 >= 22600 and r.get(ref)]
    u = [run(r["P"], r[ref] * (1.01 if ref == "copy" else 1), 2.0, 0.6, 20) for r in rows if r.get(ref)]
    print(f"  {lab:15s} RÈGLE B (MC >= 22,6k, x1,5 sinon 5 min) : {sum(b):+6.0f}$ sur {len(b)} tokens ({sum(b) / max(len(b), 1):+.2f}$/token, "
          f"{sum(x > 0 for x in b)} gagnants)")
    print(f"  {lab:15s} règle x2 / 20 min / stop -60 %        : {sum(u):+6.0f}$ sur {len(u)} tokens ({sum(u) / max(len(u), 1):+.2f}$/token)")
