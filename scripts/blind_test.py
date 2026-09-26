#!/usr/bin/env python3
"""
Blind out-of-sample test of "follow 4yFAz7dp with the model and the adaptive threshold" on a day the
method has never seen. Each step is a separate command, run in this order, and the frozen model and
the decisions are committed before the wallet's buys for that day are fetched:

  blind_test.py freeze              train on everything up to the end of the study data; write
                                    blind_test/frozen_4yfa.json (model + state frozen at that time)
  blind_test.py collect             4yFAz7dp's buys since the freeze and the pump.fun trades of those
                                    tokens (the studied wallet's trades are stripped out). No wallet data.
  blind_test.py decide-a            variant A, strict blind: the wallet's follow rate and the creator
                                    history stay frozen; the score window rolls over the new tokens'
                                    scores only. -> blind_test/decisions_A.json
  blind_test.py fetch-wallet        the studied wallet's buys since the freeze (the answer)
  blind_test.py decide-b            variant B, live conditions: follow rate, creator history and
                                    threshold update with the wallet's buys visible >= 60 s before
                                    each decision. -> blind_test/decisions_B.json
  blind_test.py reveal              precision, capture, baselines -> blind_test/results.json

Scored day: EVAL_START (UTC) to the collection time minus 10 minutes (the wallet buys a median 9
slots, p90 21 slots, after the sniper, so later tokens could still be bought). Tokens bought by
4yFAz7dp between the freeze and EVAL_START only serve as history.
Scoring rule, same as the validation: a hit is a token the wallet bought after our decision slot;
tokens it had bought by our decision slot are left out (buying then would be copying it).
"""
import glob
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from analyze import parse_tx  # noqa: E402
from build_sniper_universe import (decision_features, history_features, sniper_buys_from_trades,  # noqa: E402
                                   too_late)
from fetch_leader_trades import rpc  # noqa: E402
from fetch_universe_trades import fetch as fetch_token_trades  # noqa: E402
import train_sniper_model as T  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "blind_test")
DAY = os.path.join(DATA, "blind")
SNIPER = "4yFAz7dp5WwuZs3vbWKedbRiUmSFxTVGCdsLAxfEQrG3"
WALLET = "AfPWFykWPZZxU2CyF6BcPoY2v8VEkmYS7ZggvELK7Pv1"
EVAL_START = datetime(2026, 9, 26, tzinfo=timezone.utc).timestamp()
MARGIN_S = 600
FEATURES = [f for f in T.FEATURES if f != "is_cbkg"]  # 4yFAz7dp-only model
K = 1.0


def fit_4yfa(rows):
    T.FEATURES = FEATURES
    m, mu, sd = T.fit(rows)
    return {"features": FEATURES, "coef": m.coef_[0].tolist(), "intercept": float(m.intercept_[0]),
            "mean": mu.tolist(), "std": sd.tolist()}


def score(model, r):
    r = dict(r)
    r["log_creator_prev_n"] = math.log1p(r["creator_prev_n"])
    x = np.array([float(r[k]) for k in model["features"]])
    z = model["intercept"] + float(np.dot(model["coef"], (x - np.array(model["mean"])) / np.array(model["std"])))
    return 1 / (1 + math.exp(-z))


def universe_4yfa():
    rows = [r for r in json.load(open(os.path.join(DATA, "sniper_universe.json"))) if r["sniper_first"] == "4yFA"]
    for i, r in enumerate(rows):  # history features within the 4yFAz7dp-only universe
        r.update(history_features(r, rows[:i]))
    return rows


def freeze():
    rows = universe_4yfa()
    model = fit_4yfa(rows)
    t_end = max(s["blockTime"] for s in json.load(open(os.path.join(DATA, "signatures.json"))) if s.get("blockTime"))
    hist = [{"t": r["t"], "creator": r["creator"], "label": r["label"], "score": score(model, r)} for r in rows]
    json.dump({"frozen_at_data_end": t_end, "k": K, "history_n": T.HISTORY_N, "outcome_delay_s": T.OUTCOME_DELAY_S,
               "model": model, "history": hist}, open(os.path.join(OUT, "frozen_4yfa.json"), "w"))
    vis = hist[-T.HISTORY_N:]
    print(f"modèle figé sur {len(rows)} tokens (jusqu'au {time.strftime('%F %T', time.gmtime(t_end))} UTC) | "
          f"taux de suivi figé {np.mean([h['label'] for h in vis]):.3f} sur les {len(vis)} derniers")


