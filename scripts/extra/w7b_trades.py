"""Wallet 7BNaxx6K, trade by trade (swap-api.pump.fun, USD prices, any quote token): from its first buy on each
listed token to 30 min later. Reference = its own fill price. Drawdown before x2 on wins, path of losses, and
stop rules with a take profit at x2 (fills at the first trade beyond the level, 2 % slippage, fees 2.25 % a side)."""
import json, os, sys, time, glob
import numpy as np
sys.path.insert(0, "scripts")
import copy_fetch as C
from callout_test import fetch
S = "/tmp/claude-0/-home-user-lk/85657a79-e9b6-5495-86ab-a08a82fed438/scratchpad/"
W = "7BNaxx6KdUYrjACNQZ9He26NBFoFxujQMAfNLnArLGH5"
D = "data/wallet_7BNaxx6K"
exec(open(os.path.join(os.path.dirname(__file__), "w7b_lists.py")).read())   # WIN, LOSS (the user's lists)
HORIZON = 30 * 60
fr = lambda t: time.strftime("%H:%M:%S", time.gmtime(t + 7200))
ts = lambda s: int(time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))) - time.timezone

# first buy (time and signature) from the wallet's own transactions
first = {}
for f in glob.glob(f"{D}/tx/*.json"):
    tx = json.load(open(f))
    p = C.parse_tx(W, tx)
    if not p: continue
    for m, q in p["toks"].items():
        if m in WIN + LOSS and q > 0 and (m not in first or p["slot"] < first[m]["slot"]):
            first[m] = dict(slot=p["slot"], bt=p["bt"], sig=p["sig"], q=q)

os.makedirs(S + "w7b_tr", exist_ok=True)
rows = {}
for lab, lst in (("WIN", WIN), ("LOSS", LOSS)):
    for m in dict.fromkeys(lst):
        if m not in first:
            print(f"{lab} {m[:8]} : pas d'achat trouvé"); continue
        fb = first[m]
        out = S + f"w7b_tr/{m}.json"
        if os.path.exists(out):
            tr = json.load(open(out))
        else:
            tr, ok = fetch(m, fb["bt"] - 60, fb["bt"] + HORIZON)
            if not ok:
                print(f"{lab} {m[:8]} : téléchargement incomplet"); continue
            json.dump(tr, open(out, "w"))
        mine = [t for t in tr if t["tx"] == fb["sig"]]
        if not mine:
            print(f"{lab} {m[:8]} : achat du wallet absent des données pump.fun ({len(tr)} trades)"); continue
        k0 = tr.index(mine[-1])
        E = float(mine[0].get("fillPriceUsd") or mine[0]["priceUsd"])
        T = np.array([ts(t["timestamp"]) for t in tr[k0 + 1:]]) - fb["bt"]
        X = np.array([float(t["priceUsd"]) for t in tr[k0 + 1:]]) / E
        rows[m] = dict(lab=lab, T=T.tolist(), X=X.tolist(), bt=fb["bt"], E=E)
json.dump(rows, open(S + "w7b_tr_paths.json", "w"))

print(f"\n{'':4s} {'token':9s} {'achat':8s} {'x2 atteint':>10s} {'pire baisse avant x2':>20s} {'plus haut 30 min':>16s} "
      f"{'plus bas 30 min':>15s}  prix à +1/+2/+5/+10/+20/+30 min")
for m, r in rows.items():
    T, X = np.array(r["T"]), np.array(r["X"])
    if not len(X):
        continue
    i2 = np.flatnonzero(X >= 2)
    t2 = f"+{T[i2[0]] / 60:.1f} min" if len(i2) else "jamais"
    mae = X[:i2[0] + 1].min() - 1 if len(i2) else X.min() - 1
    at = lambda s: X[max(0, np.searchsorted(T, s, side="right") - 1)] if T[0] <= s else 1.0
    print(f"{r['lab']:4s} {m[:8]:9s} {fr(r['bt']):8s} {t2:>10s} {mae:+19.0%} {X.max():15.2f}x {X.min():14.2f}x  "
          + " ".join(f"{at(s):.2f}" for s in (60, 120, 300, 600, 1200, 1800)))
