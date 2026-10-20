#!/usr/bin/env python3
"""
Quick test of exit rules on a wallet's N most recent tokens (copy_fetch.py's sigs.json must exist).

1. getTransaction on the wallet's successful signatures, newest first, until N tokens have a buy that is at
   least --age minutes old and at least 15 min after the oldest transaction fetched (so the position start is seen).
2. For each token: all its trades from 5 min before the wallet's first buy to 45 min after (swap-api.pump.fun),
   then a 50 $ copy 2 s after that buy, sold by F1R, F1, "copy its sells" (sell all once it has sold >= 50 %
   of its tokens) and F1R + its sells.
3. Checks: the wallet's own result from its transactions, and the time it had sold half of its tokens from its
   transactions vs from the trades feed used by the simulation.

Usage: wallet_sample.py <wallet> [--n 30] [--age 45]
"""
import argparse
import collections
import json
import os
import queue
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import copy_fetch as C  # noqa: E402
import flow_backtest as B  # noqa: E402
from callout_test import fetch  # noqa: E402

TZ = 7200
RPCS = [("https://solana-rpc.publicnode.com", 0.3, 3), ("https://solana-mainnet.gateway.tatum.io", 0.3, 3),
        ("https://api.mainnet-beta.solana.com", 0.9, 1)]
RULES = {"F1R": B.runner_rule(), "F1": B.flow_rule(), "ses ventes": dict(wallet=(0.5, 1.0)),
         "F1R+ventes": dict(B.runner_rule(), wallet=(0.5, 1.0))}
fr = lambda s: time.strftime("%d/%m %H:%M:%S", time.gmtime(s + TZ))


def get_txs(sigs, D):
    """fetch the transactions not cached yet (parallel over public RPCs)"""
    q = queue.Queue()
    for s in sigs:
        if not os.path.exists(os.path.join(D, "tx", s + ".json")):
            q.put((s, 0))

    def worker(url, gap):
        while True:
            try:
                sig, tries = q.get(timeout=3)
            except queue.Empty:
                return
            try:
                r = C.http_json(url, {"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                                      "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}]},
                                headers={"User-Agent": "curl/8.5.0"})
                if r.get("result"):
                    json.dump(r["result"], open(os.path.join(D, "tx", sig + ".json"), "w"))
                elif tries < 6:
                    q.put((sig, tries + 1))
            except Exception:
                if tries < 6:
                    q.put((sig, tries + 1))
                time.sleep(2)
            time.sleep(gap)
    th = [threading.Thread(target=worker, args=(u, g)) for u, g, n in RPCS for _ in range(n)]
    [t.start() for t in th]
    [t.join() for t in th]


