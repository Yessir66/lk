#!/usr/bin/env python3
"""
Test of a Discord alert bot's history: are its "new token" alerts worth buying, and are its performance
notifications (x2, x3...) right?

Input: CSV, one line per bot message, oldest first, ';'-separated: type;date;heure;adresse;multiple;infos
  type NOUVEAU (new token alert) or PERFORMANCE (x2, x3... notification); date dd/mm/yyyy or yyyy-mm-dd;
  heure HH:MM or HH:MM:SS, Paris time (Discord shows minutes only); multiple e.g. "x2" (PERFORMANCE only).
For each NOUVEAU alert: all trades of the token (swap-api.pump.fun) from 5 min before to 45 min after, then a
50 $ buy at the start of the alert's minute, at its end and 30 s later, sold by F1R and F1 (flow_backtest.py);
plus the highest multiple reached in 45 min vs the price at the start of the minute.
For each PERFORMANCE notification: the multiple our data shows at that time vs the announced one.

Usage: alerts_test.py <alerts.csv>      (Windows: python scripts\\alerts_test.py alerts.csv, needs pip install tzdata)
"""
import collections
import concurrent.futures
import csv
import datetime
import json
import os
import re
import sys
import zoneinfo

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import flow_backtest as B  # noqa: E402
from callout_test import fetch  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")          # Windows consoles and pipes
try:
    PARIS = zoneinfo.ZoneInfo("Europe/Paris")
except zoneinfo.ZoneInfoNotFoundError:
    raise SystemExit("Fuseau horaire Europe/Paris introuvable : lancer « pip install tzdata » (Windows)")
DELAYS = {"début minute": 0, "fin minute": 59, "+30 s": 89}
RULES = {"F1R": B.runner_rule(), "F1": B.flow_rule()}
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "alerts")
PRE_S, POST_S = 1800, 119 + B.HORIZON          # trades kept around each alert (s before, s after)


def when(date, hour):
    """unix time of a Paris date and time"""
    d = date.strip().replace("-", "/").split("/")
    y, m, dd = (int(d[0]), int(d[1]), int(d[2])) if len(d[0]) == 4 else (int(d[2]), int(d[1]), int(d[0]))
    h = [int(x) for x in hour.strip().split(":")] + [0]
    return int(datetime.datetime(y, m, dd, h[0], h[1], h[2], tzinfo=PARIS).timestamp())


def read_alerts(path):
    """{mint: unix time of its first NOUVEAU alert}, [(mint, unix time, announced multiple)]"""
    rows = [r for r in csv.reader(open(path, encoding="utf-8-sig"), delimiter=";") if len(r) >= 4]
    alerts, perf = {}, []
    for r in rows:
        kind = r[0].strip().upper()
        if kind not in ("NOUVEAU", "PERFORMANCE"):
            continue
        t, mint = when(r[1], r[2]), r[3].strip()
        if kind == "NOUVEAU":
            alerts.setdefault(mint, t)
        else:
            mult = re.search(r"(\d+(?:[.,]\d+)?)", r[4] if len(r) > 4 else "")
            perf.append((mint, t, float(mult.group(1).replace(",", ".")) if mult else None))
    return alerts, perf


def trades_for(mint, t):
    """all trades of mint from PRE_S before to POST_S after t (cached in data/alerts)"""
    f = os.path.join(OUT, f"trades_{mint}_{t}.json")
    if os.path.exists(f):
        return json.load(open(f))["trades"]
    now = int(datetime.datetime.now().timestamp())
    tr, ok = fetch(mint, t - PRE_S, min(t + POST_S, now))
    if t + POST_S < now:                        # complete period only
        json.dump({"ok": ok, "trades": tr}, open(f, "w"))
    return tr


