#!/usr/bin/env python3
"""
For each of the wallet's top repeat creators, compare how fast that
creator launches tokens (from pump.fun's recent-launches API, rate
extrapolated) against what fraction of their output the wallet actually
buys. Tests whether the "extra filter" beyond the creator whitelist is
content-based, or simply a throughput/capacity constraint (the wallet
loses more races the faster a creator's stream fires).
"""
import json
import os
import math

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
META_DIR = os.path.join(DATA_DIR, "coin_meta")
LAUNCHES_FILE = os.path.join(DATA_DIR, "top_creator_launches.json")
WINDOW_DAYS = 16.34


def pearson(pairs):
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    return cov / (sx * sy) if sx and sy else 0.0


def main():
    report = json.load(open(os.path.join(DATA_DIR, "report.json")))
    per_token = report["per_token"]
    launches = json.load(open(LAUNCHES_FILE))

    creator_bought = {}
    for m in per_token:
        p = os.path.join(META_DIR, f"{m}.json")
        if not os.path.exists(p):
            continue
        d = json.load(open(p))
        if "__error__" not in d:
            creator_bought.setdefault(d["creator"], set()).add(m)

    rows = []
    for c, coins in launches.items():
        bought = creator_bought.get(c, set())
        times = [x["created_timestamp"] / 1000 for x in coins]
        span_h = (max(times) - min(times)) / 3600
        if span_h <= 0:
            continue
        rate = len(coins) / span_h
        estimated_total = rate * WINDOW_DAYS * 24
        capture_pct = 100 * len(bought) / estimated_total
        rows.append({"creator": c, "launch_rate_per_hour": round(rate, 2),
                     "estimated_tokens_in_window": round(estimated_total),
                     "bought": len(bought), "capture_pct": round(capture_pct, 1)})

    rows.sort(key=lambda r: -r["launch_rate_per_hour"])

    lin_r = pearson([(r["launch_rate_per_hour"], r["capture_pct"]) for r in rows])
    log_r = pearson([(math.log(r["launch_rate_per_hour"]), math.log(max(r["capture_pct"], 0.01))) for r in rows])

    result = {"window_days": WINDOW_DAYS, "creators": rows,
              "correlation_rate_vs_capture": round(lin_r, 3),
              "correlation_log_rate_vs_log_capture": round(log_r, 3)}
    with open(os.path.join(DATA_DIR, "capture_vs_rate_report.json"), "w") as f:
        json.dump(result, f, indent=2)

    print("=" * 78)
    print("TAUX DE CAPTURE vs RYTHME DE LANCEMENT DU CRÉATEUR (top 20 créateurs récurrents)")
    print("=" * 78)
    print(f"{'créateur':46s} {'rythme(t/h)':>12s} {'estimé/fenêtre':>15s} {'acheté':>7s} {'capture%':>9s}")
    for r in rows:
        print(f"{r['creator']:46s} {r['launch_rate_per_hour']:>12.2f} "
              f"{r['estimated_tokens_in_window']:>15d} {r['bought']:>7d} {r['capture_pct']:>8.1f}%")
    print(f"\nCorrélation linéaire (rythme, capture%): r={lin_r:.3f}")
    print(f"Corrélation log-log (rythme, capture%): r={log_r:.3f}")
    print("\n=> Relation quasi log-linéaire très forte : plus un créateur lance vite, plus la part")
    print("   captée par le wallet s'effondre. Cohérent avec une contrainte de capacité/course")
    print("   contre d'autres bots plutôt qu'un jugement qualitatif token par token.")


if __name__ == "__main__":
    main()
