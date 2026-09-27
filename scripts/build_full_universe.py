#!/usr/bin/env python3
"""
Full-universe dataset: every token the studied wallet bought (positives) + an unbiased random sample
of ALL pump.fun launches (sample_launches.py), with features at S0 + H slots (S0 = creation slot).

Weights: a sampled launch that the wallet did not buy stands for hour_count / sampled_in_hour launches
of its hour; positives are all included (weight 1). Sampled launches the wallet bought are already
among the positives. A positive whose wallet buy landed by S0 + H is left out (acting then would be
copying it), as are tokens whose trades do not reach creation.

Features (decision-time only; the wallet's own trades never enter):
  early trades in [S0, S0+H] (build_early_features.window_features), known snipers present,
  a ~2.926 SOL sniper buy, the creator's earlier tokens bought by the wallet (causal), the wallet's
  public activity in the previous hour / day, hour of day.
Output: data/full_universe_h<H>.json
"""
import bisect
import glob
import json
import math
import os
import sys
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from build_early_features import WALLET, window_features  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
H = int(sys.argv[sys.argv.index("--h") + 1]) if "--h" in sys.argv else 2
KNOWN = {"4yFAz7dp5WwuZs3vbWKedbRiUmSFxTVGCdsLAxfEQrG3": "sn_4yFA", "CBKgS8Nj714YomoPPxhVLUWos7vSrWnuL2cVKnJqWo2s": "sn_CBKg",
         "2mqrindMAjJEQPLhroYWyiYPo5h9iAsahfdd4QtsjwdY": "sn_2mqr", "8RvtT8189KpAq5MkXGVHFn6LdZpxq9PkvDeAvs1g5Yj4": "sn_8Rvt",
         "Fhbh1DTUDKt6qu9zVg8Q5VgnZa1WWWbxBxG6pCoJoggA": "sn_Fhbh", "2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG": "sn_2CQg"}


def wallet_buys():
    ledger = json.load(open(os.path.join(DATA, "wallet_ledger.json")))
    out = {m: {"slot": v["first_buy_slot"], "bt": v["first_buy_bt"]} for m, v in ledger.items()}
    extra = os.path.join(DATA, "blind", "wallet_buys.json")
    if os.path.exists(extra):
        for m, b in json.load(open(extra))["buys"].items():
            out.setdefault(m, {"slot": b["slot"], "bt": b["bt"]})
    return out


