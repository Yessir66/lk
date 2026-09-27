#!/usr/bin/env python3
"""
Step 3c: PnL of "enter right after 4yFAz7dp's buy". Decision at the sniper's buy slot (visible once its
transaction lands), our entry LATENCY slots later at a random in-slot position, size 0.5 SOL, calibrated
costs. All recorded trades are kept in the path (we sell into the follower wave, wallet's included where
present) — except on the blind-test day (26/09), whose paths had the wallet's own trades stripped at
fetch time, so that day slightly understates follower demand.

Universe: every token where 4yFAz7dp bought first (sniper_universe.json, sniper_first == '4yFA'),
restricted to ones with a fetched path (data/paths/<mint>.json, via fetch_candidate_paths.py --sniper).
No sampling weights needed: every trigger is a real, exhaustively-collected event.

Periods (same cut points as train_full_universe.py / early_entry_search.py, fixed in advance):
  fit < 16/09 | validation 16/09-21/09 (choose score, volume, exit rule) | test 21/09-26/09 21:03.
Scores: (a) follow every trigger, (b) sig_293 only, (c) regression on the simulated return with a
reference exit (5 slots after the sniper), from decision-time features only.
Output: data/sniper_pnl_search.json
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

sys.path.insert(0, os.path.dirname(__file__))
import backtest as B  # noqa: E402
from exit_search import name  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
TS = lambda *a: datetime(*a, tzinfo=timezone.utc).timestamp()
T_VAL, T_TEST, T_END = TS(2026, 9, 16), TS(2026, 9, 21), TS(2026, 9, 26, 21, 3)
FEE, FIXED, SIZE, MAX_SLOTS = 0.0125, 0.001, 0.5, 80
RULES = [{"kind": "hold", "n": n} for n in (3, 5, 8, 12, 20, 30)] + [
    {"kind": "tpsl", "tp": 0.2, "sl": 0.15, "max_slots": 40}, {"kind": "tpsl", "tp": 0.3, "sl": 0.2, "max_slots": 50},
    {"kind": "tpsl", "tp": 0.5, "sl": 0.3, "max_slots": 75}, {"kind": "tpsl", "tp": 1.0, "sl": 0.3, "max_slots": 75},
    {"kind": "trail", "trail": 0.15, "min_slots": 3, "max_slots": 40}, {"kind": "trail", "trail": 0.2, "min_slots": 3, "max_slots": 50},
    {"kind": "trail", "trail": 0.3, "min_slots": 3, "max_slots": 75}]
REF_RULE = 2  # hold 8 slots
FEATURES = ["sniper_sol", "sig_293", "sniper_small", "sniper_offset", "both_snipers", "veto_present",
            "log_price_chg", "log_sol_buys", "n_buyers", "dev_buy_sol", "n_sells", "hour",
            "recent_follow_rate", "creator_follow_rate", "creator_prev_n"]


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


def select_topn(s, mask, n_target):
    idx = np.where(mask)[0]
    o = idx[np.argsort(-s[idx])]
    return o[:min(n_target, len(o))]


def main():
    rows = [r for r in json.load(open(os.path.join(DATA, "sniper_universe.json")))
            if r["sniper_first"] == "4yFA" and r["t"] < T_END]
    data = []
    for latency in (1, 2):
        pass
    report = {}
    built = []
    for r in rows:
        p = os.path.join(DATA, "paths", f"{r['mint']}.json")
        if not os.path.exists(p):
            continue
        d = json.load(open(p))
        if not d["complete"] or not d["trades"]:
            continue
        built.append((r, d["trades"]))
    print(f"{len(built)}/{len(rows)} déclenchements 4yFAz7dp avec trajectoire récupérée")

    for latency in (1, 2):
        R, P, X, t = [], [], [], []
        for r, tr in built:
            land = r["sniper_slot"] + latency
            e = Entry(r["mint"], tr, land)
            if e.last < e.land + 15:
                continue
            R.append(r)
            P.append([e.pnl(rule, latency) for rule in RULES])
            X.append([float(r.get(f, 0.0)) for f in FEATURES])
            t.append(r["t"])
        P, X, t = np.array(P), np.array(X), np.array(t)
        y_ref = P[:, REF_RULE]
        fit, val, test = t < T_VAL, (t >= T_VAL) & (t < T_TEST), (t >= T_TEST) & (t < T_END)
        n_days = lambda m: max(1, len({int(x // 86400) for x in t[m]}))
        reg = HistGradientBoostingRegressor(max_depth=3, learning_rate=0.05, max_iter=200, min_samples_leaf=20,
                                            random_state=0).fit(X[fit], y_ref[fit])
        scores = {"tout suivre": np.ones(len(R)), "signature ~2.926 SOL": np.array([r["sig_293"] for r in R], float),
                  "rendement prédit": reg.predict(X)}
        print(f"\n=== latence +{latency} slot(s) après l'achat de 4yFAz7dp | {len(R)} déclenchements exploitables "
              f"(ajustement {fit.sum()}, validation {val.sum()}, test {test.sum()}) ===")
        best = None
        for sname, s in scores.items():
            per_day_opts = (0,) if sname == "tout suivre" else (3, 5, 10, 20, 50)
            for per_day in ([1e9] if sname == "tout suivre" else per_day_opts):
                sel_val = select_topn(s, val, int(per_day * n_days(val)) if per_day < 1e8 else val.sum())
                if len(sel_val) < 5:
                    continue
                for j in range(len(RULES)):
                    v = float(P[sel_val, j].sum())
                    per_trade = v / len(sel_val)
                    if best is None or v > best[0]:
                        best = (v, sname, per_day, j, len(sel_val), per_trade)
        v, sname, per_day, j, n_val, pt_val = best
        s = scores[sname]
        sel_val = select_topn(s, val, int(per_day * n_days(val)) if per_day < 1e8 else val.sum())
        sel_test = select_topn(s, test, int(per_day * n_days(test)) if per_day < 1e8 else test.sum())
        pd_str = "toutes" if per_day >= 1e8 else f"{per_day}/jour"
        print(f"  choix sur validation: score '{sname}' ({pd_str}), sortie '{name(RULES[j])}' -> "
              f"{v:+.2f} SOL sur {n_val} trades ({pt_val:+.4f}/trade)")
        pnl_t = P[sel_test, j]
        n_t = len(pnl_t)
        tot_t = float(pnl_t.sum())
        print(f"  TEST (21-26/09): {n_t} trades ({n_t / n_days(test):.1f}/jour) | PnL {tot_t:+.2f} SOL "
              f"({tot_t / n_days(test):+.2f}/jour) | par trade {tot_t / max(1, n_t):+.4f} SOL | "
              f"gagnants {100 * (pnl_t > 0).mean() if n_t else 0:.0f}%")
        if n_t > 1:
            boot = [pnl_t[np.random.default_rng(i).integers(0, n_t, n_t)].mean() for i in range(1000)]
            print(f"     IC95 par trade: [{np.percentile(boot, 2.5):+.4f}, {np.percentile(boot, 97.5):+.4f}]")
        for jj, rule in enumerate(RULES):
            tot = float(P[sel_test, jj].sum())
            print(f"     avec '{name(rule)}': {tot:+.2f} SOL ({tot / max(1, n_t):+.4f}/trade)")
        report[str(latency)] = {"n_total": len(R), "score": sname, "per_day": None if per_day >= 1e8 else per_day,
                                "rule": name(RULES[j]), "val_pnl": round(v, 2), "test_n": n_t,
                                "test_pnl": round(tot_t, 2), "test_per_trade": round(tot_t / max(1, n_t), 4)}
    json.dump(report, open(os.path.join(DATA, "sniper_pnl_search.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
