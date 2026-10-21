"""Stop rules with a take profit at x2, trade by trade, on the 30 min after the wallet's first buy (multiples of its
fill price). 50 $ per token, 2.25 % fee per side, 2 % slippage on exits, 0.21 $ gas per transaction."""
import json
import numpy as np
S = "/tmp/claude-0/-home-user-lk/85657a79-e9b6-5495-86ab-a08a82fed438/scratchpad/"
FEE, SLIP, GAS, STAKE = 0.0225, 0.02, 0.21, 50.0
R = {m: r for m, r in json.load(open(S + "w7b_tr_paths.json")).items() if r["X"]}


def run(T, X, stop=None, tmax=None, be=None, grace=0):
    """stop: fraction lost (0.3 = -30 %), checked after `grace` s; tmax: min without x2 -> sell;
    be: (rise, floor): once the price reached `rise` x, the stop moves up to `floor` x. Returns ($, exit multiple, s)"""
    floor, armed = (1 - stop) if stop else 0.0, False
    for t, x in zip(T, X):
        if x >= 2.0:
            return STAKE * (2.0 * (1 - SLIP) * (1 - FEE) / (1 + FEE) - 1) - 2 * GAS, 2.0, t
        if be and x >= be[0]:
            armed = True
        level = max(floor, be[1]) if armed else floor
        if t >= grace and x <= level:
            return STAKE * (x * (1 - SLIP) * (1 - FEE) / (1 + FEE) - 1) - 2 * GAS, x, t
        if tmax and t >= tmax * 60:
            return STAKE * (x * (1 - SLIP) * (1 - FEE) / (1 + FEE) - 1) - 2 * GAS, x, t
    x = X[-1]
    return STAKE * (x * (1 - SLIP) * (1 - FEE) / (1 + FEE) - 1) - 2 * GAS, x, T[-1]


RULES = {"sans stop (x2 ou prix à 30 min)": {}}
for s in (0.2, 0.3, 0.4, 0.5, 0.6):
    RULES[f"stop -{int(s * 100)} %"] = dict(stop=s)
for s in (0.3, 0.4):
    RULES[f"stop -{int(s * 100)} % seulement après 1 min"] = dict(stop=s, grace=60)
for t in (3, 5, 10, 15, 20):
    RULES[f"sortie si pas x2 en {t} min"] = dict(tmax=t)
for s, t in ((0.4, 10), (0.4, 15), (0.5, 10), (0.5, 20), (0.3, 10)):
    RULES[f"stop -{int(s * 100)} % + sortie si pas x2 en {t} min"] = dict(stop=s, tmax=t)
for rise, fl in ((1.5, 1.0), (1.5, 1.2), (1.3, 1.0)):
    RULES[f"stop -40 %, remonté à x{fl:g} dès x{rise:g}"] = dict(stop=0.4, be=(rise, fl))

wins = [m for m, r in R.items() if r["lab"] == "WIN"]
losses = [m for m, r in R.items() if r["lab"] == "LOSS"]
print(f"{len(wins)} wins et {len(losses)} looses avec données\n")
print(f"{'règle':46s} {'x2 gardés':>9s} {'wins':>8s} {'looses':>8s} {'total':>8s}   sortie des looses (multiple)")
for name, kw in RULES.items():
    vw = [run(R[m]["T"], R[m]["X"], **kw) for m in wins]
    vl = [run(R[m]["T"], R[m]["X"], **kw) for m in losses]
    kept = sum(x >= 2.0 for _, x, _ in vw)
    tw, tl = sum(v for v, _, _ in vw), sum(v for v, _, _ in vl)
    print(f"{name:46s} {kept:>4d}/{len(wins):<4d} {tw:+7.0f}$ {tl:+7.0f}$ {tw + tl:+7.0f}$   "
          + " ".join(f"x{x:.2f}@{t / 60:.0f}m" for _, x, t in vl))
