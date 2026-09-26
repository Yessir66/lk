#!/usr/bin/env python3
"""
Sniper-following universe: every token bought by one of the snipers the wallet follows (4yFAz7dp,
CBKgS8Nj), labelled by whether the wallet bought it. Unlike the case-control sample this is the
complete set of candidates, so precision measured on it is exact.

Features only use what is public when we would act, i.e. right after the sniper's buy lands:
  - the sniper's buy (size, ~2.93 SOL signature, slots after creation), co-snipers, 'veto' snipers
  - pump.fun trades in [S0, E], E = max(S0 + 2, sniper slot + DELAY)  (wallet's own trades excluded);
    DELAY (slots, default 0, --delay D) is how long we wait after the sniper before deciding. The
    wallet lands a median 8 slots after the sniper; tokens it already bought by E are flagged
    ('wallet_before_decision') since for them the window can contain its copy-traders.
  - creator history inside the universe (earlier tokens of the same creator and whether the wallet
    followed them) — only tokens whose outcome was already visible (>= 60 s earlier)
  - the wallet's recent behaviour: follow rate over the last 30 universe tokens (outcome visible),
    buys in the last hour, currently holding a position
Output: data/sniper_universe.json
"""
import bisect
import glob
import json
import math
import os
import sys
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(__file__))
from build_early_features import WALLET, window_features  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
SNIPERS = {"4yFAz7dp5WwuZs3vbWKedbRiUmSFxTVGCdsLAxfEQrG3": "4yFA", "CBKgS8Nj714YomoPPxhVLUWos7vSrWnuL2cVKnJqWo2s": "CBKg"}
VETO = {"2mqrindMAjJEQPLhroYWyiYPo5h9iAsahfdd4QtsjwdY", "8RvtT8189KpAq5MkXGVHFn6LdZpxq9PkvDeAvs1g5Yj4",
        "Fhbh1DTUDKt6qu9zVg8Q5VgnZa1WWWbxBxG6pCoJoggA"}
SIBLINGS = {"AWrTFnoSjCbJ4KhTzCQsjHwWrCt8jhXNKQubjdT1ENWw": "AWrT", "6yRBpeDDba4yASyjmWBLstxv1gYHVsvdHfuBpVpr63N2": "6yRB",
            "JE87rShx7BJJ82J9gSZDueAAPozjM32Exz9A2D3PY3bT": "JE87", "2mqrindMAjJEQPLhroYWyiYPo5h9iAsahfdd4QtsjwdY": "2mqr"}
OUTCOME_DELAY_S = 60
DELAY = int(sys.argv[sys.argv.index("--delay") + 1]) if "--delay" in sys.argv else 0
RECENT_N = 30


