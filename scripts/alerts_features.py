#!/usr/bin/env python3
"""
Which alerts of a Discord alert bot are bad and which perform: each "new token" alert is described by
indicators computed from the token's trades, and the indicators are compared between tokens that perform and
tokens that fail. Indicators and outcomes were fixed before looking at any alert.

At the alert (start of its minute t, trades before t only):
  MC, age (min since the first trade seen, 5 = 5 or more), volume 1 min / 5 min ($), accélération
  (5 x vol 1 min / vol 5 min), achats % (buy share of the last minute's $ volume), achats/ventes (counts, 1 min),
  nouveaux 1 min (wallets whose first trade is a buy, last 60 s), acheteurs (distinct buyers, last 6 min),
  var 1 min / var 5 min (price change), vs ATH (price / highest price before t), gros acheteur % (largest
  wallet's share of the last 5 min's buy volume), achat moyen ($, 1 min), dev vendu (share of its tokens the
  creator has sold, when the creation is in the data).
One minute after the alert (t + 60 s): nouveaux après 1 min, var après 1 min.
Outcomes, buying at the end of the alert minute (t + 59 s), or at t + 119 s for the "after 1 min" indicators:
  performe = reaches x2 within the horizon (45 min by default); mauvais = never reaches x1.3; result of a 50 $ F1R position.
Analysis: medians (performe vs mauvais) and AUC of each indicator (probability that a performing token has a
higher value than a bad one: 0.5 = useless, near 0 or 1 = discriminating); one-indicator filters chosen on
the first half of the alerts (in time) and checked on the second half.

Usage: alerts_features.py <alerts.csv> [--minutes 45] [--cache-only]   (same CSV and options as alerts_test.py)
"""
import collections
import datetime
import json
import os
import sys
import warnings

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import flow_backtest as B  # noqa: E402
import alerts_test as AT  # noqa: E402
from alerts_test import OUT, PRE_S, prefetch, trades_for, window  # noqa: E402

warnings.simplefilter("ignore", RuntimeWarning)         # medians of empty groups
AT_ALERT = ["MC", "âge (min)", "volume 1 min", "volume 5 min", "accélération", "achats %", "achats/ventes",
            "nouveaux 1 min", "acheteurs", "var 1 min", "var 5 min", "vs ATH", "gros acheteur %", "achat moyen",
            "dev vendu"]
AFTER = ["nouveaux après 1 min", "var après 1 min"]
CAP = (PRE_S - 60) / 60                                  # age seen at most (min)


def price_at(T, px, t):
    i = np.searchsorted(T, t, side="right") - 1
    return px[i] if i >= 0 else np.nan


def features(mint, t, tr):
    T = np.array([B.ts(x["timestamp"]) for x in tr])
    px = np.array([float(x["priceUsd"] or 0) for x in tr]) * 1e9
    usd = np.array([float(x["amountUsd"] or 0) for x in tr])
    base = np.array([float(x["baseAmount"] or 0) for x in tr])
    buy = np.array([x["type"] == "buy" for x in tr])
    user = [x["userAddress"] for x in tr]
    pre = T < t
    if pre.sum() < 5:
        return None
    seen_start = T[0] > t - PRE_S + 60                 # the token's first trade is in the data
    first = {}
    for u, tt, b in zip(user, T, buy):
        first.setdefault(u, (tt, b))
    f = {"MC": price_at(T, px, t - 1), "âge (min)": min(CAP, (t - T[0]) / 60) if seen_start else CAP}
    w = window(mint, t, tr)
    if w is None:
        return None
    A, i = w.A, w.i0
    f.update({"volume 1 min": A["vol1"][i], "volume 5 min": A["vol5"][i], "accélération": A["acc"][i],
              "achats %": A["bvol1"][i] / max(A["vol1"][i], 1e-9), "achats/ventes": A["buys1"][i] / (A["sells1"][i] + 1),
              "nouveaux 1 min": A["new1"][i], "achat moyen": A["avgbuy"][i]})
    f["acheteurs"] = len({u for u, b, p in zip(user, buy, pre) if b and p})
    p_now = f["MC"]
    f["var 1 min"] = p_now / price_at(T, px, t - 61) - 1
    f["var 5 min"] = p_now / price_at(T, px, t - 301) - 1 if T[0] < t - 300 else np.nan
    f["vs ATH"] = p_now / px[pre].max()
    m5 = pre & (T >= t - 300) & buy
    by = collections.Counter()
    for u, v in zip(np.array(user)[m5], usd[m5]):
        by[u] += v
    f["gros acheteur %"] = max(by.values()) / sum(by.values()) if by else np.nan
    if seen_start and buy[0]:
        dev = user[0]
        b_in = sum(q for u, q, b, p in zip(user, base, buy, pre) if u == dev and b and p)
        b_out = sum(q for u, q, b, p in zip(user, base, buy, pre) if u == dev and not b and p)
        f["dev vendu"] = min(1.0, b_out / b_in) if b_in else np.nan
    else:
        f["dev vendu"] = np.nan
    f["nouveaux après 1 min"] = sum(1 for tt, b in first.values() if b and t <= tt < t + 60)
    f["var après 1 min"] = price_at(T, px, t + 59) / p_now - 1
    for key, d in (("", 59), (" après 1 min", 119)):     # outcomes for an entry at t + d
        we = window(mint, t + d, tr)
        if we is None:
            return None
        p = B.Path(we, stake=50.0)
        top = float(np.nanmax(p.x))
        f["max" + key] = top
        f["F1R" + key] = B.evaluate(p, **B.runner_rule())
    f.update(mint=mint, t=t, venue=w.venue)
    return f


