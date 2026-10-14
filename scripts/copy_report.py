#!/usr/bin/env python3
"""
Is copying this wallet's buys profitable with my own exits? Report on real prices (copy_sim.py):
  1. price paid by a copy vs the wallet's own post-trade price, by copy delay
  2. share of copy entries reaching +20% ... +500% within 24 h
  3. named exit rules, a few delays, plus "copy its sells too" (GMGN's default) and the theoretical bound of
     buying at the wallet's own price
  4. rule search: chosen on the oldest 60% of entries, checked on the newest 40%
  5. limit-order entries at the wallet's price; simple filters fixed in advance (wallet buy size, market cap,
     how far the price already ran)
  6. the wallet's own result over the same period (SOL flows, open positions at their last price)
Usage: copy_report.py <wallet> [--since YYYY-MM-DD] [--size 25]
Output: printed, and data/wallet_<8>/report.json
"""
import argparse
import calendar
import itertools
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import copy_sim as S  # noqa: E402

V2 = dict(tps=[(1.5, .4), (2.0, .3)], sl=.6, sl_after=.75, trail=(.4, 2.0), tmin=45)
NAMED = {
    "Paliers +50/+100 % + trailing -40 % (v2)": V2,
    "Paliers +50/+100/+200/+500 % (v2 fixes)": dict(tps=[(1.5, .4), (2.0, .3), (3.0, .2), (6.0, .1)], sl=.6, sl_after=.75, trail=None, tmin=45),
    "Tout vendre à +50 %": dict(tps=[(1.5, 1.0)], sl=.6, sl_after=None, trail=None, tmin=45),
    "Garder 24 h": dict(tps=[], sl=None, sl_after=None, trail=None, tmin=None),
}


def grid():
    g = {}
    for tp, sl, tm in itertools.product((0.2, 0.3, 0.5, 0.75, 1.0, 2.0), (0.2, 0.3, 0.5, None), (5, 15, 45, None)):
        g[f"TP +{int(tp * 100)}% / SL {'-' + str(int(sl * 100)) + '%' if sl else 'aucun'} / {str(tm) + ' min' if tm else 'sans limite'}"] = \
            dict(tps=[(1 + tp, 1.0)], sl=sl, sl_after=None, trail=None, tmin=tm)
    for (a, b), sl, tm in itertools.product(((0.3, 0.6), (0.5, 1.0), (0.5, 2.0)), (0.3, 0.6), (15, 45)):
        g[f"40% à +{int(a * 100)}%, 30% à +{int(b * 100)}%, reste suiveur -40% / SL -{int(sl * 100)}% / {tm} min"] = \
            dict(tps=[(1 + a, .4), (1 + b, .3)], sl=sl, sl_after=.75, trail=(.4, 1 + b), tmin=tm)
    for tr, arm, sl, tm in itertools.product((0.2, 0.3, 0.4), (1.2, 1.5), (0.3, 0.5), (15, None)):
        g[f"suiveur -{int(tr * 100)}% dès +{int((arm - 1) * 100)}% / SL -{int(sl * 100)}% / {str(tm) + ' min' if tm else 'sans limite'}"] = \
            dict(tps=[], sl=sl, sl_after=None, trail=(tr, arm), tmin=tm)
    return g


def stats(p):
    p = np.asarray(p, dtype=float)
    boot = [p[np.random.default_rng(i).integers(0, len(p), len(p))].mean() for i in range(1000)]
    return {"n": len(p), "total": round(float(p.sum()), 2), "mean": round(float(p.mean()), 3),
            "median": round(float(np.median(p)), 3), "win": round(float((p > 0).mean()), 3),
            "ci95": [round(float(np.percentile(boot, 2.5)), 3), round(float(np.percentile(boot, 97.5)), 3)]}


