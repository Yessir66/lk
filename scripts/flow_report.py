#!/usr/bin/env python3
"""
Frozen evaluation of the "flow" exit algorithm on a followed wallet's buys (flow_backtest.py).

Algorithms (parameters fixed on the development days, never re-tuned on the hold-out days):
  F1  copy the wallet's buys made on the pump.fun bonding curve only; at +2 min sell all if fewer than 50 new
      wallets bought since entry; sell half at x1.5; sell the rest when new-wallet inflow falls under 30% of
      its peak (after 5 min); at +5 min sell all if the price is under x0.85; 45 min horizon
  F2  F1, only when the wallet buys >= 2.5 SOL
  F3  F1, only when our fill is < 10% above the wallet's own post-trade price
  F1+forme  F1, but a buy is only taken if the last 15 closed F1 paper trades sum > 0 (all signals are
      paper-traded; added AFTER the hold-out test failed, so it needs a forward test)
Baselines on the same buys: copy its sells (sell when it has sold >= 50%), take profit x1.5 / stop -30%,
climax + retest (token_flow.py).

Usage: flow_report.py <wallet> <windows dir> [--split YYYY-MM-DD] [--stake 50]
  --split: buys before this date (French time) are the hold-out, the others the development set.
"""
import argparse
import calendar
import collections
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import flow_backtest as B  # noqa: E402

TZ = 7200
ALGOS = {
    "F1 flux (courbe pump.fun)": (lambda w: w.venue == "pump", B.flow_rule()),
    "F2 = F1 si achat du wallet >= 2,5 SOL": (lambda w: w.venue == "pump" and w.wallet_sol >= 2.5, B.flow_rule()),
    "F3 = F1 si copie < +10 % du prix du wallet": (lambda w: w.venue == "pump" and w.entry_spot / w.wallet_px < 1.10, B.flow_rule()),
}
BASE = {
    "copier ses ventes (courbe)": (lambda w: w.venue == "pump", dict(wallet=(0.5, 1.0))),
    "TP x1,5 / stop -30 % (courbe)": (lambda w: w.venue == "pump", dict(levels=[(1.5, 1)], sl0=0.3)),
    "climax + retest (courbe)": (lambda w: w.venue == "pump", "climax"),
    "F1 sur tous les achats (sans filtre)": (lambda w: True, B.flow_rule()),
}


def evaluate(w, rule, **kw):
    if rule == "climax":
        return w.replay(B.climax_retest(), kw.get("stake", 50.0))
    return B.evaluate(B.Path(w, **kw), **rule)


def form_filter(ws_all, n=15, stake=50.0):
    """F1 results of the curve buys, and whether each is taken under the form filter (causal)"""
    cur = sorted([w for w in ws_all if w.venue == "pump"], key=lambda w: w.bt)
    pnl = [B.evaluate(B.Path(w, stake=stake), **B.flow_rule()) for w in cur]
    take = {}
    for i, w in enumerate(cur):
        past = [pnl[j] for j in range(i) if cur[j].bt + 45 * 60 <= w.bt][-n:]
        take[(w.mint, w.bt)] = (len(past) < n or sum(past) > 0, pnl[i])
    return take


def block(ws, title, stake, robust=True, form=None):
    day = lambda w: time.strftime("%d/%m", time.gmtime(w.bt + TZ))
    print(f"\n=== {title}: {len(ws)} achats du wallet exploitables, {len(set(map(day, ws)))} jours")
    if form:
        byd = collections.OrderedDict()
        for w in ws:
            if w.venue == "pump":
                tk, x = form[(w.mint, w.bt)]
                byd[day(w)] = byd.get(day(w), 0.0) + (x if tk else 0.0)
        n = sum(1 for w in ws if w.venue == "pump" and form[(w.mint, w.bt)][0])
        print(f"  {'F1 + filtre forme (15 derniers > 0)':44s} {n:3d} trades  total {sum(byd.values()):+7.0f}$  "
              f"jours positifs {sum(x > 0 for x in byd.values())}/{len(byd)}  jours perdants {sum(x < 0 for x in byd.values())}")
        print("      par jour : " + "  ".join(f"{d} {x:+.0f}$" for d, x in byd.items()))
    for name, (flt, rule) in list(ALGOS.items()) + list(BASE.items()):
        sub = [w for w in ws if flt(w)]
        if not sub:
            continue
        v = np.array([evaluate(w, rule, stake=stake) for w in sub])
        byd = collections.OrderedDict()
        for w, x in zip(sub, v):
            byd[day(w)] = byd.get(day(w), 0.0) + x
        s = np.sort(v)[::-1]
        boot = [v[np.random.default_rng(i).integers(0, len(v), len(v))].mean() for i in range(2000)]
        print(f"  {name:44s} {len(v):3d} trades  total {v.sum():+7.0f}$  sans les 3 meilleurs {s[3:].sum():+6.0f}$  "
              f"jours positifs {sum(x > 0 for x in byd.values())}/{len(byd)}  moyenne {v.mean():+.2f}$ "
              f"[{np.percentile(boot, 2.5):+.2f}; {np.percentile(boot, 97.5):+.2f}]")
        if name.startswith("F1"):
            print("      par jour : " + "  ".join(f"{d} {x:+.0f}$" for d, x in byd.items()))
    if robust:
        flt, rule = ALGOS["F1 flux (courbe pump.fun)"]
        sub = [w for w in ws if flt(w)]
        print("  F1, robustesse :")
        for lab, kw in (("copie 4 s plus tard", dict(delay_s=4)), ("copie 10 s plus tard", dict(delay_s=10)),
                        ("glissement 4 % au lieu de 2 %", dict(slip=0.04)), ("frais du bot 0,5 % au lieu de 1 %", dict(fee=0.0175)),
                        ("mise 100 $", dict(stake=100.0)), ("mise 25 $", dict(stake=25.0))):
            v = np.array([evaluate(w, rule, **kw) for w in sub])
            print(f"      {lab:32s} total {v.sum():+7.0f}$  sans les 3 meilleurs {np.sort(v)[::-1][3:].sum():+6.0f}$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wallet")
    ap.add_argument("windows")
    ap.add_argument("--split", default=None)
    ap.add_argument("--stake", type=float, default=50.0)
    ap.add_argument("--cache", default=None)
    a = ap.parse_args()
    ws = B.load_all(a.windows, a.wallet, a.cache or os.path.join(a.windows, "..", "fb_cache"))
    form = form_filter(ws, stake=a.stake)
    if a.split:
        cut = calendar.timegm(time.strptime(a.split, "%Y-%m-%d")) - TZ
        block([w for w in ws if w.bt >= cut], "Jours de développement", a.stake, form=form)
        block([w for w in ws if w.bt < cut], "Jours mis de côté (jamais vus pendant la mise au point)", a.stake, form=form)
    block(ws, "Toutes les journées", a.stake, robust=False, form=form)


if __name__ == "__main__":
    main()
