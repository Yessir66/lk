#!/usr/bin/env python3
"""
Which sniper buys does the wallet follow? Exact precision on the sniper universe
(data/sniper_universe.json, see build_sniper_universe.py).

Time split: oldest 70% train, newest 30% test. Reported on test:
  - simple rules (no fitting)
  - logistic regression on decision-time features, precision / share of the wallet's buys
    captured at several score thresholds (thresholds fixed on train)
Output: data/sniper_model.json (refit on everything) and data/sniper_validation.json
"""
import json
import math
import os
import sys

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
FEATURES = ["sig_293", "sniper_offset", "both_snipers", "veto_present", "log_price_chg", "log_sol_buys",
            "n_buyers", "dev_buy_sol", "recent_follow_rate", "creator_follow_rate", "log_creator_prev_n",
            "wallet_busy", "log_bot_buys_1h", "is_cbkg"]
TARGET_PRECISIONS = [0.4, 0.5, 0.6, 0.7]


def x_of(r):
    r = dict(r)
    r["log_creator_prev_n"] = math.log1p(r["creator_prev_n"])
    r["log_bot_buys_1h"] = math.log1p(r["bot_buys_1h"])
    r["is_cbkg"] = int(r["sniper_first"] == "CBKg")
    return [float(r[k]) for k in FEATURES]


def fit(rows):
    X = np.array([x_of(r) for r in rows])
    y = np.array([r["label"] for r in rows])
    mu, sd = X.mean(0), X.std(0) + 1e-9
    m = LogisticRegression(C=0.5, max_iter=5000).fit((X - mu) / sd, y)
    return m, mu, sd


def predict(model, rows):
    m, mu, sd = model
    return m.predict_proba((np.array([x_of(r) for r in rows]) - mu) / sd)[:, 1]


def threshold_for(p, y, target):
    """Lowest threshold whose precision on (p, y) is >= target (None if never reached)."""
    order = np.argsort(-p)
    hits = np.cumsum(y[order])
    prec = hits / np.arange(1, len(y) + 1)
    ok = [i for i in range(len(y)) if prec[i] >= target and i >= 9]
    return float(p[order[max(ok)]]) if ok else None


def main():
    delay = int(sys.argv[sys.argv.index("--delay") + 1]) if "--delay" in sys.argv else 0
    suffix = "" if delay == 0 else f"_d{delay}"
    rows = json.load(open(os.path.join(DATA, f"sniper_universe{suffix}.json")))
    ledger = json.load(open(os.path.join(DATA, "wallet_ledger.json")))
    cut = rows[int(len(rows) * 0.7)]["t"]
    train = [r for r in rows if r["t"] < cut]
    test = [r for r in rows if r["t"] >= cut]
    n_bot_test = sum(1 for v in ledger.values() if v["first_buy_bt"] >= cut)
    y = np.array([r["label"] for r in test])
    print(f"univers: {len(rows)} tokens | train {len(train)} (achetés {sum(r['label'] for r in train)}) | "
          f"test {len(test)} (achetés {int(y.sum())}) | achats du wallet sur la période de test: {n_bot_test}")

    def line(name, sel):
        sel = np.array(sel, dtype=bool)
        k, h = int(sel.sum()), int(y[sel].sum())
        print(f"  {name:52s} {h:4d}/{k:<4d} précision {100 * h / max(1, k):5.1f}% | "
              f"part des achats du wallet {100 * h / n_bot_test:4.1f}%")
        return {"rule": name, "n": k, "hits": h, "precision": round(h / max(1, k), 3),
                "share_of_wallet_buys": round(h / n_bot_test, 3)}

    out = {"n": len(rows), "n_test": len(test), "cut": cut, "rules": [], "model": []}
    print("règles simples (période de test):")
    out["rules"].append(line("tout suivre", [True] * len(test)))
    out["rules"].append(line("sniper ~2.93 SOL", [r["sig_293"] for r in test]))
    out["rules"].append(line("taux de suivi récent >= 30%", [r["recent_follow_rate"] >= 0.3 for r in test]))
    out["rules"].append(line("créateur déjà suivi (>= 1 fois dans l'univers)", [r["creator_prev_followed"] >= 1 for r in test]))

    model = fit(train)
    p_tr, p = predict(model, train), predict(model, test)
    y_tr = np.array([r["label"] for r in train])
    print(f"régression logistique: AUC test {roc_auc_score(y, p):.3f} (train {roc_auc_score(y_tr, p_tr):.3f})")
    print("  poids:", {k: round(float(c), 2) for k, c in zip(FEATURES, model[0].coef_[0])})
    for tp in TARGET_PRECISIONS:
        th = threshold_for(p_tr, y_tr, tp)
        if th is None:
            continue
        res = line(f"score >= seuil visant {int(100 * tp)}% (seuil fixé sur train: {th:.2f})", p >= th)
        res["target"], res["threshold"] = tp, th
        out["model"].append(res)
    for q in (0.1, 0.2, 0.3, 0.5):
        k = max(1, int(q * len(test)))
        sel = np.zeros(len(test), dtype=bool)
        sel[np.argsort(-p)[:k]] = True
        out["model"].append(line(f"top {int(100 * q)}% du score", sel))
    json.dump(out, open(os.path.join(DATA, f"sniper_validation{suffix}.json"), "w"), indent=1)

    final = fit(rows)
    p_all, y_all = predict(final, rows), np.array([r["label"] for r in rows])
    json.dump({"features": FEATURES, "coef": final[0].coef_[0].tolist(), "intercept": float(final[0].intercept_[0]),
               "mean": final[1].tolist(), "std": final[2].tolist(),
               "thresholds": {str(tp): threshold_for(p_all, y_all, tp) for tp in TARGET_PRECISIONS}},
              open(os.path.join(DATA, f"sniper_model{suffix}.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
