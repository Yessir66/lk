#!/usr/bin/env python3
"""
Final scoring algorithm: P(wallet buys | first ~2 slots of a launch).

Window: trades in slots [S0, S0+2] (S0 = creation slot = the creator's
first buy), i.e. the first ~0.8-1.2 s. The wallet's own buys land later
for ~91% of launches it buys (median S0+9), so this is information it
could have had before acting.

Features (4, all with an intuitive sign):
  log_price_chg   log(1 + % price move from the dev buy to the last trade)  (+)
  has_3sol_buy    a single ~3 SOL buy (2.9-3.1 SOL) — the sniper signature (+)
  wallet_pos      sum of positive per-wallet scores among early buyers     (+)
  wallet_neg      sum of |negative| per-wallet scores among early buyers   (-)
  Per-wallet score = log odds ratio of being an early buyer on bought vs
  skipped launches, stratified by creator (Mantel-Haenszel). Adding volume
  or buyer-count features was tested and did not help (worse out-of-sample
  AUC and sign flips from collinearity).
Hard veto: early buyers seen on >= 15 launches with <= 2% of them bought
by the wallet force the score down (x0.05). Learned on train only for the
validation, it hit 63 test launches and none of them was bought.

Model: standardized logistic regression (transparent weights).
Validation: train on the oldest 70% of launches, test on the newest 30%;
within-creator AUC and population-reweighted AUC with bootstrap 95% CIs.
The exported model is then refit on everything.

Outputs: data/scoring_model.json, data/scoring_validation.json
"""
import json
import os
import sys
from collections import defaultdict

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(__file__))
from analyze_scoring import leader_scores_mh, population_weights, within_creator_auc  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
H = 2
VETO_MIN_SUPPORT = 15
VETO_MAX_RATE = 0.02
VETO_FACTOR = 0.05
FEATURES = ["log_price_chg", "has_3sol_buy", "wallet_pos", "wallet_neg"]


def token_features(f, wallet_scores):
    """f: output of build_early_features.window_features at horizon H (must include 'buyers')."""
    vals = [wallet_scores[b] for b in f["buyers"] if b in wallet_scores]
    return {
        "log_price_chg": float(np.log1p(max(0.0, f["price_change_pct"]))),
        "has_3sol_buy": float(any(2.9 <= s < 3.1 for s in f["buy_sizes"])),
        "wallet_pos": float(sum(v for v in vals if v > 0)),
        "wallet_neg": float(-sum(v for v in vals if v < 0)),
    }


def fit(rows, wallet_scores):
    X = np.array([[token_features(r, wallet_scores)[k] for k in FEATURES] for r in rows])
    y = np.array([r["label"] for r in rows])
    mu, sd = X.mean(0), X.std(0) + 1e-9
    m = LogisticRegression(C=0.3, max_iter=5000).fit((X - mu) / sd, y)
    return m, mu, sd


def hard_veto_wallets(rows, min_support=VETO_MIN_SUPPORT, max_rate=VETO_MAX_RATE):
    """Early buyers whose presence (almost) never coincides with a wallet buy: seen on >= min_support
    launches with a bought rate <= max_rate. Their presence forces the score down."""
    cnt = defaultdict(lambda: [0, 0])
    for r in rows:
        for b in r["buyers"]:
            cnt[b][0] += r["label"]
            cnt[b][1] += 1
    return {b for b, (a, n) in cnt.items() if n >= min_support and a / n <= max_rate}


def score(rows, m, mu, sd, wallet_scores, veto=frozenset()):
    X = np.array([[token_features(r, wallet_scores)[k] for k in FEATURES] for r in rows])
    p = m.predict_proba((X - mu) / sd)[:, 1]
    hit = np.array([any(b in veto for b in r["buyers"]) for r in rows])
    return np.where(hit, p * VETO_FACTOR, p)