def prefetch(alerts, threads=4):
    os.makedirs(OUT, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(threads) as ex:
        for k, _ in enumerate(ex.map(lambda kv: trades_for(*kv), alerts.items()), 1):
            if k % 10 == 0:
                print(f"  trades récupérés pour {k}/{len(alerts)} alertes", flush=True)


def window(mint, t, trades):
    """flow_backtest Window entered at the last trade at or before t (None if not enough data)"""
    before = [x for x in trades if B.ts(x["timestamp"]) <= t]
    if not before:
        return None
    path = os.path.join(OUT, f"{mint}_{t}.json")
    json.dump({"mint": mint, "bt": t, "sig": before[-1]["tx"], "complete": True, "trades": trades}, open(path, "w"))
    w = B.Window(path, "none")
    return w if w.ok else None


def main():
    alerts, perf = read_alerts(sys.argv[1])
    prefetch(alerts)
    res = []
    fr = lambda t: datetime.datetime.fromtimestamp(t, PARIS).strftime("%d/%m %H:%M:%S")
    for mint, t in sorted(alerts.items(), key=lambda kv: kv[1]):
        trades = trades_for(mint, t)
        r = dict(mint=mint, t=t, n=len(trades))
        w0 = window(mint, t, trades)
        if w0 is None:
            r["skip"] = "pas de données pump.fun" if not trades else "trop peu de trades"
            res.append(r)
            continue
        p0 = B.Path(w0, stake=50.0)
        r.update(venue=w0.venue, mc=w0.entry_spot * 1e9, top=float(np.nanmax(p0.x)), t_top=int(np.nanargmax(p0.x)))
        for lab, d in DELAYS.items():
            w = window(mint, t + d, trades)
            for k, rule in RULES.items():
                r[f"{k} {lab}"] = B.evaluate(B.Path(w, stake=50.0), **rule) if w else 0.0
        r["perf"] = []
        for m2, t2, mult in perf:
            if m2 == mint and t2 >= t:
                i = min(len(p0.x), t2 + 60 - p0.t0)        # notification minute included
                r["perf"].append((t2, mult, float(np.nanmax(p0.x[:max(i, 1)]))))
        res.append(r)
    json.dump(res, open(os.path.join(OUT, "results.json"), "w"), indent=1)

    ok = [r for r in res if "skip" not in r]
    print(f"{len(alerts)} alertes NOUVEAU, {len(ok)} testables, {len(perf)} notifications de performance\n")
    print(f"{'alerte':15s} {'token':9s} {'lieu':8s} {'MC':>7s} {'max 45 min':>10s} " +
          " ".join(f"{'F1R ' + k:>16s}" for k in DELAYS))
    for r in res:
        if "skip" in r:
            print(f"{fr(r['t'])} {r['mint'][:8]}  ignorée : {r['skip']}")
            continue
        print(f"{fr(r['t'])} {r['mint'][:8]}  {r['venue']:8s} {r['mc'] / 1e3:6.1f}k x{r['top']:5.2f} à +{r['t_top'] // 60:2d} min "
              + " ".join(f"{r['F1R ' + k]:+16.2f}" for k in DELAYS))
        for t2, mult, seen in r["perf"]:
            print(f"     notification {fr(t2)} : x{mult} annoncé, x{seen:.2f} dans nos données")
    if not ok:
        return
    top = np.array([r["top"] for r in ok])
    print(f"\nPlus haut en 45 min (vs prix au début de la minute de l'alerte) : "
          + ", ".join(f"≥x{k}: {(top >= k).sum()}/{len(ok)}" for k in (1.5, 2, 3, 5, 10)))
    print("Résultat avec 50 $ par alerte :")
    for k in RULES:
        for lab in DELAYS:
            v = np.sort(np.array([r[f"{k} {lab}"] for r in ok]))[::-1]
            print(f"  {k:3s} achat {lab:12s} total {v.sum():+8.2f} $  gagnants {int((v > 0).sum()):2d}/{len(v)}  "
                  f"sans les 3 meilleurs {v[3:].sum():+8.2f} $")
    seen = [(m, s) for r in ok for _, m, s in r["perf"] if m]
    if seen:
        good = sum(s >= 0.9 * m for m, s in seen)
        print(f"Notifications de performance confirmées par nos données (à 10 % près) : {good}/{len(seen)}")
    by_day = collections.Counter(datetime.datetime.fromtimestamp(r["t"], PARIS).strftime("%d/%m") for r in ok)
    print("Alertes par jour : " + ", ".join(f"{d} {n}" for d, n in sorted(by_day.items())))


if __name__ == "__main__":
    main()