def collect():
    os.makedirs(os.path.join(DAY, "trades"), exist_ok=True)
    fz = json.load(open(os.path.join(OUT, "frozen_4yfa.json")))
    since, now = fz["frozen_at_data_end"], time.time()
    sigs, before = [], None
    while True:
        p = {"limit": 1000, **({"before": before} if before else {})}
        page = rpc("getSignaturesForAddress", [SNIPER, p])
        if not page:
            break
        sigs += [s for s in page if (s.get("blockTime") or 0) > since]
        before = page[-1]["signature"]
        if (page[-1].get("blockTime") or 0) <= since:
            break
        time.sleep(0.25)
    ok = [s for s in sigs if s["err"] is None]
    print(f"4yFAz7dp: {len(sigs)} signatures depuis le gel, {len(ok)} réussies", flush=True)
    buys = {}
    for i, s in enumerate(sorted(ok, key=lambda s: s["slot"]), 1):
        tx = rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}])
        ev = parse_tx(s["signature"], tx, SNIPER) if tx else None
        if ev and ev["side"] == "BUY" and ev["mint"] not in buys:
            buys[ev["mint"]] = {"mint": ev["mint"], "slot": s["slot"], "bt": s["blockTime"]}
        if i % 100 == 0:
            print(f"  {i}/{len(ok)} transactions, {len(buys)} achats", flush=True)
        time.sleep(0.15)
    cutoff = now - MARGIN_S
    buys = {m: b for m, b in buys.items() if b["bt"] <= cutoff}
    print(f"{len(buys)} tokens achetés par 4yFAz7dp entre le gel et {time.strftime('%T', time.gmtime(cutoff))} UTC", flush=True)
    for i, (m, b) in enumerate(sorted(buys.items(), key=lambda kv: kv[1]["bt"]), 1):
        p = os.path.join(DAY, "trades", f"{m}.json")
        if not os.path.exists(p):
            d = fetch_token_trades(m, b["bt"])
            d["trades"] = [t for t in d["trades"] if t["user"] != WALLET]  # blind: no wallet trades at all
            json.dump(d, open(p, "w"))
            time.sleep(0.3)
        if i % 50 == 0:
            print(f"  trades {i}/{len(buys)}", flush=True)
    json.dump({"collected_at": now, "cutoff": cutoff, "buys": buys}, open(os.path.join(DAY, "sniper_buys.json"), "w"))


def candidates():
    """Today's decision rows (features without history), in time order, same filters as the universe."""
    meta = json.load(open(os.path.join(DAY, "sniper_buys.json")))
    rows, dropped = [], {}
    for m in meta["buys"]:
        d = json.load(open(os.path.join(DAY, "trades", f"{m}.json")))
        tr = d["trades"]
        reason = None
        if not d["complete"] or not tr:
            reason = "création non atteinte (sniper tardif)"
        else:
            buys = sniper_buys_from_trades(tr)
            if "4yFA" not in buys:
                reason = "achat 4yFA absent des trades"
            elif too_late(tr, min(b["bt"] for b in buys.values())):
                reason = "sniper au-delà des 300 premiers trades"
            else:
                r = decision_features(tr, buys)
                if r["sniper_first"] != "4yFA":
                    reason = "CBKg a acheté avant 4yFA"
                else:
                    r["mint"] = m
                    rows.append(r)
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
    rows.sort(key=lambda r: r["t"])
    return rows, dropped, meta


def decide(variant):
    fz = json.load(open(os.path.join(OUT, "frozen_4yfa.json")))
    model, hist = fz["model"], list(fz["history"])
    rows, dropped, meta = candidates()
    wallet_buys = {}
    if variant == "B":
        wallet_buys = json.load(open(os.path.join(DAY, "wallet_buys.json")))["buys"]
    frozen_vis = [h for h in hist][-fz["history_n"]:]
    frozen_rate = float(np.mean([h["label"] for h in frozen_vis]))
    out = []
    seen = []  # today's tokens so far: {t, creator, score, label?}
    for r in rows:
        if variant == "A":
            r.update(history_features(r, hist))  # frozen: nothing after the freeze has a label
            window = [h["score"] for h in hist] + [s["score"] for s in seen]
            window = window[-fz["history_n"]:]
            rate = frozen_rate
        else:
            # outcome of a token = did the wallet buy it; visible once its buy (or the delay) has passed
            labelled = hist + [{"t": s["t"], "creator": s["creator"], "score": s["score"],
                                "label": int(s["mint"] in wallet_buys)} for s in seen]
            r.update(history_features(r, labelled))
            vis = [h for h in labelled if h["t"] <= r["t"] - fz["outcome_delay_s"]][-fz["history_n"]:]
            window = [h["score"] for h in vis]
            rate = float(np.mean([h["label"] for h in vis]))
        p = score(model, r)
        thr = float(np.quantile(window, max(0.0, 1 - fz["k"] * rate)))
        out.append({"mint": r["mint"], "t": r["t"], "decision_slot": r["decision_slot"], "score": round(p, 4),
                    "threshold": round(thr, 4), "rate_used": round(rate, 4), "buy": bool(p >= thr),
                    "sniper_sol": r["sniper_sol"], "sniper_small": r["sniper_small"], "sig_293": r["sig_293"]})
        if not (variant == "B" and r["mint"] in wallet_buys and wallet_buys[r["mint"]]["slot"] <= r["decision_slot"]):
            # B, like the validation, keeps tokens the wallet was already in out of the history (visible live);
            # A cannot know it without looking at the wallet, so they stay in its score window
            seen.append({"t": r["t"], "creator": r["creator"], "score": p, "mint": r["mint"]})
    json.dump({"variant": variant, "eval_start": EVAL_START, "cutoff": meta["cutoff"], "dropped": dropped,
               "decisions": out}, open(os.path.join(OUT, f"decisions_{variant}.json"), "w"), indent=1)
    ev = [d for d in out if d["t"] >= EVAL_START]
    print(f"variante {variant}: {len(out)} tokens décidés ({len(ev)} sur la journée évaluée), "
          f"{sum(d['buy'] for d in ev)} ACHATS | exclus: {dropped}")


