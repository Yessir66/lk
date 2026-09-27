#!/usr/bin/env python3
"""
Method 2: score EVERY pump.fun launch at S0 + H slots and buy the best ones. Evaluated against all
launches (the unbiased sample is weighted up to the real launch counts), not a sniper's buys or a
creator whitelist.

Periods (fixed before looking at results): train < 21/09; test 1 = 21/09-25/09; test 2 = 26/09
00:00-21:03 UTC (never used for any choice). Labels are known up to 26/09 21:03.
Metrics: precision = wallet tokens selected / (wallet tokens selected + estimated other launches
selected); capture = wallet tokens selected / all tokens the wallet bought in the period.
Volume policy (causal): each day, buy the launches whose score is above the threshold that would have
selected k x (wallet's buys the previous day) launches on the previous day's population.
Output: data/full_universe_eval_h<H>.json
"""
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
H = int(sys.argv[sys.argv.index("--h") + 1]) if "--h" in sys.argv else 2
TS = lambda *a: datetime(*a, tzinfo=timezone.utc).timestamp()
T_TEST1, T_TEST2, T_END = TS(2026, 9, 21), TS(2026, 9, 26), TS(2026, 9, 26, 21, 3)
BASE = ["n_buys_s0", "sol_buys_s0", "n_buys", "n_buyers", "log_sol_buys", "n_sells", "sol_sells", "dev_sold",
        "max_buy_sol", "top_buyer_share", "log_price_chg", "dev_buy_sol", "dev_buy_pct", "n_active_slots",
        "first_nondev_buy_offset", "small_buy_frac", "has_2926", "sn_4yFA", "sn_CBKg", "sn_2mqr", "sn_8Rvt",
        "sn_Fhbh", "sn_2CQg", "log_creator_prev_bought", "log_wallet_buys_1h", "log_wallet_buys_24h", "hour_sin",
        "hour_cos"]
FEATURES = BASE + ["wallet_pos", "wallet_neg"]
KS = [0.25, 0.5, 1.0, 2.0]


def prep(r):
    r["log_creator_prev_bought"] = math.log1p(r["creator_prev_bought"])
    r["log_wallet_buys_1h"] = math.log1p(r["wallet_buys_1h"])
    r["log_wallet_buys_24h"] = math.log1p(r["wallet_buys_24h"])
    r["hour_sin"], r["hour_cos"] = math.sin(2 * math.pi * r["hour"] / 24), math.cos(2 * math.pi * r["hour"] / 24)


def wallet_scores(train, min_support=8):
    """Early buyers' log odds of appearing on the wallet's tokens vs on (weighted) other launches."""
    pos_n = sum(1 for r in train if r["label"])
    neg_w = sum(r["weight"] for r in train if not r["label"])
    a, b, sup = defaultdict(float), defaultdict(float), defaultdict(int)
    for r in train:
        for u in r["buyers"]:
            sup[u] += 1
            if r["label"]:
                a[u] += 1
            else:
                b[u] += r["weight"]
    return {u: math.log(((a[u] + 0.5) / pos_n) / ((b[u] + 0.5 * neg_w / max(1, pos_n)) / neg_w))
            for u in sup if sup[u] >= min_support}


def attach(rows, ws):
    for r in rows:
        v = [ws[u] for u in r["buyers"] if u in ws]
        r["wallet_pos"] = sum(x for x in v if x > 0)
        r["wallet_neg"] = -sum(x for x in v if x < 0)


def X(rows, feats):
    return np.array([[float(r[f]) for f in feats] for r in rows])


def train_models(train, feats):
    y = np.array([r["label"] for r in train])
    w = np.array([r["weight"] for r in train])
    w_bal = np.where(y == 1, w[y == 0].sum() / max(1, y.sum()), w)  # balance classes for fitting
    Xt = X(train, feats)
    mu, sd = Xt.mean(0), Xt.std(0) + 1e-9
    lr = LogisticRegression(C=0.3, max_iter=5000).fit((Xt - mu) / sd, y, sample_weight=w_bal / w_bal.mean())
    gb = HistGradientBoostingClassifier(max_depth=4, learning_rate=0.05, max_iter=400, min_samples_leaf=30,
                                        l2_regularization=1.0, random_state=0).fit(Xt, y, sample_weight=w_bal / w_bal.mean())
    return {"logreg": lambda R: lr.predict_proba((X(R, feats) - mu) / sd)[:, 1],
            "gbm": lambda R: gb.predict_proba(X(R, feats))[:, 1]}


def weighted_auc(y, s, w):
    o = np.argsort(s)
    y, w = y[o], w[o]
    neg_cum = np.cumsum(np.where(y == 0, w, 0))
    return float(np.sum(np.where(y == 1, w * (neg_cum - np.where(y == 0, w, 0)), 0)) / (w[y == 1].sum() * w[y == 0].sum()))


