#!/usr/bin/env python3
"""
Frozen test of the alert rule chosen on the first 90 alerts of 20/10/2026 (13:16 -> 23:33, see
analyses/alerts_preregistration.md), on later alerts only:

  wait one minute after the alert; buy 2 min after it (t + 119 s) only if at least NEW_MIN wallets made their
  first trade on the token, as a buy, during the minute after the alert; sell with F1R; 20 min horizon.

Compared with buying every alert at t + 119 s and at t + 59 s (same exits). Other thresholds are shown for
information only (not part of the frozen rule).

Usage: alerts_rule.py <alerts.csv> --after "DD/MM/YYYY HH:MM" [--sample N] [--minutes 20] [--cache-only]
  --sample N: only N of these alerts, drawn at random with a fixed seed (same draw on every run)
"""
import datetime
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import alerts_test as AT  # noqa: E402
from alerts_features import features  # noqa: E402

NEW_MIN = 131                                  # frozen on the first 90 alerts


def main():
    argv = sys.argv[1:]
    sample = None
    if "--sample" in argv:
        j = argv.index("--sample")
        sample = int(argv[j + 1])
        argv = argv[:j] + argv[j + 2:]
    cache_only = "--cache-only" in argv                    # applied after the draw, so the draw never depends on it
    argv = [a for a in argv if a != "--cache-only"]
    i = argv.index("--after")
    after = datetime.datetime.strptime(argv[i + 1], "%d/%m/%Y %H:%M").replace(tzinfo=AT.PARIS).timestamp()
    alerts, _ = AT.setup(argv[:i] + argv[i + 2:])
    alerts = {m: t for m, t in alerts.items() if t >= after}
    print(f"{len(alerts)} alertes à partir du {argv[i + 1]} (jamais utilisées pour choisir la règle)")
    if sample and sample < len(alerts):
        keep = random.Random(0).sample(sorted(alerts), sample)
        alerts = {m: alerts[m] for m in keep}
        print(f"échantillon tiré au hasard (graine fixe) : {len(alerts)} alertes")
    if cache_only:
        alerts = {m: t for m, t in alerts.items() if os.path.exists(os.path.join(AT.OUT, f"trades_{m}_{t}.json"))}
        print(f"dont {len(alerts)} déjà téléchargées (--cache-only)")
    AT.prefetch(alerts)
    rows = []
    for mint, t in sorted(alerts.items(), key=lambda kv: kv[1]):
        tr = AT.trades_for(mint, t)
        f = features(mint, t, tr) if tr is not None else None
        if f:
            rows.append(f)
    print(f"{len(rows)} analysables\n")
    fr = lambda t: datetime.datetime.fromtimestamp(t, AT.PARIS).strftime("%d/%m %H:%M:%S")
    for r in rows:
        take = r["nouveaux après 1 min"] >= NEW_MIN
        print(f"{fr(r['t'])} {r['mint'][:8]}  nouveaux après 1 min {r['nouveaux après 1 min']:4.0f}  "
              f"{'ACHAT' if take else '  -  '}  F1R à +2 min {r['F1R après 1 min']:+7.2f} $  max x{r['max après 1 min']:.2f}")
    if not rows:
        return
    v2 = np.array([r["F1R après 1 min"] for r in rows])
    v1 = np.array([r["F1R"] for r in rows])
    new = np.array([r["nouveaux après 1 min"] for r in rows])
    top = np.array([r["max après 1 min"] for r in rows])
    print(f"\nRÈGLE FIGÉE (≥ {NEW_MIN} nouveaux acheteurs dans la minute après l'alerte) : "
          f"{int((new >= NEW_MIN).sum())} achats, total {v2[new >= NEW_MIN].sum():+.2f} $, gagnants {int((v2[new >= NEW_MIN] > 0).sum())}")
    print(f"  performants gardés {int(((new >= NEW_MIN) & (top >= 2)).sum())}/{int((top >= 2).sum())}, "
          f"mauvais écartés {int(((new < NEW_MIN) & (top < 1.3)).sum())}/{int((top < 1.3).sum())}")
    print(f"Toutes les alertes, achat à +2 min : {v2.sum():+.2f} $ ({int((v2 > 0).sum())}/{len(v2)} gagnants)")
    print(f"Toutes les alertes, achat à +59 s  : {v1.sum():+.2f} $ ({int((v1 > 0).sum())}/{len(v1)} gagnants)")
    print("Pour information, autres seuils : " + "  ".join(
        f"≥{k}: {int((new >= k).sum())} achats {v2[new >= k].sum():+.0f} $" for k in (80, 100, 150, 200)))


if __name__ == "__main__":
    main()
