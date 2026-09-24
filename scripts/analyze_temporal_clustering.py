#!/usr/bin/env python3
"""
Test whether the wallet's successful captures of a given fast creator's
tokens are spread uniformly over time, or cluster into "sessions"
(bursts of several captures within a short window, separated by long
quiet gaps) — and whether that clustering is stronger than what the
creator's own (already somewhat bursty) launch pattern would explain on
its own.
"""
import json
import os
import math
from datetime import datetime, timezone
from collections import Counter

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
META_DIR = os.path.join(DATA_DIR, "coin_meta")
LAUNCHES_FILE = os.path.join(DATA_DIR, "top_creator_launches.json")

SESSION_GAP_S = 2 * 3600  # group wallet captures within 2h into one "session"


def parse_ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc).timestamp()


def burst_ratio(times):
    """median/mean of inter-arrival gaps. ~0.693 (ln 2) for a pure Poisson
    process; well below that means clustering/bursts."""
    times = sorted(times)
    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    if not gaps:
        return None
    mean_gap = sum(gaps) / len(gaps)
    median_gap = sorted(gaps)[len(gaps) // 2]
    return median_gap / mean_gap if mean_gap else None


def sessions(times, gap_s):
    times = sorted(times)
    out = [[times[0]]]
    for t in times[1:]:
        if t - out[-1][-1] < gap_s:
            out[-1].append(t)
        else:
            out.append([t])
    return out


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
        if "__error__" in d:
            continue
        creator_bought.setdefault(d["creator"], []).append(parse_ts(per_token[m]["first_buy_time"]))

    result = {}
    print("=" * 78)
    print("REGROUPEMENT TEMPOREL DES CAPTURES ('sessions' vs bruit pur)")
    print("=" * 78)
    for c, times in creator_bought.items():
        if len(times) < 15:
            continue
        wr = burst_ratio(times)
        sess = sessions(times, SESSION_GAP_S)
        sizes = Counter(len(s) for s in sess)

        creator_ratio = None
        if c in launches:
            ctimes = [x["created_timestamp"] / 1000 for x in launches[c]]
            creator_ratio = burst_ratio(ctimes)

        result[c] = {
            "n_captures": len(times),
            "wallet_burst_ratio": round(wr, 3) if wr else None,
            "creator_own_burst_ratio": round(creator_ratio, 3) if creator_ratio else None,
            "n_sessions": len(sess),
            "session_sizes": dict(sorted(sizes.items())),
        }

        print(f"\n{c} ({len(times)} captures)")
        print(f"  ratio médiane/moyenne des écarts (wallet)   : {wr:.3f}  (1.0=régulier, 0.693=Poisson pur, <0.5=rafales nettes)")
        if creator_ratio:
            print(f"  ratio médiane/moyenne des écarts (créateur) : {creator_ratio:.3f}")
        print(f"  nombre de sessions (regroupement <2h)       : {len(sess)}  tailles: {dict(sorted(sizes.items()))}")

    with open(os.path.join(DATA_DIR, "temporal_clustering_report.json"), "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nRapport JSON: data/temporal_clustering_report.json")


if __name__ == "__main__":
    main()
