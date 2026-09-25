#!/usr/bin/env python3
"""
Can the first seconds of a launch predict whether the wallet buys it?

Two views of "what the wallet could see":
  - fixed horizon h: trades in slots [S0, S0+h]; a bought launch is used
    only if the wallet's buy landed after S0+h (no leakage)
  - decision-aligned: trades up to d = k-2 (k = wallet's buy slot offset);
    skipped launches get d drawn from the same distribution

Feature blocks compared on a time split (oldest 70% train / newest 30% test):
  whitelist  : creator's historical buy rate (train period, population-level)
  onchain    : first-seconds trade features
  leaders    : early buyers whose presence is far more frequent on bought
               launches (learned on train only)
  context    : what the wallet already knew (creator cadence, own history)

Metrics on test: within-creator AUC (only bought-vs-skipped pairs from the
same creator — the honest test of the first-seconds question) and a
population-weighted AUC (case-control sample reweighted to the real
launch population).
"""
import json
import os
from collections import defaultdict

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
ONCHAIN = ["dev_buy_sol", "dev_buy_pct", "n_buys_s0", "sol_buys_s0", "n_buys", "n_buyers", "sol_buys",
           "n_sells", "sol_sells", "dev_sold", "max_buy_sol", "top_buyer_share", "net_sol",
           "price_change_pct", "n_active_slots", "first_nondev_buy_offset", "small_buy_frac"]
RECENT = ["rec_n_buys", "rec_sol_buys", "rec_n_sells", "rec_sol_sells", "rec_max_buy"]
LEADERS = ["leader_sum", "leader_max", "leader_n_pos", "leader_n_neg"]
CONTEXT = ["creator_prev_gap_s", "creator_launches_1h", "bot_bought_prev_launch", "bot_buy_streak_creator",
           "bot_buys_creator_6h", "bot_last_pnl_creator", "bot_has_history_creator", "bot_secs_since_last_buy",
           "bot_recent_pnl_5"]
WHITELIST = ["creator_hist_rate"]
TRAIN_FRAC = 0.7


def within_creator_auc(y, s, groups):
    conc = pairs = 0.0
    by = defaultdict(list)
    for yi, si, g in zip(y, s, groups):
        by[g].append((yi, si))
    for items in by.values():
        pos = [si for yi, si in items if yi == 1]
        neg = np.array([si for yi, si in items if yi == 0])
        if not pos or not len(neg):
            continue
        for p in pos:
            conc += float((p > neg).sum()) + 0.5 * float((p == neg).sum())
            pairs += len(neg)
    return (conc / pairs if pairs else float("nan")), int(pairs)


def population_weights(rows):
    """Case-control -> population: each sampled skipped launch stands for N_pop/n_sample skipped
    launches of the same creator (active periods); bought launches were all sampled."""
    labels = json.load(open(os.path.join(DATA_DIR, "launch_labels.json")))
    sample = json.load(open(os.path.join(DATA_DIR, "early_sample.json")))
    ledger = json.load(open(os.path.join(DATA_DIR, "wallet_ledger.json")))
    import bisect
    bt = sorted(v["first_buy_bt"] for v in ledger.values())

    def active(t, win=1800):
        i = bisect.bisect_left(bt, t - win)
        return i < len(bt) and bt[i] <= t + win

    pop_neg = defaultdict(int)
    for r in labels:
        if r["status"] == "skipped" and active(r["created_ts"]):
            pop_neg[r["creator"]] += 1
    samp_neg = defaultdict(int)
    for r in sample:
        if r["status"] == "skipped":
            samp_neg[r["creator"]] += 1
    return [1.0 if r["label"] else pop_neg[r["creator"]] / max(1, samp_neg[r["creator"]]) for r in rows]


def leader_scores(train, min_support=5, prior=1.0):
    n_pos = sum(r["label"] for r in train)
    n_neg = len(train) - n_pos
    pos_c, neg_c = defaultdict(int), defaultdict(int)
    for r in train:
        for b in r["buyers"]:
            (pos_c if r["label"] else neg_c)[b] += 1
    scores = {}
    for w in set(pos_c) | set(neg_c):
        if pos_c[w] + neg_c[w] < min_support:
            continue
        scores[w] = float(np.log(((pos_c[w] + prior) / (n_pos + 2 * prior)) /
                                 ((neg_c[w] + prior) / (n_neg + 2 * prior))))
    return scores


def attach(rows, train, cut):
    state = json.load(open(os.path.join(DATA_DIR, "state_features.json")))
    labels = json.load(open(os.path.join(DATA_DIR, "launch_labels.json")))
    hist = defaultdict(lambda: [0, 0])
    for r in labels:
        if r["created_ts"] < cut:
            hist[r["creator"]][0] += int(r["status"] != "skipped")
            hist[r["creator"]][1] += 1
    scores = leader_scores(train)
    for r in rows:
        r.update(state.get(r["mint"], {f: 0.0 for f in CONTEXT}))
        a, n = hist[r["creator"]]
        r["creator_hist_rate"] = (a + 1) / (n + 20)
        vals = [scores[b] for b in r["buyers"] if b in scores]
        r["leader_sum"] = float(sum(vals))
        r["leader_max"] = float(max(vals)) if vals else 0.0
        r["leader_n_pos"] = sum(1 for v in vals if v > 0.7)
        r["leader_n_neg"] = sum(1 for v in vals if v < -0.7)
    return scores