def main():
    census = json.load(open(os.path.join(DATA, "launch_census.json")))
    counts = {int(h): n for h, n in census["hour_counts"].items()}
    # resolution of each sampled create: sig -> mint (None + kind for transactions that are not launches)
    resolved = {}
    for p in glob.glob(os.path.join(DATA, "launch_sample_mints*.jsonl")):
        for l in open(p):
            x = json.loads(l)
            if x["mint"] or x.get("kind") or x["sig"] not in resolved:
                resolved[x["sig"]] = x
    have = {os.path.basename(f)[:-5] for f in glob.glob(os.path.join(DATA, "full_universe", "*.json"))}

    def processed(sig):
        """A sampled transaction whose outcome is known: not a launch, or a launch whose trades we have.
        Weights divide by processed transactions only, so a partially fetched sample (fetched in an order
        unrelated to time or label) stays unbiased."""
        x = resolved.get(sig)
        return bool(x) and ((not x["mint"] and x.get("kind")) or (x["mint"] in have))

    sampled_per_hour = defaultdict(int)
    for s in census["sample"]:
        if processed(s["sig"]):
            sampled_per_hour[int(s["bt"] // 3600 * 3600)] += 1
    # denser sample of the test window (sample_launches.py --dense): inside that window it replaces the
    # base sample (it contains it: same hash rule, higher rate), with its own weights
    dense_p = os.path.join(DATA, "launch_census_dense.json")
    dense = json.load(open(dense_p)) if os.path.exists(dense_p) and "--no-dense" not in sys.argv else None
    dense_sigs, dense_per_hour, dense_from, dense_to = set(), defaultdict(int), float("inf"), float("-inf")
    dense_counts = {}
    if dense:
        dense_counts = {int(h): n for h, n in dense["hour_counts"].items()}
        dense_sigs = {s["sig"] for s in dense["sample"]}
        for s in dense["sample"]:
            if processed(s["sig"]):
                dense_per_hour[int(s["bt"] // 3600 * 3600)] += 1
        dense_from = min(int(h) for h in dense["hour_counts"])
        dense_to = max(int(h) for h in dense["hour_counts"]) + 3600
    sample_mints = {}
    for s in resolved.values():
        if s["mint"]:
            in_dense_window = dense_from <= s["bt"] < dense_to
            if in_dense_window and s["sig"] not in dense_sigs:
                continue
            s["dense"] = in_dense_window
            sample_mints[s["mint"]] = s
    if dense:
        n_d = sum(1 for x in dense["sample"])
        print(f"échantillon dense: {sum(dense_per_hour.values())}/{n_d} transactions traitées")
    wb = wallet_buys()
    buy_times = sorted(b["bt"] for b in wb.values())

    rows, dropped = [], defaultdict(int)
    by_creator = defaultdict(list)  # every wallet buy with a known creator, including ones dropped below
    files = sorted(glob.glob(os.path.join(DATA, "full_universe", "*.json")))
    for p in files:
        d = json.load(open(p))
        if d["mint"] in wb and d["trades"] and d["complete"]:
            by_creator[d["trades"][0]["user"]].append(wb[d["mint"]]["bt"])
    for c in by_creator:
        by_creator[c].sort()
    for p in files:
        d = json.load(open(p))
        m, tr = d["mint"], d["trades"]
        is_pos, in_sample = m in wb, m in sample_mints
        cls = "positif" if is_pos else "échantillon"
        if not is_pos and not in_sample:
            continue
        if not d["complete"] or not tr:
            dropped[f"{cls}: création non atteinte"] += 1
            continue
        s0, creator = tr[0]["slot"], tr[0]["user"]
        if is_pos and wb[m]["slot"] <= s0 + H:
            dropped["positif: wallet déjà entré à la décision"] += 1
            continue
        f = window_features(tr, creator, s0, H)
        t0 = sample_mints[m]["bt"] if in_sample else None
        if t0 is None:  # creation time of a positive: timestamp of its first trade
            t0 = datetime.fromisoformat(tr[0]["ts"].replace("Z", "+00:00")).timestamp()
        early = {t["user"] for t in tr if t["type"] == "buy" and t["slot"] <= s0 + H and t["user"] != WALLET}
        r = {k: v for k, v in f.items() if k not in ("buy_sizes",)}
        r.update({
            "mint": m, "creator": creator, "t": t0, "s0": s0, "label": int(is_pos), "in_sample": int(in_sample),
            # launches in the hour / sampled launches in the hour, each from its own census (the dense census
            # counts only its window, so partial hours at its edges are weighted correctly)
            "weight": 1.0 if is_pos else (
                dense_counts.get(int(t0 // 3600 * 3600), 0) / max(1, dense_per_hour[int(t0 // 3600 * 3600)])
                if sample_mints[m]["dense"] else
                counts.get(int(t0 // 3600 * 3600), 0) / max(1, sampled_per_hour[int(t0 // 3600 * 3600)])),
            "has_2926": int(any(2.92 <= s < 2.93 for s in f["buy_sizes"])),
            "log_price_chg": math.log1p(max(0.0, f["price_change_pct"])),
            "log_sol_buys": math.log1p(f["sol_buys"]),
            "hour": int((t0 % 86400) // 3600),
            "wallet_buys_1h": bisect.bisect_left(buy_times, t0) - bisect.bisect_left(buy_times, t0 - 3600),
            "wallet_buys_24h": bisect.bisect_left(buy_times, t0) - bisect.bisect_left(buy_times, t0 - 86400),
        })
        for u, name in KNOWN.items():
            r[name] = int(u in early)
        rows.append(r)
    rows.sort(key=lambda r: r["t"])
    # creator's earlier tokens bought by the wallet (buy visible >= 60 s before), causal
    for r in rows:
        r["creator_prev_bought"] = bisect.bisect_left(by_creator[r["creator"]], r["t"] - 60)
    json.dump(rows, open(os.path.join(DATA, f"full_universe_h{H}.json"), "w"))
    pos = sum(r["label"] for r in rows)
    print(f"H={H}: {len(rows)} lignes | positifs {pos} | échantillon non acheté {len(rows) - pos - 0} "
          f"(représente {sum(r['weight'] for r in rows if not r['label']):.0f} lancements) | exclus: {dict(dropped)}")


if __name__ == "__main__":
    main()