def auc(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    if not len(a) or not len(b):
        return np.nan
    return float(((a[:, None] > b[None, :]).sum() + 0.5 * (a[:, None] == b[None, :]).sum()) / (len(a) * len(b)))


def fmt(v):
    if np.isnan(v):
        return "—"
    return f"{v:,.0f}".replace(",", " ") if abs(v) >= 100 else f"{v:.2f}"


def best_filter(rows, names, out, min_keep=0.3):
    """best one-indicator filter on rows: (score, name, sign, threshold)"""
    best = []
    for nm in names:
        v = np.array([r[nm] for r in rows], float)
        y = np.array([r[out] for r in rows])
        ok = ~np.isnan(v)
        if ok.sum() < 6:
            continue
        cand = None
        for q in range(20, 81, 10):
            th = np.percentile(v[ok], q)
            for sg in (1, -1):
                keep = ok & (sg * v >= sg * th)
                if keep.sum() >= min_keep * len(rows):
                    sc = y[keep].sum()
                    if cand is None or sc > cand[0]:
                        cand = (sc, nm, sg, th)
        if cand:
            best.append(cand)
    return sorted(best, key=lambda c: -c[0])


def main():
    alerts, _ = AT.setup(sys.argv[1:])
    prefetch(alerts)
    rows, failed = [], 0
    for mint, t in sorted(alerts.items(), key=lambda kv: kv[1]):
        tr = trades_for(mint, t)
        if tr is None:
            failed += 1
            continue
        f = features(mint, t, tr)
        if f:
            rows.append(f)
    if failed:
        print(f"{failed} alertes au téléchargement incomplet (limite de l'API) laissées de côté : relancer plus tard")
    json.dump(rows, open(os.path.join(OUT, "features.json"), "w"), indent=1, default=float)
    print(f"{len(alerts)} alertes NOUVEAU, {len(rows)} analysables")
    if len(rows) < 10:
        print("Trop peu d'alertes pour comparer les indicateurs.")
        return
    for title, names, key in (("Au moment de l'alerte (achat à la fin de la minute)", AT_ALERT, ""),
                              ("En attendant 1 minute après l'alerte (achat 1 min plus tard)", AT_ALERT + AFTER, " après 1 min")):
        perf = [r for r in rows if r["max" + key] >= 2]
        bad = [r for r in rows if r["max" + key] < 1.3]
        pnl = np.array([r["F1R" + key] for r in rows])
        print(f"\n=== {title}")
        print(f"{len(perf)} performent (≥ x2 en {B.HORIZON // 60} min), {len(bad)} mauvais (jamais x1,3), {len(rows) - len(perf) - len(bad)} entre les deux ; "
              f"F1R sur toutes : {pnl.sum():+.0f} $ ({int((pnl > 0).sum())} gagnants / {len(pnl)})")
        tab = []
        for nm in names:
            tab.append((nm, np.nanmedian([r[nm] for r in perf]) if perf else np.nan,
                        np.nanmedian([r[nm] for r in bad]) if bad else np.nan, auc([r[nm] for r in perf], [r[nm] for r in bad])))
        tab.sort(key=lambda x: -abs((x[3] if not np.isnan(x[3]) else 0.5) - 0.5))
        print(f"  {'indicateur':22s} {'médiane performe':>17s} {'médiane mauvais':>16s} {'AUC':>6s}")
        for nm, a, b, u in tab:
            print(f"  {nm:22s} {fmt(a):>17s} {fmt(b):>16s} {u:6.2f}" + ("   <- discriminant" if abs(u - 0.5) >= 0.2 else ""))
        half = len(rows) // 2
        first, second = rows[:half], rows[half:]
        cands = best_filter(first, names, "F1R" + key)[:3]
        y2 = np.array([r["F1R" + key] for r in second])
        print(f"  Filtres choisis sur les {len(first)} premières alertes, vérifiés sur les {len(second)} suivantes "
              f"(F1R sans filtre sur ces {len(second)} : {y2.sum():+.0f} $) :")
        for sc, nm, sg, th in cands:
            k2 = [r for r in second if not np.isnan(r[nm]) and sg * r[nm] >= sg * th]
            v2 = np.array([r["F1R" + key] for r in k2])
            p_all = sum(r["max" + key] >= 2 for r in second)
            b_all = sum(r["max" + key] < 1.3 for r in second)
            print(f"    {nm} {'≥' if sg > 0 else '≤'} {fmt(th)} : 1re moitié {sc:+.0f} $ ; 2e moitié {v2.sum():+.0f} $ "
                  f"sur {len(k2)} alertes gardées, performants gardés {sum(r['max' + key] >= 2 for r in k2)}/{p_all}, "
                  f"mauvais écartés {b_all - sum(r['max' + key] < 1.3 for r in k2)}/{b_all}")
    print("\nAttention : avec moins de ~100 alertes, ces écarts peuvent être dus au hasard ; à confirmer sur d'autres jours.")


if __name__ == "__main__":
    main()
