#!/usr/bin/env python3
"""
Sniper-following universe: every token bought by one of the snipers the wallet follows (4yFAz7dp,
CBKgS8Nj), labelled by whether the wallet bought it. Unlike the case-control sample this is the
complete set of candidates, so precision measured on it is exact.

Features only use what is public when we would act, i.e. right after the sniper's buy lands:
  - the sniper's buy (size, ~2.93 SOL signature, slots after creation), co-snipers, 'veto' snipers
  - pump.fun trades in [S0, E], E = max(S0 + 2, sniper slot + DELAY)  (wallet's own trades excluded);
    DELAY (slots, default 0, --delay D) is how long we wait after the sniper before deciding.
Tokens the wallet had already bought by E are left out: acting then would be copying it, not
reproducing its decision (CBKgS8Nj in particular often buys after the wallet).
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
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from build_early_features import window_features  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
SNIPERS = {"4yFAz7dp5WwuZs3vbWKedbRiUmSFxTVGCdsLAxfEQrG3": "4yFA", "CBKgS8Nj714YomoPPxhVLUWos7vSrWnuL2cVKnJqWo2s": "CBKg"}
VETO = {"2mqrindMAjJEQPLhroYWyiYPo5h9iAsahfdd4QtsjwdY", "8RvtT8189KpAq5MkXGVHFn6LdZpxq9PkvDeAvs1g5Yj4",
        "Fhbh1DTUDKt6qu9zVg8Q5VgnZa1WWWbxBxG6pCoJoggA"}
SIBLINGS = {"AWrTFnoSjCbJ4KhTzCQsjHwWrCt8jhXNKQubjdT1ENWw": "AWrT", "6yRBpeDDba4yASyjmWBLstxv1gYHVsvdHfuBpVpr63N2": "6yRB",
            "JE87rShx7BJJ82J9gSZDueAAPozjM32Exz9A2D3PY3bT": "JE87", "2mqrindMAjJEQPLhroYWyiYPo5h9iAsahfdd4QtsjwdY": "2mqr"}
OUTCOME_DELAY_S = 60
DELAY = int(sys.argv[sys.argv.index("--delay") + 1]) if "--delay" in sys.argv else 0
RECENT_N = 30


def sniper_buys_from_trades(tr):
    """First buy of each sniper in the token's pump.fun trades: {name: {slot, bt, sol}}. The amount is
    pump.fun's net SOL (the ~2.93 SOL signature is exactly 2.926 there; the wallet-level SOL change adds
    a variable tip and fee, 2.965-3.215)."""
    out = {}
    for t in tr:
        if t["type"] == "buy" and t["user"] in SNIPERS and SNIPERS[t["user"]] not in out:
            bt = datetime.fromisoformat(t["ts"].replace("Z", "+00:00")).timestamp()
            out[SNIPERS[t["user"]]] = {"slot": t["slot"], "bt": bt, "sol": t["sol"]}
    return out


def decision_features(tr, sniper_buys, delay=0):
    """Features of one token at decision time, from its pump.fun trades (sorted, from creation) and the
    snipers' buys {name: {slot, bt, sol}} (sniper_buys_from_trades). Shared by the universe builder and
    the live tool (sniper_follow.py). Only snipers that bought by the decision slot count."""
    s0, creator = tr[0]["slot"], tr[0]["user"]
    first_name, first = min(sniper_buys.items(), key=lambda kv: kv[1]["slot"])
    e = max(s0 + 2, first["slot"] + delay)
    f = window_features(tr, creator, s0, e - s0)
    in_win = {t["user"] for t in tr if t["type"] == "buy" and t["slot"] <= e}
    snipers_by_e = {k for k, v in sniper_buys.items() if v["slot"] <= e} | {SNIPERS[u] for u in in_win & set(SNIPERS)}
    return {
        "creator": creator, "t": first["bt"], "s0": s0, "decision_slot": e,
        "sniper_first": first_name, "sniper_slot": first["slot"],
        "sniper_sol": first["sol"], "sig_293": int(2.9 <= first["sol"] < 3.0),
        "sniper_small": int(first["sol"] < 2.5),
        "sniper_offset": first["slot"] - s0,
        "both_snipers": int(len(snipers_by_e) == len(SNIPERS)),
        "veto_present": int(bool(in_win & VETO)),
        "log_price_chg": math.log1p(max(0.0, f["price_change_pct"])),
        "log_sol_buys": math.log1p(f["sol_buys"]), "n_buyers": f["n_buyers"],
        "dev_buy_sol": f["dev_buy_sol"], "n_sells": f["n_sells"],
        "hour": int((first["bt"] % 86400) // 3600),
        "buyers": f["buyers"],
    }


def history_features(r, history):
    """Sequential features from earlier universe tokens whose outcome was visible OUTCOME_DELAY_S before
    r['t']. history: list of {t, creator, label} sorted by t."""
    vis = [h for h in history if h["t"] <= r["t"] - OUTCOME_DELAY_S]
    recent = [h["label"] for h in vis[-RECENT_N:]]
    same = [h["label"] for h in vis if h["creator"] == r["creator"]]
    return {"recent_follow_rate": (sum(recent) + 1) / (len(recent) + 4),
            "creator_prev_n": len(same), "creator_prev_followed": sum(same),
            "creator_follow_rate": (sum(same) + 0.25) / (len(same) + 1)}


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
        buys = sniper_buys_from_trades(tr)
        if not buys:
            missing["sniper absent des trades"] += 1
            continue
        r = decision_features(tr, buys, DELAY)
        later = {t["user"] for t in tr if t["type"] == "buy"}
        bot = ledger.get(m)
        if bot and bot["first_buy_slot"] <= r["decision_slot"]:
            # the wallet was already in when we would decide: buying then is copying it, not
            # reproducing its decision, so the token is not a decision we can make
            missing["wallet déjà entré à la décision"] += 1
            continue
        first_bt, e = r["t"], r["decision_slot"]
        r.update({
            "mint": m, "label": int(bot is not None),
            "bot_slot_offset": (bot["first_buy_slot"] - r["sniper_slot"]) if bot else None,
            "wallet_before_decision": int(bool(bot) and bot["first_buy_slot"] <= e),
            "siblings_later": sorted(SIBLINGS[u] for u in later & set(SIBLINGS)),
            # the wallet's own buy of this token (5% land before the sniper's) must not leak in
            "wallet_busy": int(any(a <= first_bt <= b + 1 and not (bot and a == bot["first_buy_bt"]) for a, b in
                                   intervals[max(0, bisect.bisect_right(starts, first_bt) - 2):
                                             bisect.bisect_right(starts, first_bt)])),
            "bot_buys_1h": bisect.bisect_left(buy_times, first_bt) - bisect.bisect_left(buy_times, first_bt - 3600)
            - int(bool(bot) and first_bt - 3600 <= bot["first_buy_bt"] < first_bt),
        })
        rows.append(r)
    rows.sort(key=lambda r: r["t"])

    for i, r in enumerate(rows):
        r.update(history_features(r, rows[:i]))

    json.dump(rows, open(os.path.join(DATA, "sniper_universe.json" if DELAY == 0 else f"sniper_universe_d{DELAY}.json"), "w"))
    print(f"{len(rows)} tokens dans l'univers ({sum(r['label'] for r in rows)} achetés par le wallet) | "
          f"exclus: {dict(missing)}")


if __name__ == "__main__":
    main()