def evaluate(rows, s, n_wallet, label):
    y = np.array([r["label"] for r in rows]); w = np.array([r["weight"] for r in rows])
    out = {"auc": round(weighted_auc(y, s, w), 3), "fixed_rate": [], "volume_policy": []}
    order = np.argsort(-s)
    cw = np.cumsum(w[order])
    days = (np.array([r["t"] for r in rows]) // 86400).astype(int)
    n_days = len(set(days))
    print(f"  {label}: AUC pondérée {out['auc']} | {len(rows)} lignes, {int(y.sum())} achats du wallet retenus "
          f"sur {n_wallet} ({n_days} jours)")
    for per_day in (10, 25, 50, 100, 200):
        kk = int(np.searchsorted(cw, per_day * n_days)) + 1
        sel = order[:kk]
        tp = y[sel].sum(); fp = w[sel][y[sel] == 0].sum()
        res = {"launches_per_day": per_day, "precision": round(float(tp / max(1e-9, tp + fp)), 3),
               "capture": round(float(tp / n_wallet), 3), "tp": int(tp), "fp_est": round(float(fp), 1),
               "fp_sampled": int((y[sel] == 0).sum())}
        out["fixed_rate"].append(res)
        print(f"    top {per_day:3d} lancements/jour: précision {100 * res['precision']:5.1f}% | capture "
              f"{100 * res['capture']:4.1f}% | {res['tp']} vrais, ~{res['fp_est']:.0f} faux (sur {res['fp_sampled']} tirés)")
    return out


def volume_policy(rows_all, s_all, wallet_day_counts, start, end, k):
    """Each day d in [start, end): threshold = score above which, on day d-1, the (weighted) number of
    launches equals k x wallet buys on day d-1. Returns selection mask on rows_all."""
    t = np.array([r["t"] for r in rows_all]); w = np.array([r["weight"] for r in rows_all])
    days = (t // 86400).astype(int)
    sel = np.zeros(len(rows_all), bool)
    for d in range(int(start // 86400), int(math.ceil(end / 86400))):
        prev = days == d - 1
        target = k * wallet_day_counts.get(d - 1, 0)
        if not prev.any() or target <= 0:
            continue
        o = np.argsort(-s_all[prev]); cw = np.cumsum(w[prev][o])
        i = min(len(o) - 1, int(np.searchsorted(cw, target)))
        thr = s_all[prev][o][i]
        today = (days == d) & (t >= start) & (t < end)
        sel |= today & (s_all >= thr)
    return sel


def main():
    rows = [r for r in json.load(open(os.path.join(DATA, f"full_universe_h{H}.json"))) if r["t"] < T_END]
    for r in rows:
        prep(r)
    ledger = json.load(open(os.path.join(DATA, "wallet_ledger.json")))
    wb = dict((m, v["first_buy_bt"]) for m, v in ledger.items())
    extra = os.path.join(DATA, "blind", "wallet_buys.json")
    if os.path.exists(extra):
        for m, b in json.load(open(extra))["buys"].items():
            wb.setdefault(m, b["bt"])
    wallet_day_counts = defaultdict(int)
    for bt in wb.values():
        wallet_day_counts[int(bt // 86400)] += 1
    n_wallet = lambda a, b: sum(1 for bt in wb.values() if a <= bt < b)
    t0 = min(r["t"] for r in rows)
    train = [r for r in rows if r["t"] < T_TEST1]
    tests = {"test 1 (21-25/09)": (T_TEST1, T_TEST2), "test 2 (26/09, jamais vu)": (T_TEST2, T_END)}
    # buyer-identity scores: test rows use scores learned on the whole train period; train rows get
    # out-of-fold scores (5 time blocks) so the model does not learn from in-sample target encoding
    ws = wallet_scores(train)
    attach([r for r in rows if r["t"] >= T_TEST1], ws)
    blocks = np.array_split(np.arange(len(train)), 5)
    for bl in blocks:
        inside = set(bl.tolist())
        attach([train[i] for i in bl], wallet_scores([r for i, r in enumerate(train) if i not in inside]))
    report = {"h": H, "n_rows": len(rows), "wallet_scores": len(ws), "models": {}}
    for feats_name, feats in (("sans identité des acheteurs", BASE), ("avec identité des acheteurs", FEATURES)):
        models = train_models(train, feats)
        for mname, predict in models.items():
            key = f"{mname} / {feats_name}"
            print(f"\n=== {key} ===")
            s_all = predict(rows)
            rep = {}
            for tname, (a, b) in tests.items():
                idx = [i for i, r in enumerate(rows) if a <= r["t"] < b]
                rep[tname] = evaluate([rows[i] for i in idx], s_all[idx], n_wallet(a, b), tname)
                y = np.array([r["label"] for r in rows]); w = np.array([r["weight"] for r in rows])
                rep[tname]["volume_policy"] = []
                for k in KS:
                    sel = volume_policy(rows, s_all, wallet_day_counts, a, b, k)
                    tp = y[sel].sum(); fp = w[sel][y[sel] == 0].sum()
                    r_ = {"k": k, "precision": round(float(tp / max(1e-9, tp + fp)), 3),
                          "capture": round(float(tp / n_wallet(a, b)), 3), "tp": int(tp), "fp_est": round(float(fp), 1)}
                    rep[tname]["volume_policy"].append(r_)
                    print(f"    volume = {k:g} x achats du wallet la veille: précision {100 * r_['precision']:5.1f}% | "
                          f"capture {100 * r_['capture']:4.1f}% | {r_['tp']} vrais, ~{r_['fp_est']:.0f} faux")
            report["models"][key] = rep
    json.dump(report, open(os.path.join(DATA, f"full_universe_eval_h{H}.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
