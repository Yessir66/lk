#!/usr/bin/env python3
"""
Step 3b: our own strategy — enter EARLY (decision at S0+2, landing LATENCY slots later at a random
position in the slot) on launches likely to attract the follower wave (the studied wallet buys around
S0+8, like other followers), and sell into it. Every recorded trade stays in the paths: the followers'
buys are the demand we sell into.

Universe: all pump.fun launches (full_universe_h2.json: every wallet token with weight 1 + the random
sample weighted to the real launch counts). Launches failing the loose filter (>= 3 buyers and >= 1 SOL
at S0+2) are never entered. Periods (fixed in advance): model fit < 16/09; choice of score, threshold and
exit rule on 16/09-21/09; test 21/09-26/09 21:03 (dense sample).
Scores: (a) P(wallet buys) — imitation; (b) predicted return of our own trade (regression on the
simulated return with a reference exit), both from decision-time features only.
"""
import hashlib
import json
import math
import os
import sys
from datetime import datetime, timezone

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

sys.path.insert(0, os.path.dirname(__file__))
import backtest as B  # noqa: E402
from exit_search import name  # noqa: E402
from fetch_candidate_paths import loose_filter  # noqa: E402
from train_full_universe import BASE, prep  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
TS = lambda *a: datetime(*a, tzinfo=timezone.utc).timestamp()
T_VAL, T_TEST, T_END = TS(2026, 9, 16), TS(2026, 9, 21), TS(2026, 9, 26, 21, 3)
FEE, FIXED, SIZE, MAX_SLOTS = 0.0125, 0.001, 0.5, 80
RULES = [{"kind": "hold", "n": n} for n in (5, 10, 15, 25, 40)] + [
    {"kind": "tpsl", "tp": 0.3, "sl": 0.2, "max_slots": 50}, {"kind": "tpsl", "tp": 0.5, "sl": 0.3, "max_slots": 75},
    {"kind": "tpsl", "tp": 1.0, "sl": 0.3, "max_slots": 75},
    {"kind": "trail", "trail": 0.2, "min_slots": 3, "max_slots": 50}, {"kind": "trail", "trail": 0.3, "min_slots": 3, "max_slots": 75}]
REF_RULE = 2  # hold 15 slots: target of the return regression


class Entry:
    def __init__(self, mint, trades, land):
        self.curve = B.Curve(trades)
        self.mint, self.land = mint, land
        vt = self.curve.vt_at(land, self.curve.random_idx(land, self._rng("entry")))
        self.tokens, _ = B.buy(vt, SIZE, FEE)
        self.entry_px = SIZE / self.tokens
        self.last = trades[-1]["slot"]
        self.px = {s: B.price(self.curve.vt_at(s) - self.tokens) for s in range(land + 1, min(self.last, land + MAX_SLOTS) + 1)}

    def _rng(self, tag):
        return np.random.default_rng(int(hashlib.sha256(f"{self.mint}{tag}".encode()).hexdigest()[:12], 16))

    def pnl(self, rule, latency):
        peak, decide = self.entry_px, None
        for s in range(self.land + 1, min(self.last, self.land + MAX_SLOTS) + 1):
            px = self.px[s]
            peak = max(peak, px)
            held, k = s - self.land, rule["kind"]
            if (k == "hold" and held >= rule["n"]) or \
               (k == "tpsl" and (px >= self.entry_px * (1 + rule["tp"]) or px <= self.entry_px * (1 - rule["sl"])
                                 or held >= rule["max_slots"])) or \
               (k == "trail" and ((px <= peak * (1 - rule["trail"]) and held >= rule["min_slots"]) or held >= rule["max_slots"])):
                decide = s
                break
        if decide is None:
            decide = min(self.last, self.land + MAX_SLOTS)
        ex = decide + latency
        vt = self.curve.vt_at(ex, self.curve.random_idx(ex, self._rng(f"exit{ex}")))
        out, _ = B.sell(vt - self.tokens, self.tokens, FEE)
        return out - SIZE - 2 * FIXED