def line(name, st):
    return (f"{name:58s} n={st['n']:3d}  total {st['total']:+8.2f}$  moyenne {st['mean']:+6.2f}$ "
            f"[{st['ci95'][0]:+.2f}; {st['ci95'][1]:+.2f}]  gagnants {100 * st['win']:3.0f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wallet")
    ap.add_argument("--since", default=None)
    ap.add_argument("--size", type=float, default=25.0)
    a = ap.parse_args()
    S.setup(a.wallet)
    S.SIZE = a.size
    since = calendar.timegm(time.strptime(a.since, "%Y-%m-%d")) if a.since else 0
    rep = {"wallet": a.wallet, "size": a.size, "since": since}

    ens = {lat: S.entries(lat, V2, since) for lat in (-1, 0, 2, 5, 15)}
    E = ens[5]
    rep["n"], rep["span"] = len(E), [E[0].bt, E[-1].bt]
    print(f"{len(E)} entrées copiées ({a.size:.0f} $ chacune), du {time.strftime('%d/%m %H:%M', time.gmtime(E[0].bt))} "
          f"au {time.strftime('%d/%m %H:%M', time.gmtime(E[-1].bt))} UTC, {sum(e.exact for e in E)} avec les trades au bloc près")

    print("\n== 1. Prix payé par la copie vs prix juste après l'achat du wallet")
    rep["gap"] = {}
    for lat in (0, 2, 5, 15):
        g = [e.entry / e.wallet_px - 1 for e in ens[lat] if e.exact and e.wallet_px]
        rep["gap"][lat] = [float(np.percentile(g, q)) for q in (25, 50, 75)]
        print(f"  retard {lat:2d} blocs (~{lat * 0.4:.1f} s): médiane {100 * np.median(g):+.1f}%  "
              f"(moitié des cas entre {100 * np.percentile(g, 25):+.1f}% et {100 * np.percentile(g, 75):+.1f}%)")

    print("\n== 2. Gain max atteint dans les 24 h après l'entrée copiée (retard 5 blocs)")
    mfe = np.array([e.mfe for e in E])
    rep["mfe"] = {x: float((mfe >= x).mean()) for x in (0.2, 0.5, 1, 2, 5)}
    print("  " + ", ".join(f"+{int(100 * x)}% atteint par {100 * v:.0f}%" for x, v in rep["mfe"].items()))

    print("\n== 3. Règles de sortie")
    rep["named"] = {}
    for lat in (-1, 2, 5, 15):
        print(f" -- copie {lat} blocs (~{lat * 0.4:.1f} s) après le wallet" if lat >= 0 else
              " -- THÉORIQUE : achat au prix exact du wallet (aucune copie ne l'obtient)")
        for name, r in NAMED.items():
            st = stats([e.run(r)[0] for e in ens[lat]])
            rep["named"][f"{name}|{lat}"] = st
            print("  " + line(name, st))
        st = stats([e.mirror() for e in ens[lat]])
        rep["named"][f"Copier aussi ses ventes|{lat}"] = st
        print("  " + line("Copier aussi ses ventes (réglage par défaut)", st))

    print("\n== 4. Recherche de règle : choisie sur les 60 % plus anciennes, vérifiée sur les 40 % récentes (retard 5 blocs)")
    G, cut = grid(), int(len(E) * 0.6)
    P = {k: np.array([e.run(r)[0] for e in E]) for k, r in G.items()}
    order = sorted(P, key=lambda k: -P[k][:cut].sum())
    rep["grid"] = []
    for k in order[:8]:
        tr, te = stats(P[k][:cut]), stats(P[k][cut:])
        rep["grid"].append({"rule": k, "train": tr, "test": te})
        print(f"  {k:58s} ancien {tr['mean']:+6.2f}$ | récent {te['mean']:+6.2f}$ [{te['ci95'][0]:+.2f}; {te['ci95'][1]:+.2f}]")
    best = max(P, key=lambda k: P[k].sum())
    rep["best_overall"] = {"rule": best, **stats(P[best])}
    print("  meilleure sur toute la période (choisie après coup, optimiste) :\n  " + line(best, stats(P[best])))

    print("\n== 5a. Entrée par ordre limite au prix du wallet (règles v2 / meilleure règle de l'étape 4)")
    rep["limit"] = {}
    for lim in ((1.0, 2), (1.0, 10), (1.0, 60), (0.9, 60)):
        es = S.entries(5, V2, since, limit=lim)
        if len(es) < 2:
            continue
        x, y = stats([e.run(V2)[0] for e in es]), stats([e.run(G[order[0]])[0] for e in es])
        rep["limit"][str(lim)] = {"v2": x, "best": y, "filled": len(es)}
        print(f"  limite {lim[0]:.1f}x son prix, valable {lim[1]:2d} min : remplie {len(es)}/{len(E)}, "
              f"v2 {x['mean']:+.2f}$ [{x['ci95'][0]:+.2f}; {x['ci95'][1]:+.2f}], meilleure grille {y['mean']:+.2f}$")

    print("\n== 5b. Filtres fixés à l'avance (retard 5 blocs, règles v2)")
    med = float(np.median([e.wallet_sol for e in E]))
    filters = {
        f"achat du wallet >= {med:.1f} SOL (médiane)": lambda e: e.wallet_sol >= med,
        f"achat du wallet < {med:.1f} SOL": lambda e: e.wallet_sol < med,
        "MC à l'entrée < 20 k$": lambda e: e.entry * 1e9 < 20e3,
        "MC à l'entrée >= 20 k$": lambda e: e.entry * 1e9 >= 20e3,
        "copie <= +10 % au-dessus du wallet": lambda e: bool(e.wallet_px) and e.entry / e.wallet_px <= 1.10,
        "copie > +10 % au-dessus du wallet": lambda e: bool(e.wallet_px) and e.entry / e.wallet_px > 1.10,
    }
    pv2, rep["filters"] = np.array([e.run(V2)[0] for e in E]), {}
    for name, fn in filters.items():
        msk = np.array([fn(e) for e in E])
        old, new = msk & (np.arange(len(E)) < cut), msk & (np.arange(len(E)) >= cut)
        st = stats(pv2[msk])
        o = stats(pv2[old])["mean"] if old.sum() > 1 else float("nan")
        n = stats(pv2[new])["mean"] if new.sum() > 1 else float("nan")
        rep["filters"][name] = {"all": st, "old": o, "new": n}
        print(f"  {name:44s} n={st['n']:3d} moyenne {st['mean']:+6.2f}$ [{st['ci95'][0]:+.2f}; {st['ci95'][1]:+.2f}] "
              f"| ancien {o:+6.2f}$ récent {n:+6.2f}$")

    print("\n== 6. Le wallet lui-même (tokens achetés dans la période)")
    sol_usd = 104.5
    tot, n, win, open_sol = 0.0, 0, 0, 0.0
    for m, es in S.load().items():
        if es[0][0] != "buy" or es[0][1] < since:
            continue
        f = sum(-x[4] if x[0] == "buy" else x[4] for x in es)
        left, c = sum(x[3] if x[0] == "buy" else -x[3] for x in es), S.candles(m)
        if left > 0 and c:
            v = left * c[1][-1][3] / sol_usd
            f, open_sol = f + v, open_sol + v
        tot, n, win = tot + f, n + 1, win + (f > 0)
    rep["wallet_self"] = {"sol": tot, "tokens": n, "winners": win, "open_sol": open_sol}
    print(f"  {n} tokens, résultat net {tot:+.2f} SOL (dont {open_sol:.2f} SOL de positions encore ouvertes au dernier prix), "
          f"{win} tokens gagnants")
    json.dump(rep, open(os.path.join(S.D, "report.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