def main():
    rows = [r for r in json.load(open(os.path.join(DATA_DIR, "early_features.json"))) if r["h"] == H]
    ts = sorted(r["created_ts"] for r in rows)
    cut = ts[int(len(ts) * 0.7)]
    train = [r for r in rows if r["created_ts"] < cut]
    test = [r for r in rows if r["created_ts"] >= cut]

    ws_train = leader_scores_mh(train)
    veto_train = hard_veto_wallets(train)
    m, mu, sd = fit(train, ws_train)
    s = score(test, m, mu, sd, ws_train, veto_train)
    y = np.array([r["label"] for r in test])
    w = np.array(population_weights(test))
    grp = [r["creator"] for r in test]

    rng = np.random.default_rng(0)
    pos, neg = np.where(y == 1)[0], np.where(y == 0)[0]
    intra, pop = [], []
    for _ in range(500):
        b = np.concatenate([rng.choice(pos, len(pos)), rng.choice(neg, len(neg))])
        intra.append(within_creator_auc(y[b], s[b], [grp[i] for i in b])[0])
        pop.append(roc_auc_score(y[b], s[b], sample_weight=w[b]))
    ci = lambda a: [round(float(np.mean(a)), 3), round(float(np.percentile(a, 2.5)), 3),
                    round(float(np.percentile(a, 97.5)), 3)]

    # operating points on the population-reweighted test set: flag the top-q launches
    order = np.argsort(-s)
    tot_w, tot_pos = w.sum(), (w * y).sum()
    ops = []
    for q in [0.02, 0.05, 0.10, 0.20, 0.30]:
        cum_w = np.cumsum(w[order])
        k = int(np.searchsorted(cum_w, q * tot_w)) + 1
        sel = order[:k]
        ops.append({"flag_rate": q, "threshold": round(float(s[order[k - 1]]), 4),
                    "recall": round(float((w[sel] * y[sel]).sum() / tot_pos), 3),
                    "precision": round(float((w[sel] * y[sel]).sum() / w[sel].sum()), 3)})

    validation = {"horizon_slots": H, "train_until": cut, "n_train": len(train), "n_test": len(test),
                  "n_test_pos": int(y.sum()), "auc_within_creator_ci95": ci(intra),
                  "auc_population_ci95": ci(pop), "base_rate_population": round(float(tot_pos / tot_w), 4),
                  "operating_points_test": ops, "n_hard_veto_wallets_train": len(veto_train),
                  "coefficients_train": dict(zip(FEATURES, [round(float(c), 3) for c in m.coef_[0]]))}
    json.dump(validation, open(os.path.join(DATA_DIR, "scoring_validation.json"), "w"), indent=1)

    print(f"Validation (train < {cut}, test = {len(test)} lancements dont {int(y.sum())} achetés)")
    print(f"  AUC intra-créateur : {ci(intra)[0]} [IC95 {ci(intra)[1]}-{ci(intra)[2]}]")
    print(f"  AUC population     : {ci(pop)[0]} [IC95 {ci(pop)[1]}-{ci(pop)[2]}]")
    print(f"  taux d'achat de base (population): {100 * tot_pos / tot_w:.1f}%")
    print("  si on signale le top X% des lancements (créateurs suivis), sur la période de test :")
    for o in ops:
        print(f"    top {100 * o['flag_rate']:4.0f}% -> {100 * o['recall']:5.1f}% des achats du bot captés, "
              f"précision {100 * o['precision']:5.1f}%")
    print("  poids (variables standardisées):", validation["coefficients_train"])

    # refit on everything for the exported model
    ws_all = leader_scores_mh(rows)
    veto_all = hard_veto_wallets(rows)
    m, mu, sd = fit(rows, ws_all)
    s_all = score(rows, m, mu, sd, ws_all, veto_all)
    w_all = np.array(population_weights(rows))
    order = np.argsort(-s_all)
    cum = np.cumsum(w_all[order])
    thr = {f"top_{int(q * 100)}pct": float(s_all[order[int(np.searchsorted(cum, q * w_all.sum()))]])
           for q in [0.05, 0.10, 0.20]}
    model = {
        "horizon_slots": H, "features": FEATURES, "mean": mu.tolist(), "std": sd.tolist(),
        "coef": m.coef_[0].tolist(), "intercept": float(m.intercept_[0]),
        "thresholds": thr, "hard_veto_wallets": sorted(veto_all), "veto_factor": VETO_FACTOR,
        "wallet_scores": {k: round(v, 4) for k, v in sorted(ws_all.items(), key=lambda kv: -abs(kv[1]))},
        "creators": sorted({r["creator"] for r in rows}),
        "note": "Scores are case-control probabilities (skipped launches were subsampled ~3:1); use the "
                "thresholds (calibrated on the population) rather than the raw probability.",
    }
    json.dump(model, open(os.path.join(DATA_DIR, "scoring_model.json"), "w"), indent=1)
    print(f"\nmodèle final exporté: {len(ws_all)} wallets notés, {len(veto_all)} wallets veto, seuils {({k: round(v, 3) for k, v in thr.items()})}")
    print("poids finaux:", dict(zip(FEATURES, [round(float(c), 3) for c in m.coef_[0]])))


if __name__ == "__main__":
    main()