def main():
    rows = [r for r in json.load(open(os.path.join(DATA, "full_universe_h2.json"))) if r["t"] < T_END]
    for r in rows:
        prep(r)
    cand = [r for r in rows if loose_filter(r) or r["label"]]
    report = {}
    for latency in (1, 2):
        data = []
        for r in cand:
            if not loose_filter(r):
                continue  # a wallet token failing the loose filter is not entered either
            p = os.path.join(DATA, "paths", f"{r['mint']}.json")
            if not os.path.exists(p):
                continue
            d = json.load(open(p))
            if not d["complete"] or not d["trades"]:
                continue
            e = Entry(r["mint"], d["trades"], r["s0"] + 2 + latency)
            if e.last < e.land + 20:
                continue
            data.append((r, [e.pnl(rule, latency) for rule in RULES]))
        R = [r for r, _ in data]
        P = np.array([p for _, p in data])
        w = np.array([r["weight"] for r in R])
        t = np.array([r["t"] for r in R])
        X = np.array([[float(r[f]) for f in BASE] for r in R])
        y = np.array([r["label"] for r in R])
        fit, val, test = t < T_VAL, (t >= T_VAL) & (t < T_TEST), (t >= T_TEST) & (t < T_END)
        days = lambda m: len({int(x // 86400) for x in t[m]})
        clf = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=300, min_samples_leaf=30,
                                             random_state=0).fit(X[fit], y[fit], sample_weight=np.where(y[fit] == 1, w[fit][y[fit] == 0].sum() / max(1, y[fit].sum()), w[fit]))
        reg = HistGradientBoostingRegressor(max_depth=3, learning_rate=0.05, max_iter=300, min_samples_leaf=30,
                                            random_state=0).fit(X[fit], P[fit, REF_RULE], sample_weight=w[fit])
        scores = {"imitation (P achat du wallet)": clf.predict_proba(X)[:, 1], "rendement prédit": reg.predict(X)}
        print(f"\n=== latence +{latency} slot(s) | {len(R)} lancements candidats avec trajectoire "
              f"(ajustement {fit.sum()}, validation {val.sum()}, test {test.sum()}) ===")
        # choose score, volume (launches/day) and exit rule on validation, then report test
        best = None
        for sname, s in scores.items():
            for per_day in (5, 10, 25, 50, 100):
                sel_val = select(s, w, val, per_day * days(val))
                for j, rule in enumerate(RULES):
                    v = float((P[sel_val, j] * w[sel_val]).sum())
                    if best is None or v > best[0]:
                        best = (v, sname, per_day, j)
        v, sname, per_day, j = best
        s = scores[sname]
        sel_val = select(s, w, val, per_day * days(val))
        sel_test = select(s, w, test, per_day * days(test))
        pnl_t = P[sel_test, j]; w_t = w[sel_test]
        n_tr = w_t.sum()
        print(f"  choix sur validation: score '{sname}', {per_day} lancements/jour, sortie '{name(RULES[j])}' "
              f"-> validation {v:+.2f} SOL ({v / days(val):+.2f}/jour)")
        print(f"  TEST: ~{n_tr:.0f} trades ({n_tr / days(test):.0f}/jour) | PnL {float((pnl_t * w_t).sum()):+.2f} SOL "
              f"({float((pnl_t * w_t).sum()) / days(test):+.2f}/jour) | par trade {float((pnl_t * w_t).sum() / n_tr):+.4f} SOL | "
              f"gagnants {100 * float((w_t * (pnl_t > 0)).sum() / n_tr):.0f}% | tirés: {len(pnl_t)}")
        # robustness: the same score/volume with every exit rule on test
        for jj, rule in enumerate(RULES):
            tot = float((P[sel_test, jj] * w_t).sum())
            print(f"     test avec '{name(rule)}': {tot:+.2f} SOL ({tot / max(1e-9, n_tr):+.4f}/trade)")
        report[str(latency)] = {"score": sname, "per_day": per_day, "rule": name(RULES[j]), "val_pnl": round(v, 2),
                                "test_pnl": round(float((pnl_t * w_t).sum()), 2), "test_trades": round(float(n_tr), 1),
                                "test_days": days(test)}
    json.dump(report, open(os.path.join(DATA, "early_entry_search.json"), "w"), indent=1)


def select(s, w, mask, n_target):
    """Highest-scored launches in `mask` until their weighted count reaches n_target."""
    idx = np.where(mask)[0]
    o = idx[np.argsort(-s[idx])]
    k = int(np.searchsorted(np.cumsum(w[o]), n_target)) + 1
    return o[:k]


if __name__ == "__main__":
    main()
