#!/usr/bin/env python3
"""
Price paths for the backtester: every pump.fun trade from creation to creation + HORIZON_S for
  - every token the studied wallet bought (full_universe rows with label 1), and
  - the sampled launches that pass the loose entry filter at S0+2 (>= 3 buyers and >= 1 SOL bought):
    23% of all launches, 89% of the wallet's buys. Launches failing it are never entered, so they
    need no path.
  With --sniper: instead, every token 4yFAz7dp bought first (sniper_universe.json, plus the blind-test
  day in data/blind), anchored at the token's first trade.
Usage: fetch_candidate_paths.py [--shard K/N] [--sniper]
Output: data/paths/<mint>.json {mint, t_create, complete, trades}
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from fetch_position_paths import fetch_path  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
OUT = os.path.join(DATA, "paths")
HORIZON_S = 45


def loose_filter(r):
    return r["n_buyers"] >= 3 and r["sol_buys"] >= 1.0


def main():
    os.makedirs(OUT, exist_ok=True)
    k, n = tuple(map(int, sys.argv[sys.argv.index("--shard") + 1].split("/"))) if "--shard" in sys.argv else (0, 1)
    if "--sniper" in sys.argv:
        from datetime import datetime
        jobs = []
        srcs = [(r["mint"], os.path.join(DATA, "universe_trades", f"{r['mint']}.json"))
                for r in json.load(open(os.path.join(DATA, "sniper_universe.json"))) if r["sniper_first"] == "4yFA"]
        blind = os.path.join(DATA, "blind", "sniper_buys.json")
        if os.path.exists(blind):
            srcs += [(m, os.path.join(DATA, "blind", "trades", f"{m}.json")) for m in json.load(open(blind))["buys"]]
        for m, f in srcs:
            if os.path.exists(f):
                tr = json.load(open(f))["trades"]
                if tr:
                    jobs.append((m, datetime.fromisoformat(tr[0]["ts"].replace("Z", "+00:00")).timestamp()))
        return run(jobs, k, n)
    rows = json.load(open(os.path.join(DATA, "full_universe_h2.json")))
    # the wallet's tokens first (they calibrate the simulator), then the other candidates
    wallet_tokens = {r["mint"] for r in rows if r["label"]}
    jobs = sorted(((r["mint"], r["t"]) for r in rows if r["label"] or loose_filter(r)),
                  key=lambda mt: (mt[0] not in wallet_tokens, mt[0]))
    return run(jobs, k, n)


def run(jobs, k, n):
    todo = [(m, t) for m, t in jobs if not os.path.exists(os.path.join(OUT, f"{m}.json"))][k::n]
    print(f"{len(jobs)} candidats, {len(todo)} à récupérer dans cette part", flush=True)
    for i, (m, t0) in enumerate(todo, 1):
        try:
            tr, complete = fetch_path(m, t0 - 2, t0 + HORIZON_S)
            json.dump({"mint": m, "t_create": t0, "complete": complete, "trades": tr},
                      open(os.path.join(OUT, f"{m}.json"), "w"))
        except Exception as e:
            print(f"  ERREUR {m}: {e}", flush=True)
        if i % 100 == 0:
            print(f"  {i}/{len(todo)}", flush=True)
        time.sleep(0.5)
    print("done", flush=True)


if __name__ == "__main__":
    main()