def fetch_wallet():
    fz = json.load(open(os.path.join(OUT, "frozen_4yfa.json")))
    since = fz["frozen_at_data_end"]
    sigs, before = [], None
    while True:
        p = {"limit": 1000, **({"before": before} if before else {})}
        page = rpc("getSignaturesForAddress", [WALLET, p])
        if not page:
            break
        sigs += [s for s in page if (s.get("blockTime") or 0) > since]
        before = page[-1]["signature"]
        if (page[-1].get("blockTime") or 0) <= since:
            break
        time.sleep(0.25)
    ok = [s for s in sigs if s["err"] is None]
    print(f"wallet: {len(sigs)} signatures depuis le gel, {len(ok)} réussies", flush=True)
    buys = {}
    for i, s in enumerate(sorted(ok, key=lambda s: s["slot"]), 1):
        tx = rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}])
        ev = parse_tx(s["signature"], tx, WALLET) if tx else None
        if ev and ev["side"] == "BUY" and ev["mint"] not in buys:
            buys[ev["mint"]] = {"slot": s["slot"], "bt": s["blockTime"], "sol": ev["quote_amount_sol"]}
        if i % 100 == 0:
            print(f"  {i}/{len(ok)}", flush=True)
        time.sleep(0.15)
    json.dump({"fetched_at": time.time(), "buys": buys}, open(os.path.join(DAY, "wallet_buys.json"), "w"))
    print(f"{len(buys)} tokens achetés par le wallet depuis le gel", flush=True)


def reveal():
    wb = json.load(open(os.path.join(DAY, "wallet_buys.json")))["buys"]
    res = {}
    for variant in ("A", "B"):
        D = json.load(open(os.path.join(OUT, f"decisions_{variant}.json")))
        cutoff = D["cutoff"]
        day = [d for d in D["decisions"] if EVAL_START <= d["t"] <= cutoff]
        # the wallet was already in at our decision slot: not a reproducible decision (same rule as validation)
        late = [d for d in day if d["mint"] in wb and wb[d["mint"]]["slot"] <= d["decision_slot"]]
        day = [d for d in day if d not in late]
        hit = lambda d: d["mint"] in wb
        n_wallet = sum(1 for b in wb.values() if EVAL_START <= b["bt"] <= cutoff)
        rules = {
            f"modèle + seuil adaptatif ({variant})": [d for d in day if d["buy"]],
            "suivre tout 4yFAz7dp": day,
            "sauf achats < 2,5 SOL": [d for d in day if not d["sniper_small"]],
        }
        res[variant] = {"n_tokens": len(day), "excluded_wallet_already_in": len(late), "wallet_buys_in_window": n_wallet,
                        "wallet_buys_on_4yfa_tokens": sum(hit(d) for d in day), "rules": {}}
        print(f"\n=== variante {variant} | journée du {time.strftime('%d/%m', time.gmtime(EVAL_START))} "
              f"00:00 -> {time.strftime('%H:%M', time.gmtime(cutoff))} UTC ===")
        print(f"  {len(day)} tokens où 4yFAz7dp a acheté en premier ({len(late)} exclus: wallet déjà entré) | "
              f"le wallet a acheté {n_wallet} tokens sur la période, dont {res[variant]['wallet_buys_on_4yfa_tokens']} parmi ceux-ci")
        for name, sel in rules.items():
            h = sum(hit(d) for d in sel)
            r = {"n": len(sel), "hits": h, "precision": round(h / max(1, len(sel)), 3),
                 "capture_all_wallet_buys": round(h / max(1, n_wallet), 3)}
            res[variant]["rules"][name] = r
            print(f"  {name:36s} {h:3d}/{len(sel):<4d} précision {100 * r['precision']:5.1f}% | "
                  f"capture {100 * r['capture_all_wallet_buys']:4.1f}% des achats du wallet")
    json.dump(res, open(os.path.join(OUT, "results.json"), "w"), indent=1)


if __name__ == "__main__":
    {"freeze": freeze, "collect": collect, "decide-a": lambda: decide("A"), "fetch-wallet": fetch_wallet,
     "decide-b": lambda: decide("B"), "reveal": reveal}[sys.argv[1]]()
