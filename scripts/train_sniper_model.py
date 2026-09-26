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
FEATURES = ["sig_293", "sniper_offset", "both_snipers", "log_price_chg", "log_sol_buys",
            "n_buyers", "dev_buy_sol", "recent_follow_rate", "creator_follow_rate", "log_creator_prev_n",
            "is_cbkg"]
TARGET_PRECISIONS = [0.4, 0.5, 0.6, 0.7]
HISTORY_N = 100        # adaptive policy: look at the last N sniper buys whose outcome is visible
OUTCOME_DELAY_S = 60   # the wallet's decision on a token is public within seconds; keep a margin
POLICY_K = [1.0, 0.5]  # buy the top k x (wallet's recent follow rate) of sniper buys


def adaptive_select(p, y, t, start, k, n=HISTORY_N):
    """Rank-matching policy: the wallet keeps the same criteria but changes how many sniper buys it
    follows (27% before 21/09, ~5% on 25/09). For token i, look at the last n tokens whose outcome
    was visible, measure the wallet's follow rate r there, and buy if the score is in the top k*r of
    those tokens' scores. Only public, past information is used."""
    sel = np.zeros(len(p), dtype=bool)
    for i in range(start, len(p)):
        j = int(np.searchsorted(t, t[i] - OUTCOME_DELAY_S, side="right"))
        lo = max(0, j - n)
        if j - lo < 20:
            continue
        rate = y[lo:j].mean()
        sel[i] = p[i] >= np.quantile(p[lo:j], max(0.0, 1 - k * rate))
    return sel


def boot_ci(hits_mask, sel, reps=2000, seed=0):
    rng = np.random.default_rng(seed)
    idx = np.where(sel)[0]
    if len(idx) == 0:
        return [0.0, 0.0]
    v = [hits_mask[rng.choice(idx, len(idx))].mean() for _ in range(reps)]
    return [round(float(np.percentile(v, 2.5)), 3), round(float(np.percentile(v, 97.5)), 3)]


def x_of(r):
    r = dict(r)
    r["log_creator_prev_n"] = math.log1p(r["creator_prev_n"])
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

    # adaptive policy on the test period (scores of earlier tokens come from the same train-fitted model)
    p_all = np.concatenate([p_tr, p])
    y_all = np.array([r["label"] for r in rows])
    t_all = np.array([r["t"] for r in rows])
    half = len(train) + len(test) // 2
    print(f"politique adaptative (historique {HISTORY_N} derniers achats du sniper, résultats visibles):")
    out["adaptive"] = []
    for k in POLICY_K:
        sel_all = adaptive_select(p_all, y_all, t_all, len(train), k)
        sel = sel_all[len(train):]
        res = line(f"acheter le top {k:g} x taux de suivi récent", sel)
        res["k"], res["precision_ci95"] = k, boot_ci(y_all, sel_all)
        for name, a, b in (("1re moitié du test", len(train), half), ("2e moitié du test", half, len(rows))):
            m = sel_all[a:b]
            res[name] = {"n": int(m.sum()), "hits": int(y_all[a:b][m].sum())}
        print(f"      IC95 précision {res['precision_ci95']} | {res['1re moitié du test']} puis {res['2e moitié du test']}")
        out["adaptive"].append(res)
    json.dump(out, open(os.path.join(DATA, f"sniper_validation{suffix}.json"), "w"), indent=1)

    # walk-forward: for each day from the 5th on, fit on everything before it and apply the policy to it
    # each token is scored by the model fitted on everything before its day (tokens of the first 4 days,
    # only used as history, by the model fitted on those days)
    days = sorted({int(r["t"] // 86400) for r in rows})
    wf_p = np.empty(len(rows))
    a0 = int(np.searchsorted(t_all, days[4] * 86400))
    wf_p[:a0] = predict(fit(rows[:a0]), rows[:a0])
    for d in days[4:]:
        a = int(np.searchsorted(t_all, d * 86400))
        b = int(np.searchsorted(t_all, (d + 1) * 86400))
        if b > a:
            wf_p[a:b] = predict(fit(rows[:a]), rows[a:b])
    start = int(np.searchsorted(t_all, days[4] * 86400))
    ledger_days = [int(v["first_buy_bt"] // 86400) for v in ledger.values()]
    n_bot_wf = sum(1 for d in ledger_days if d >= days[4])
    print(f"validation glissante jour par jour ({len(rows) - start} tokens, {int(y_all[start:].sum())} suivis, "
          f"{n_bot_wf} achats du wallet sur ces jours):")
    out["walk_forward"] = {"base_precision": round(float(y_all[start:].mean()), 3)}
    print(f"  tout suivre: précision {100 * y_all[start:].mean():.1f}%")
    for k in POLICY_K:
        sel = adaptive_select(wf_p, y_all, t_all, start, k)
        h, n = int(y_all[sel].sum()), int(sel.sum())
        ci = boot_ci(y_all, sel)
        per_day = []
        for d in days[4:]:
            a, b = int(np.searchsorted(t_all, d * 86400)), int(np.searchsorted(t_all, (d + 1) * 86400))
            per_day.append(f"{int(y_all[a:b][sel[a:b]].sum())}/{int(sel[a:b].sum())}")
        print(f"  top {k:g} x taux récent: {h}/{n} précision {100 * h / max(1, n):.1f}% IC95 {ci} | "
              f"part des achats du wallet {100 * h / n_bot_wf:.1f}% | "
              f"part des tokens suivis de l'univers {100 * h / max(1, y_all[start:].sum()):.0f}%")
        print("     par jour:", " ".join(per_day))
        out["walk_forward"][f"k={k:g}"] = {"n": n, "hits": h, "precision": round(h / max(1, n), 3), "precision_ci95": ci,
                                            "share_of_wallet_buys": round(h / n_bot_wf, 3), "per_day": per_day}
    json.dump(out, open(os.path.join(DATA, f"sniper_validation{suffix}.json"), "w"), indent=1)

    final = fit(rows)
    p_fin = predict(final, rows)
    json.dump({"features": FEATURES, "coef": final[0].coef_[0].tolist(), "intercept": float(final[0].intercept_[0]),
               "mean": final[1].tolist(), "std": final[2].tolist(),
               "thresholds": {str(tp): threshold_for(p_fin, y_all, tp) for tp in TARGET_PRECISIONS},
               "policy": {"history_n": HISTORY_N, "outcome_delay_s": OUTCOME_DELAY_S, "k": POLICY_K[0]}},
              open(os.path.join(DATA, f"sniper_model{suffix}.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
