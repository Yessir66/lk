#!/usr/bin/env python3
"""
Re-entry ("rachat") test: after F1R has fully sold a token, buy it again (0) if it comes back, to catch the
big winners F1R sold too early. Grid of triggers x managements, chosen on October (day-block CV), checked on
September. Result (20/10): no setting is positive in October; September is positive only through one x180 token.

Usage: reentry_test.py   (needs data/musk/paths3h.pkl and paths3h_sept.pkl from the 3 h path build)
"""
import collections
import os
import pickle
import sys
import time
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
import flow_backtest as B

TZ = 7200
day = lambda p: int(time.strftime("%d", time.gmtime(p.bt + TZ)))


class Sub:
    """position bought at index j of path p (decision at j - DELAY), as a Path-like object for B.evaluate"""
    def __init__(self, p, j, stake):
        e = p.x[j] * (1 + B.SLIP + p.impact)
        self.x = p.x[j:] / e
        self.fill = p.fill[j:] / e
        self.runmax = np.maximum.accumulate(self.x)
        self.A = {k: v[j:] for k, v in p.A.items()}
        self.fee, self.gas, self.stake, self.impact = p.fee, p.gas, stake, p.impact


def trigger(p, ex, kind, a, b, cd):
    """decision index of the re-entry after the F1R exit at ex, or None"""
    x, n = p.x, len(p.x)
    s = ex + cd
    if s >= n - 60:
        return None
    if kind == "prix":                      # price >= a x our first entry and >= b x the price we sold at
        m = (x >= a) & (x >= b * x[ex])
        m[:s] = False
        return B.first(m, s)
    if kind == "record":                    # new high: >= a x the high before our exit, and >= b x first entry
        m = (x >= a * x[:ex + 1].max()) & (x >= b)
        m[:s] = False
        return B.first(m, s)
    if kind == "flux":                      # inside the 45 min of trades: >= a new wallets in 60 s, price >= b
        nn = min(n, p.n45)
        m = np.zeros(n, bool)
        m[:nn] = (p.A["new1"][:nn] >= a) & (x[:nn] >= b)
        m[:s] = False
        return B.first(m, s)


def mgmt_rule(sl, tp, trail):
    r = dict(sl0=sl, trail=trail)
    if tp:
        r["levels"] = [tp]
        r["sl1"] = None
    return r


def run_one(p, base, ex, trig, mg, stake=50.0):
    i = trigger(p, ex, *trig)
    if i is None:
        return base, 0.0, None
    j = min(i + B.DELAY, len(p.x) - 1)
    q = Sub(p, j, stake)
    return base, B.evaluate(q, **mgmt_rule(*mg)), i


TRIGS = ([("prix", a, b, cd) for a in (1.2, 1.5, 2.0, 3.0) for b in (1.0, 1.5, 2.0) for cd in (30, 180)] +
         [("record", a, b, cd) for a in (1.0, 1.2, 1.5) for b in (1.0, 1.5, 2.0) for cd in (30, 180)] +
         [("flux", a, b, cd) for a in (40, 70, 100, 150) for b in (0.8, 1.0, 1.3) for cd in (30, 180)])
MGS = [(sl, tp, tr) for sl in (0.25, 0.35, 0.5) for tp in (None, (2.0, 0.3), (2.0, 0.5))
       for tr in ((0.4, 1.0), (0.5, 1.0), (0.5, 2.0), (0.6, 1.5))]


MGS = MGS + ["F1R"]          # re-entry managed by F1R itself


def prep(P):
    out = []
    for p in P:
        pnl, ex = B.evaluate(p, **B.runner_rule(), return_exit=True)
        out.append((p, pnl, ex))
    return out



def grid(Q):
    res = {}
    for tg in TRIGS:
        idx = [trigger(p, ex, *tg) for p, _, ex in Q]
        for mg in MGS:
            v = np.zeros(len(Q))
            for k, ((p, b, ex), i) in enumerate(zip(Q, idx)):
                if i is not None:
                    q = Sub(p, min(i + B.DELAY, len(p.x) - 1), 50.0)
                    v[k] = B.evaluate(q, **(B.runner_rule() if mg == "F1R" else mgmt_rule(*mg)))
            res[(tg, mg)] = v
    return res


if __name__ == "__main__":
    O = prep(pickle.load(open("data/musk/paths3h.pkl", "rb")))
    S = prep(pickle.load(open("data/musk/paths3h_sept.pkl", "rb")))
    for lab, Q in (("Octobre", O), ("Septembre", S)):
        r = np.array([(p.x[ex + 1:].max(), ex) for p, _, ex in Q])
        print(f"{lab}: {len(Q)} achats, F1R {sum(b for _, b, _ in Q):+.0f}$ ; après la vente F1R, le token monte ensuite à "
              + ", ".join(f"≥x{k}: {(r[:, 0] >= k).sum()}" for k in (2, 3, 5, 10)) + " (vs notre 1re entrée)")
    ro, rs = grid(O), grid(S)
    days = np.array([day(p) for p, _, _ in O])
    folds = [(1, 5), (6, 10), (11, 15), (16, 19)]
    cv = 0.0
    for a, b in folds:
        te = (days >= a) & (days <= b)
        best = max(ro, key=lambda k: ro[k][~te].sum())
        cv += ro[best][te].sum()
    big = int(np.argmax([np.nanmax(p.x) for p, _, _ in S]))
    best = max(ro, key=lambda k: ro[k].sum())
    vo = np.array([v.sum() for v in ro.values()])
    vs = np.array([rs[k].sum() for k in ro])
    vs2 = np.array([rs[k].sum() - rs[k][big] for k in ro])
    print(f"{len(ro)} réglages du rachat. Octobre : {(vo > 0).sum()} positifs, médiane {np.median(vo):+.0f}$, "
          f"validation croisée par blocs de jours {cv:+.0f}$")
    print(f"Meilleur réglage d'octobre {best}: octobre {ro[best].sum():+.0f}$, septembre {rs[best].sum():+.0f}$")
    print(f"Septembre : {(vs > 0).sum()} positifs (médiane {np.median(vs):+.0f}$) ; sans le token x180 : "
          f"{(vs2 > 0).sum()} positifs (médiane {np.median(vs2):+.0f}$)")
    fam = collections.defaultdict(list)
    for k in ro:
        fam[(k[0][0], "F1R" if k[1] == "F1R" else "stop/suiveur")].append((ro[k].sum(), rs[k].sum(), rs[k].sum() - rs[k][big]))
    for k, l in sorted(fam.items()):
        a = np.array(l)
        print(f"  {k[0]:7s} {k[1]:12s} médianes : octobre {np.median(a[:, 0]):+6.0f}$  septembre {np.median(a[:, 1]):+6.0f}$  "
              f"septembre sans x180 {np.median(a[:, 2]):+6.0f}$")