def fit_eval(train, test, feats, w_test):
    Xtr = np.array([[r[f] for f in feats] for r in train], dtype=float)
    Xte = np.array([[r[f] for f in feats] for r in test], dtype=float)
    ytr = np.array([r["label"] for r in train])
    yte = np.array([r["label"] for r in test])
    res = {}
    for name, m in {
        "logreg": make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000, C=0.5)),
        "gbm": HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=300,
                                              min_samples_leaf=20, random_state=0),
    }.items():
        m.fit(Xtr, ytr)
        s = m.predict_proba(Xte)[:, 1]
        wc, pairs = within_creator_auc(yte, s, [r["creator"] for r in test])
        res[name] = {"auc_within_creator": round(wc, 3), "auc_population": round(roc_auc_score(yte, s, sample_weight=w_test), 3),
                     "pairs": pairs}
    return res


def univariate(rows, feats):
    y = [r["label"] for r in rows]
    out = {}
    for f in feats:
        s = [r[f] for r in rows]
        wc, _ = within_creator_auc(y, s, [r["creator"] for r in rows])
        out[f] = {"auc_within_creator": round(wc, 3),
                  "median_pos": round(float(np.median([r[f] for r in rows if r["label"]])), 4),
                  "median_neg": round(float(np.median([r[f] for r in rows if not r["label"]])), 4)}
    return out


def run_view(name, rows, onchain_feats):
    rows = [dict(r) for r in rows]
    ts = sorted(r["created_ts"] for r in rows)
    cut = ts[int(len(ts) * TRAIN_FRAC)]
    train = [r for r in rows if r["created_ts"] < cut]
    test = [r for r in rows if r["created_ts"] >= cut]
    n_leaders = len(attach(rows, train, cut))
    w_test = population_weights(test)
    print(f"\n=== {name} | n={len(rows)} (positifs={sum(r['label'] for r in rows)}) | "
          f"test={len(test)} (positifs={sum(r['label'] for r in test)}) | wallets leaders appris={n_leaders} ===")
    # leader features are learned on train: measure them on test only, the rest on everything
    uni = univariate(rows, onchain_feats + CONTEXT)
    uni.update({f"{f} (test)": v for f, v in univariate(test, LEADERS).items()})
    print("  univarié intra-créateur (top 12):")
    for f, v in sorted(uni.items(), key=lambda kv: -abs(kv[1]["auc_within_creator"] - 0.5))[:12]:
        print(f"    {f:26s} AUC intra={v['auc_within_creator']:.3f}  médiane achetés={v['median_pos']:.3f} "
              f"vs ignorés={v['median_neg']:.3f}")
    blocks = {
        "liste blanche seule": WHITELIST,
        "premières secondes (on-chain)": onchain_feats,
        "wallets leaders": LEADERS,
        "on-chain + leaders": onchain_feats + LEADERS,
        "contexte seul": CONTEXT,
        "on-chain + leaders + contexte": onchain_feats + LEADERS + CONTEXT,
        "tout + liste blanche": onchain_feats + LEADERS + CONTEXT + WHITELIST,
    }
    res = {}
    print(f"  {'bloc':34s} {'logreg intra':>12s} {'logreg pop':>10s} {'gbm intra':>10s} {'gbm pop':>8s}")
    for bname, feats in blocks.items():
        r = fit_eval(train, test, feats, w_test)
        res[bname] = r
        print(f"  {bname:34s} {r['logreg']['auc_within_creator']:>12.3f} {r['logreg']['auc_population']:>10.3f} "
              f"{r['gbm']['auc_within_creator']:>10.3f} {r['gbm']['auc_population']:>8.3f}")
    return {"n": len(rows), "univariate": uni, "blocks": res, "cut": cut}


def main():
    rows_all = json.load(open(os.path.join(DATA_DIR, "early_features.json")))
    kv = sorted({(r["mint"], r["k"]) for r in rows_all if r["label"] and r["k"] is not None})
    kv = sorted(k for _, k in kv)
    q = lambda p: kv[min(len(kv) - 1, int(p * len(kv)))]
    print(f"offset d'achat du bot k (slots après création), n={len(kv)}: p10={q(.1)} p25={q(.25)} "
          f"médiane={q(.5)} p75={q(.75)} p90={q(.9)}")
    report = {"k_distribution": kv, "views": {}}
    for h in sorted({r["h"] for r in rows_all}):
        rows = [r for r in rows_all if r["h"] == h]
        if sum(r["label"] for r in rows) >= 50:
            report["views"][f"h={h}"] = run_view(f"fenêtre fixe [S0, S0+{h}]", rows, ONCHAIN)
    ap = os.path.join(DATA_DIR, "aligned_features.json")
    if os.path.exists(ap):
        report["views"]["aligned"] = run_view("aligné sur la décision (d=k-2)", json.load(open(ap)),
                                              ONCHAIN + RECENT + ["d"])
    json.dump(report, open(os.path.join(DATA_DIR, "scoring_analysis.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
