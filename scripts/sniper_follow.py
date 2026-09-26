#!/usr/bin/env python3
"""
Live decision for the sniper-following path: should we buy a token that 4yFAz7dp or CBKgS8Nj just
sniped, the way the studied wallet would?

  sniper_follow.py <mint> [<mint> ...] [--k 0.5]

For each token: fetch its pump.fun trades from creation, find the sniper's buy, compute the same
decision-time features as the training data (build_sniper_universe.decision_features / history_features),
score them with data/sniper_model.json, and compare with the adaptive threshold: the top k x (wallet's
follow rate over the last 100 sniper buys) of those buys' scores. k=1 matches the wallet's pace
(~46% precision in walk-forward validation), k=0.5 is stricter (~60%, fewer buys).

The history (earlier sniper buys and whether the wallet followed) comes from data/sniper_universe.json;
rebuild it (fetch_leader_trades / fetch_universe_trades / build_sniper_universe) to keep it current —
the tool warns when it is more than 2 hours old.
"""
import json
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from build_sniper_universe import SNIPERS, decision_features, history_features, sniper_buys_from_trades  # noqa: E402
from fetch_universe_trades import fetch  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")


def score(model, r):
    r = dict(r)
    r["log_creator_prev_n"] = math.log1p(r["creator_prev_n"])
    r["is_cbkg"] = int(r["sniper_first"] == "CBKg")
    x = np.array([float(r[k]) for k in model["features"]])
    z = model["intercept"] + float(np.dot(model["coef"], (x - np.array(model["mean"])) / np.array(model["std"])))
    return 1 / (1 + math.exp(-z))


def adaptive_threshold(model, history, t, k):
    pol = model["policy"]
    vis = [h for h in history if h["t"] <= t - pol["outcome_delay_s"]][-pol["history_n"]:]
    rate = sum(h["label"] for h in vis) / len(vis)
    q = float(np.quantile([score(model, h) for h in vis], max(0.0, 1 - k * rate)))
    return q, rate, len(vis)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    k = float(sys.argv[sys.argv.index("--k") + 1]) if "--k" in sys.argv else None
    if k is not None:
        args.remove(sys.argv[sys.argv.index("--k") + 1])
    model = json.load(open(os.path.join(DATA, "sniper_model.json")))
    k = k if k is not None else model["policy"]["k"]
    history = json.load(open(os.path.join(DATA, "sniper_universe.json")))
    age_h = (time.time() - history[-1]["t"]) / 3600
    if age_h > 2:
        print(f"ATTENTION: historique vieux de {age_h:.1f} h — le taux de suivi récent du wallet peut avoir changé.")
    for mint in args:
        d = fetch(mint, time.time())
        tr = d["trades"]
        if not tr or not d["complete"]:
            print(f"{mint}: trades depuis la création indisponibles")
            continue
        buys = sniper_buys_from_trades(tr)
        if not buys:
            print(f"{mint}: aucun achat de {', '.join(SNIPERS.values())} — hors du périmètre de cet outil")
            continue
        r = decision_features(tr, buys)
        wallet = "AfPWFykWPZZxU2CyF6BcPoY2v8VEkmYS7ZggvELK7Pv1"
        if any(t["user"] == wallet and t["type"] == "buy" and t["slot"] <= r["decision_slot"] for t in tr):
            print(f"{mint}: le wallet est déjà entré avant la décision — rien à reproduire")
            continue
        r.update(history_features(r, history))
        p = score(model, r)
        thr, rate, n = adaptive_threshold(model, history, r["t"], k)
        print(f"\n{mint}  sniper {r['sniper_first']} {r['sniper_sol']:.3f} SOL à S0+{r['sniper_offset']}")
        print(f"  score {p:.3f} | seuil adaptatif {thr:.3f} (wallet a suivi {100 * rate:.0f}% des {n} derniers, k={k:g})")
        print(f"  -> {'ACHETER' if p >= thr else 'ignorer'}")
        print(f"  premiers slots: prix {100 * (math.expm1(r['log_price_chg'])):+.0f}% | {math.expm1(r['log_sol_buys']):.2f} SOL "
              f"achetés par {r['n_buyers']} wallets | créateur: {r['creator_prev_followed']}/{r['creator_prev_n']} suivis avant")


if __name__ == "__main__":
    main()
