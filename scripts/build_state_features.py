#!/usr/bin/env python3
"""
Context the wallet already knew at the moment a launch appeared — no
peeking at the launch itself: the creator's recent cadence and the
wallet's own recent history with that creator (all derived from
on-chain data available before created_ts).

Output: data/state_features.json  {mint: {feature: value}}
"""
import bisect
import json
import os
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def main():
    ledger = json.load(open(os.path.join(DATA_DIR, "wallet_ledger.json")))
    launches = defaultdict(list)
    for fn in os.listdir(os.path.join(DATA_DIR, "creator_launches_full")):
        for c in json.load(open(os.path.join(DATA_DIR, "creator_launches_full", fn))):
            launches[c["creator"]].append((c["created_timestamp"] / 1000, c["mint"]))
    for c in launches:
        launches[c].sort()

    all_buys = sorted(v["first_buy_bt"] for v in ledger.values())
    closed = sorted((v["last_sell_bt"], v["pnl_sol"]) for v in ledger.values()
                    if v.get("last_sell_bt") and v.get("pnl_sol") is not None)
    close_times = [t for t, _ in closed]

    out = {}
    for creator, items in launches.items():
        times = [t for t, _ in items]
        for i, (t, mint) in enumerate(items):
            prev = items[i - 1] if i > 0 else None

            def bought_before_t(m):
                return m in ledger and ledger[m]["first_buy_bt"] < t

            prev_bought = int(prev is not None and bought_before_t(prev[1]))
            # wallet's history with this creator, strictly before t
            hist = [(pt, pm) for pt, pm in items[:i] if bought_before_t(pm)]
            last_closed = None
            for pt, pm in reversed(hist):
                v = ledger[pm]
                if v.get("last_sell_bt") and v["last_sell_bt"] < t and v.get("pnl_sol") is not None:
                    last_closed = v
                    break
            streak = 0
            for pt, pm in reversed(items[:i]):
                if bought_before_t(pm):
                    streak += 1
                else:
                    break
            j = bisect.bisect_left(all_buys, t) - 1
            k = bisect.bisect_left(close_times, t)
            recent_pnl = [p for _, p in closed[max(0, k - 5):k]]
            out[mint] = {
                "creator_prev_gap_s": (t - prev[0]) if prev else 1e6,
                "creator_launches_1h": i - bisect.bisect_left(times, t - 3600),
                "bot_bought_prev_launch": prev_bought,
                "bot_buy_streak_creator": streak,
                "bot_buys_creator_6h": sum(1 for pt, _ in hist if pt >= t - 6 * 3600),
                "bot_last_pnl_creator": last_closed["pnl_sol"] if last_closed else 0.0,
                "bot_has_history_creator": int(bool(hist)),
                "bot_secs_since_last_buy": (t - all_buys[j]) if j >= 0 else 1e6,
                "bot_recent_pnl_5": sum(recent_pnl),
            }
    json.dump(out, open(os.path.join(DATA_DIR, "state_features.json"), "w"))
    print(f"variables de contexte calculées pour {len(out)} lancements")


if __name__ == "__main__":
    main()