def main():
    sigs = json.load(open(os.path.join(DATA, "signatures.json")))
    w0 = min(s["blockTime"] for s in sigs if s.get("blockTime"))
    w1 = max(s["blockTime"] for s in sigs if s.get("blockTime"))
    ledger = json.load(open(os.path.join(DATA, "wallet_ledger.json")))
    intervals = sorted((v["first_buy_bt"], v["last_sell_bt"] or v["first_buy_bt"]) for v in ledger.values()
                       if v["status"] == "bought")
    starts = [a for a, _ in intervals]
    buy_times = sorted(v["first_buy_bt"] for v in ledger.values())

    # candidate tokens: first sniper buy per mint
    cand = {}
    for w, name in SNIPERS.items():
        for line in (l for p in glob.glob(os.path.join(DATA, f"leader_{w[:8]}_trades*.jsonl")) for l in open(p)):
            t = json.loads(line)
            if t["side"] != "BUY" or not t["mint"] or not (w0 <= t["bt"] <= w1):
                continue
            c = cand.setdefault(t["mint"], {"mint": t["mint"], "snipers": {}})
            if name not in c["snipers"] or t["slot"] < c["snipers"][name]["slot"]:
                c["snipers"][name] = {"slot": t["slot"], "bt": t["bt"], "sol": -t["sol"]}

    rows, missing = [], defaultdict(int)
    for m, c in cand.items():
        p = os.path.join(DATA, "universe_trades", f"{m}.json")
        if not os.path.exists(p):
            missing["pas encore récupéré"] += 1
            continue
        d = json.load(open(p))
        tr = d["trades"]
        if not d["complete"] or not tr:
            missing["création non atteinte"] += 1
            continue
        s0, creator = tr[0]["slot"], tr[0]["user"]
        first = min(c["snipers"].values(), key=lambda s: s["slot"])
        e = max(s0 + 2, first["slot"] + DELAY)
        f = window_features(tr, creator, s0, e - s0)
        in_win = {t["user"] for t in tr if t["type"] == "buy" and t["slot"] <= e}
        later = {t["user"] for t in tr if t["type"] == "buy"}
        bot = ledger.get(m)
        rows.append({
            "mint": m, "creator": creator, "t": first["bt"], "s0": s0, "decision_slot": e,
            "label": int(bot is not None),
            "bot_slot_offset": (bot["first_buy_slot"] - first["slot"]) if bot else None,
            "wallet_before_decision": int(bool(bot) and bot["first_buy_slot"] <= e),
            "sniper_first": [k for k, v in c["snipers"].items() if v["slot"] == first["slot"]][0],
            "sniper_sol": first["sol"], "sig_293": int(2.9 <= first["sol"] < 3.0),
            "sniper_offset": first["slot"] - s0,
            "both_snipers": int(len(c["snipers"]) == 2 or all(w in in_win for w in SNIPERS)),
            "veto_present": int(bool(in_win & VETO)),
            "log_price_chg": math.log1p(max(0.0, f["price_change_pct"])),
            "log_sol_buys": math.log1p(f["sol_buys"]), "n_buyers": f["n_buyers"],
            "dev_buy_sol": f["dev_buy_sol"], "n_sells": f["n_sells"],
            "hour": int((first["bt"] % 86400) // 3600),
            "siblings_later": sorted(SIBLINGS[u] for u in later & set(SIBLINGS)),
            # the wallet's own buy of this token (5% land before the sniper's) must not leak in
            "wallet_busy": int(any(a <= first["bt"] <= b + 1 and not (bot and a == bot["first_buy_bt"]) for a, b in
                                   intervals[max(0, bisect.bisect_right(starts, first["bt"]) - 2):
                                             bisect.bisect_right(starts, first["bt"])])),
            "bot_buys_1h": bisect.bisect_left(buy_times, first["bt"]) - bisect.bisect_left(buy_times, first["bt"] - 3600)
            - int(bool(bot) and first["bt"] - 3600 <= bot["first_buy_bt"] < first["bt"]),
        })
    rows.sort(key=lambda r: r["t"])

    # sequential features: only outcomes visible OUTCOME_DELAY_S before the decision
    pending = deque()
    recent = deque(maxlen=RECENT_N)
    cre_n, cre_f = defaultdict(int), defaultdict(int)
    for r in rows:
        while pending and pending[0]["t"] <= r["t"] - OUTCOME_DELAY_S:
            q = pending.popleft()
            recent.append(q["label"])
            cre_n[q["creator"]] += 1
            cre_f[q["creator"]] += q["label"]
        r["recent_follow_rate"] = (sum(recent) + 1) / (len(recent) + 4)
        r["creator_prev_n"] = cre_n[r["creator"]]
        r["creator_prev_followed"] = cre_f[r["creator"]]
        r["creator_follow_rate"] = (cre_f[r["creator"]] + 0.25) / (cre_n[r["creator"]] + 1)
        pending.append(r)

    json.dump(rows, open(os.path.join(DATA, "sniper_universe.json" if DELAY == 0 else f"sniper_universe_d{DELAY}.json"), "w"))
    print(f"{len(rows)} tokens dans l'univers ({sum(r['label'] for r in rows)} achetés par le wallet) | "
          f"exclus: {dict(missing)}")


if __name__ == "__main__":
    main()
