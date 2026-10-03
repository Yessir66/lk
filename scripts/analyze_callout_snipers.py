#!/usr/bin/env python3
"""
Who already buys the followed accounts' callouts within seconds, and does it pay them?

A wallet that buys within FAST_S seconds of the callout on many different callouts is (very likely) a bot
doing the strategy under study. For each of its positions in the recorded window (data/callout_paths/,
WINDOW s after the callout) we take its real flows: SOL paid for its buys (recorded amount, plus the fee
on the curve where the recorded amount is net of it), SOL received for its sells, and what is left valued
at the last price of the window (an upper bound: selling it would move the price).
The same is done for the callout's author (profile wallet) and for wallets that bought in the minute
before the callout on several callouts of the same author.

Output: data/callout_snipers.json
"""
import json
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import callout_backtest as CB  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
FAST_S, MIN_CALLS = 3, 10


def flows(p, wallet, t_from):
    """Real SOL flows of `wallet` on this token from t_from to the end of the window."""
    paid = got = held = 0.0
    for j in range(len(p.user)):
        if p.user[j] != wallet or p.ts[j] < t_from:
            continue
        fee = p.fee_amm if p.amm[j] else CB.FEE_CURVE
        if p.side[j]:
            paid += p.sol[j] * (1 + fee)  # recorded buy amounts are what entered the pool, before the fee
            held += p.tokens[j]
        else:
            got += p.sol[j] if p.amm[j] else p.sol[j] * (1 - fee)  # PumpSwap sell amounts are already net
            held -= p.tokens[j]
    rest = max(held, 0.0) * p.p[-1]
    return paid, got, rest


def main():
    allp, paths = CB.load()
    clock = CB.Clock([p for p in allp if p.ok])
    usable = [p for p in paths if p.ok]
    fast = defaultdict(list)  # wallet -> [(path, first buy delay)]
    for p in usable:
        s_call = clock.slot_at(p.t_call)
        s_fast = clock.slot_at(p.t_call + FAST_S)
        seen = set()
        for j in range(len(p.user)):
            if p.side[j] and s_call <= p.slot[j] <= s_fast and p.user[j] not in seen:
                seen.add(p.user[j])
                fast[p.user[j]].append((p, clock.time_at(p.slot[j]) - p.t_call))
    bots = {w: v for w, v in fast.items() if len(v) >= MIN_CALLS and w}
    print(f"{len(usable)} callouts pump.fun; {len(bots)} wallets achètent dans les {FAST_S} s après le callout "
          f"sur au moins {MIN_CALLS} callouts différents")
    report = {"n_callouts": len(usable), "bots": []}
    print(f"  {'wallet':46s} {'callouts':>8s} {'délai méd.':>10s} {'mise méd.':>9s} {'PnL SOL':>9s} "
          f"{'/position':>9s} {'gagnants':>8s} {'encore détenu':>13s}")
    for w, v in sorted(bots.items(), key=lambda kv: -len(kv[1])):
        pnl, stake, rest_share = [], [], []
        for p, _ in v:
            paid, got, rest = flows(p, w, int(p.t_call))
            if paid <= 0:
                continue
            pnl.append(got + rest - paid)
            stake.append(paid)
            rest_share.append(rest / (got + rest) if got + rest > 0 else 0)
        pnl = np.array(pnl)
        row = {"wallet": w, "n": len(v), "delay_median_s": float(np.median([d for _, d in v])),
               "stake_median": float(np.median(stake)), "pnl_total": float(pnl.sum()), "pnl_mean": float(pnl.mean()),
               "win": float((pnl > 0).mean()), "still_held_share": float(np.mean(rest_share))}
        report["bots"].append(row)
        print(f"  {w:46s} {len(v):8d} {row['delay_median_s']:9.1f}s {row['stake_median']:9.3f} {pnl.sum():+9.2f} "
              f"{pnl.mean():+9.4f} {100 * row['win']:7.0f}% {100 * row['still_held_share']:12.0f}%")

    # the callout's author, from its profile wallet
    au = []
    for p in usable:
        w = p.meta["author_address"]
        bought_before = [p.ts[j] - p.t_call for j in range(len(p.user)) if p.user[j] == w and p.side[j] and p.ts[j] < p.t_call]
        sold_after = [p.sol[j] for j in range(len(p.user)) if p.user[j] == w and not p.side[j] and p.ts[j] >= int(p.t_call)]
        au.append({"bought_in_window_before": bool(bought_before), "last_buy_before_s": max(bought_before) if bought_before else None,
                   "sold_after_sol": float(sum(sold_after))})
    bb = [a["last_buy_before_s"] for a in au if a["last_buy_before_s"] is not None]
    print(f"\nAuteur (wallet de profil): a acheté dans la minute avant son callout sur {100 * len(bb) / len(au):.0f}% des callouts"
          + (f" (dernier achat médian {-np.median(bb):.0f} s avant)" if bb else "")
          + f"; vend dans les 15 min après sur {100 * np.mean([a['sold_after_sol'] > 0 for a in au]):.0f}%")
    report["author"] = {"bought_minute_before": len(bb) / len(au),
                        "sold_15min_after": float(np.mean([a["sold_after_sol"] > 0 for a in au]))}

    # wallets buying in the minute before the callout, repeatedly on the same author's callouts
    pre = defaultdict(lambda: defaultdict(int))
    n_by_author = defaultdict(int)
    for p in usable:
        a = p.meta["author"]
        n_by_author[a] += 1
        ws = {p.user[j] for j in range(len(p.user)) if p.side[j] and p.t_call - 60 <= p.ts[j] < int(p.t_call)
              and p.user[j] != p.meta["author_address"]}
        for w in ws:
            pre[a][w] += 1
    print("\nWallets qui achètent dans la minute AVANT les callouts d'un même auteur (>= 25% de ses callouts, >= 5 fois):")
    report["pre_buyers"] = {}
    for a, ws in pre.items():
        hits = [(w, c) for w, c in ws.items() if c >= 5 and c >= 0.25 * n_by_author[a]]
        if hits:
            report["pre_buyers"][a] = {"n_callouts": n_by_author[a], "wallets": dict(hits)}
            print(f"  {a:16s} ({n_by_author[a]} callouts): " + ", ".join(f"{w[:8]}… x{c}" for w, c in sorted(hits, key=lambda x: -x[1])[:5]))
    json.dump(report, open(os.path.join(DATA, "callout_snipers.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
