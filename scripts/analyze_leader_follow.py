#!/usr/bin/env python3
"""
Exact precision/coverage of "buy whenever leader wallet L buys" against the studied wallet's buys,
over the common window, with simple refinements (L's signature ~2.93 SOL size; creator already bought
>= 8 times by the wallet BEFORE the token — no look-ahead, and >= 8 is the level where the creator
list is exhaustive). Split before/after 2026-09-21 to show drift.
Needs: data/leader_<L[:8]>_trades.jsonl (fetch_leader_trades.py), wallet_ledger.json, creator_launches_full/.
"""
import bisect
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
L = sys.argv[1] if len(sys.argv) > 1 else "4yFAz7dp5WwuZs3vbWKedbRiUmSFxTVGCdsLAxfEQrG3"
CUT = int(datetime(2026, 9, 21, tzinfo=timezone.utc).timestamp())

tr = [json.loads(x) for x in open(os.path.join(DATA, f"leader_{L[:8]}_trades.jsonl"))]
ledger = json.load(open(os.path.join(DATA, "wallet_ledger.json")))
sigs = json.load(open(os.path.join(DATA, "signatures.json")))
bts = [s["blockTime"] for s in sigs if s.get("blockTime")]
w0, w1 = min(bts), max(bts)
buys = {}
for t in sorted(tr, key=lambda t: t["slot"]):
    if t["side"] == "BUY" and w0 <= t["bt"] <= w1 and t["mint"] not in buys:
        buys[t["mint"]] = t
bot = {m for m, v in ledger.items() if w0 <= v["first_buy_bt"] <= w1}
both = set(buys) & bot

creator = {}
for fn in os.listdir(os.path.join(DATA, "creator_launches_full")):
    for c in json.load(open(os.path.join(DATA, "creator_launches_full", fn))):
        creator[c["mint"]] = c["creator"]
hist = defaultdict(list)
for m, v in ledger.items():
    if m in creator:
        hist[creator[m]].append(v["first_buy_bt"])
for c in hist:
    hist[c].sort()


def prior(m):
    return bisect.bisect_left(hist[creator[m]], buys[m]["bt"]) if m in creator else 0


def sig(m):
    return 2.9 <= -buys[m]["sol"] < 3.0


lag = sorted(ledger[m]["first_buy_slot"] - buys[m]["slot"] for m in both)
print(f"{L[:8]}: {len(buys)} tokens achetés | bot: {len(bot)} | en commun: {len(both)} | "
      f"retard médian du bot: {lag[len(lag) // 2]} slots, bot après {L[:8]} dans {sum(x > 0 for x in lag)}/{len(lag)} cas")
rules = {
    "A: suivre partout": lambda m: True,
    "B: A + montant ~2.93 SOL": sig,
    "C: A + créateur déjà acheté >= 8 fois": lambda m: prior(m) >= 8,
    "D: B + C": lambda m: sig(m) and prior(m) >= 8,
}
out = {}
for name, f in rules.items():
    sel = [m for m in buys if f(m)]
    hit = sum(m in both for m in sel)
    pre = [m for m in sel if buys[m]["bt"] < CUT]
    post = [m for m in sel if buys[m]["bt"] >= CUT]
    rate = lambda s: round(sum(m in both for m in s) / max(1, len(s)), 3)
    out[name] = {"n": len(sel), "hits": hit, "precision": rate(sel), "coverage_of_bot": round(hit / len(bot), 3),
                 "precision_before_0921": rate(pre), "precision_since_0921": rate(post)}
    print(f"  {name:40s} précision {100 * rate(sel):5.1f}% ({hit}/{len(sel)}) | couverture {100 * hit / len(bot):4.1f}% | "
          f"avant 21/09 {100 * rate(pre):4.0f}% | depuis {100 * rate(post):4.0f}%")
json.dump(out, open(os.path.join(DATA, f"leader_follow_{L[:8]}.json"), "w"), indent=1)