def positions(w, D, sigs):
    """per token: wallet swaps in time order (from the cached transactions)"""
    by = collections.defaultdict(list)
    for s in sigs:
        f = os.path.join(D, "tx", s + ".json")
        if not os.path.exists(f):
            continue
        p = C.parse_tx(w, json.load(open(f)))
        if p and len(p["toks"]) == 1:
            (m, q), = p["toks"].items()
            if m not in C.NOT_MEME and abs(p["sol"]) >= 0.01:
                by[m].append(dict(p, q=q))
    return {m: sorted(v, key=lambda x: x["slot"]) for m, v in by.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wallet")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--age", type=float, default=45.0)
    a = ap.parse_args()
    w = a.wallet
    D = C.data_dir(w)
    os.makedirs(os.path.join(D, "tx"), exist_ok=True)
    allsig = [s for s in json.load(open(os.path.join(D, "sigs.json"))) if s["err"] is None]
    allsig.sort(key=lambda s: -s["blockTime"])
    now = time.time()
    k, step = 0, 1500
    while True:                                          # widen the fetched range until N tokens are covered
        k = min(len(allsig), k + step)
        get_txs([s["signature"] for s in allsig[:k]], D)
        oldest = allsig[k - 1]["blockTime"]
        pos = positions(w, D, [s["signature"] for s in allsig[:k]])
        ok = {m: v for m, v in pos.items() if any(x["q"] > 0 for x in v)}
        firsts = {m: next(x for x in v if x["q"] > 0) for m, v in ok.items()}
        good = [m for m, f in firsts.items() if f["bt"] <= now - a.age * 60 and f["bt"] >= oldest + 900]
        print(f"{k} transactions ({fr(oldest)} -> maintenant) : {len(good)} tokens utilisables", flush=True)
        if len(good) >= a.n or k == len(allsig):
            break
    good = sorted(good, key=lambda m: -firsts[m]["bt"])[:a.n]
    rows = []
    for m in sorted(good, key=lambda m: firsts[m]["bt"]):
        f = firsts[m]
        tr, complete = fetch(m, f["bt"] - 300, f["bt"] + B.HORIZON)
        sol_in = -sum(x["sol"] for x in pos[m] if x["q"] > 0)
        sol_out = sum(x["sol"] for x in pos[m] if x["q"] < 0)
        held = sum(x["q"] for x in pos[m] if x["q"] > 0)
        cum, t_half = 0.0, None
        for x in pos[m]:
            if x["q"] < 0:
                cum -= x["q"]
                if t_half is None and cum >= 0.5 * held:
                    t_half = x["bt"]
        row = dict(mint=m, bt=f["bt"], sol_in=sol_in, sol_out=sol_out, t_half=t_half, n_trades=len(tr))
        path = os.path.join(D, "sample", f"{m}_{f['bt']}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump({"mint": m, "bt": f["bt"], "sig": f["sig"], "complete": complete, "trades": tr}, open(path, "w"))
        win = B.Window(path, w)
        if not win.ok:
            row["skip"] = "pas de données de trades" if not tr else ("achat absent du flux de trades" if
                          all(t["tx"] != f["sig"] for t in tr) else "trop peu de trades")
            rows.append(row)
            continue
        p = B.Path(win, stake=50.0)
        row.update(venue=win.venue, mc0=p.entry * 1e9, wallet_mc=win.wallet_px * 1e9, top=float(np.nanmax(p.x)),
                   new120=B.cum_new(p, 120))
        wi = np.flatnonzero(win.A["wsold"][win.i0:] >= 0.5)
        row["t_half_feed"] = p.t0 + int(wi[0]) if len(wi) else None
        for lab, rule in RULES.items():
            row[lab] = B.evaluate(p, **rule)
        rows.append(row)
    json.dump(rows, open(os.path.join(D, "sample_results.json"), "w"), indent=1)

    print(f"\n{'achat (FR)':15s} {'token':9s} {'lieu':8s} {'MC wallet':>9s} {'MC copie':>8s} {'max':>6s} "
          f"{'nouv.2m':>7s} {'wallet':>7s} " + " ".join(f"{k:>10s}" for k in RULES) + "  vente 50 % (tx / flux)")
    for r in rows:
        if "skip" in r:
            print(f"{fr(r['bt'])} {r['mint'][:8]}  ignoré : {r['skip']}")
            continue
        mult = r["sol_out"] / r["sol_in"] if r["sol_in"] > 0 else float("nan")
        th = lambda t: time.strftime("%H:%M:%S", time.gmtime(t + TZ)) if t else "—"
        print(f"{fr(r['bt'])} {r['mint'][:8]}  {r['venue']:8s} {r['wallet_mc'] / 1e3:8.1f}k {r['mc0'] / 1e3:7.1f}k "
              f"x{r['top']:5.2f} {r['new120']:7.0f} x{mult:6.2f} " + " ".join(f"{r[k]:+10.2f}" for k in RULES)
              + f"  {th(r['t_half'])} / {th(r['t_half_feed'])}")
    ok = [r for r in rows if "skip" not in r]
    for lab, sub in (("tous", ok), ("courbe pump.fun", [r for r in ok if r["venue"] == "pump"])):
        if not sub:
            continue
        print(f"\nTotal {lab} ({len(sub)} tokens, 50 $ chacun) :")
        for k in RULES:
            v = np.array([r[k] for r in sub])
            s = np.sort(v)[::-1]
            print(f"  {k:11s} {v.sum():+8.2f} $   gagnants {int((v > 0).sum()):2d}/{len(v)}   sans le meilleur {s[1:].sum():+8.2f} $"
                  f"   sans les 3 meilleurs {s[3:].sum():+8.2f} $")
        si, so = sum(r["sol_in"] for r in sub), sum(r["sol_out"] for r in sub)
        print(f"  le wallet lui-même : {si:.2f} SOL investis, {so:.2f} SOL récupérés (x{so / si:.2f})")


if __name__ == "__main__":
    main()
