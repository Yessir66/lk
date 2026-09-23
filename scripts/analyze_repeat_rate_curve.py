#!/usr/bin/env python3
"""
Track the creator-repeat rate as a function of how much of the wallet's
history we've observed, to see whether it keeps climbing (our sample is
just too short to see the wallet's real creator list) or plateaus (we've
captured most of its regulars).

Two metrics, both computed on tokens ordered chronologically by first
purchase:
  - "sequential": was this exact creator already bought from earlier in
    the wallet's history at the time of this purchase? (forward-looking,
    the most honest measure of "how often does a NEW purchase turn out
    to be a repeat")
  - "retroactive": looking at the whole window up to n, what fraction of
    tokens have a creator bought >=2 times anywhere in that window? (our
    usual headline number, but it counts future repeats too)
"""
import json
import os
from collections import Counter

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
META_DIR = os.path.join(DATA_DIR, "coin_meta")


def main():
    report = json.load(open(os.path.join(DATA_DIR, "report.json")))
    per_token = report["per_token"]
    mints_sorted = sorted(per_token.keys(), key=lambda m: per_token[m]["first_buy_time"])
    n_total = len(mints_sorted)

    creators_seen = Counter()
    sequential_flags = []
    metas = {}
    for m in mints_sorted:
        p = os.path.join(META_DIR, f"{m}.json")
        if not os.path.exists(p):
            sequential_flags.append(None)
            continue
        d = json.load(open(p))
        if "__error__" in d:
            sequential_flags.append(None)
            continue
        metas[m] = d
        c = d["creator"]
        sequential_flags.append(creators_seen[c] > 0)
        creators_seen[c] += 1

    checkpoints = sorted(set([50, 100, 150, 200, 252, 300, 372, 500, 700, 900, 1100, 1300, 1500, n_total]))
    checkpoints = [c for c in checkpoints if c <= n_total]

    curve = []
    for cp in checkpoints:
        seq_window = [f for f in sequential_flags[:cp] if f is not None]
        seq_rate = 100 * sum(seq_window) / len(seq_window) if seq_window else None

        subset = mints_sorted[:cp]
        ccount = Counter(metas[m]["creator"] for m in subset if m in metas)
        n_with_meta = sum(1 for m in subset if m in metas)
        retro_repeat = sum(1 for m in subset if m in metas and ccount[metas[m]["creator"]] > 1)
        retro_rate = 100 * retro_repeat / n_with_meta if n_with_meta else None

        curve.append({
            "n_tokens": cp,
            "sequential_repeat_pct": round(seq_rate, 1) if seq_rate is not None else None,
            "retroactive_repeat_pct": round(retro_rate, 1) if retro_rate is not None else None,
            "distinct_creators": len(ccount),
        })

    # deceleration check: slope between last two checkpoints vs slope
    # between the two before that
    if len(curve) >= 4:
        s = [c["sequential_repeat_pct"] for c in curve[-4:]]
        recent_slope = s[-1] - s[-2]
        prior_slope = s[-3] - s[-4]
    else:
        recent_slope = prior_slope = None

    result = {"curve": curve, "recent_slope_pct_per_checkpoint": recent_slope,
              "prior_slope_pct_per_checkpoint": prior_slope}

    with open(os.path.join(DATA_DIR, "repeat_rate_curve.json"), "w") as f:
        json.dump(result, f, indent=2)

    print("=" * 78)
    print("COURBE DU TAUX DE CRÉATEURS RÉCURRENTS EN FONCTION DE LA TAILLE D'ÉCHANTILLON")
    print("=" * 78)
    print(f"{'n tokens':>10} {'séquentiel':>12} {'rétroactif':>12} {'créateurs distincts':>20}")
    for c in curve:
        print(f"{c['n_tokens']:>10} {c['sequential_repeat_pct']:>11}% {c['retroactive_repeat_pct']:>11}% "
              f"{c['distinct_creators']:>20}")

    if recent_slope is not None:
        print(f"\nPente récente (séquentiel, derniers paliers): {recent_slope:+.1f} pt")
        print(f"Pente précédente (séquentiel, paliers d'avant): {prior_slope:+.1f} pt")
        if abs(recent_slope) < abs(prior_slope) / 2:
            print("=> La courbe ralentit nettement : signe d'un début de plateau, pas d'une")
            print("   croissance indéfinie. Le taux réel semble se stabiliser autour des valeurs")
            print("   observées sur les derniers paliers plutôt que de continuer à grimper sans fin.")
        else:
            print("=> Pas encore de ralentissement net : la courbe pourrait continuer à monter")
            print("   avec plus de données.")


if __name__ == "__main__":
    main()
