"""User's 3 extra exits on top of the bot's rule (half x1.5, rest x2, stop at entry after x1.5, 20 min, -50 %),
minute candles after the wallet's buy, copy entry +1 %. r1: -30 % in the first minute -> sell all;
r2: at 5 min, price within +/-20 % of entry -> sell all (B: price never left +/-20 % during the 5 min);
r3: x1.8 reached -> sell the rest 5 s later (bounds: at 1.8, or at that minute's close)."""
import collections, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "w7b_age.py")).read().split("RULES =")[0])
exec("def load" + open("w7b_age.py").read().split("def load")[1].split("dev = set(E.WIN)")[0])


def run2(P, Ef, r1=False, r2=None, r3=None):
    left, cash, ntx, k, lvl = 1.0, 0.0, 1, 0, 0.5 * Ef
    val = E.STAKE / (1 + E.FEE)
    levels = ((1.5, .5), (2.0, .5))
    sell = lambda f, px: (f * val * px / Ef * (1 - E.SLIP) * (1 - E.FEE))
    hi5, lo5 = 0, 9e9
    for i, (o, h, l, c) in enumerate(P, 1):
        if i == 1 and r1 and l <= 0.7 * Ef and lvl < 0.7 * Ef:
            cash += sell(left, min(o, 0.7 * Ef)); left = 0; ntx += 1; break
        if l <= lvl:
            cash += sell(left, min(o, lvl)); left = 0; ntx += 1; break
        while k < len(levels) and h >= levels[k][0] * Ef:
            f = min(levels[k][1], left); cash += sell(f, levels[k][0] * Ef); left -= f; ntx += 1; k += 1
            lvl = max(lvl, Ef)
        if left <= 1e-9: break
        if r3 and h >= 1.8 * Ef:
            cash += sell(left, 1.8 * Ef if r3 == "1.8" else min(max(c, lvl), 1.8 * Ef)); left = 0; ntx += 1; break
        hi5, lo5 = max(hi5, h), min(lo5, l)
        if r2 and i == 5:
            flat = 0.8 * Ef < c < 1.2 * Ef if r2 == "A" else (hi5 < 1.2 * Ef and lo5 > 0.8 * Ef)
            if flat:
                cash += sell(left, c); left = 0; ntx += 1; break
        if i >= 20:
            cash += sell(left, c); left = 0; ntx += 1; break
    if left > 1e-9:
        cash += sell(left, P[-1][3]); ntx += 1
    return cash - E.STAKE - E.GAS * ntx


rows = [r for r in load(E.D, set(E.WIN) | set(E.LOSS)) if r["fill"] * 1e9 >= 45e3 and r.get("copy")]
day = lambda r: time.strftime("%d/%m", time.gmtime(r["bt"] + 7200))
V = {"règle actuelle du bot": {},
     "+ r1 : -30 % dans la 1re minute": dict(r1=True),
     "+ r2 : à 5 min, entre -20 % et +20 %": dict(r2="A"),
     "+ r2 variante : jamais sorti de ±20 % en 5 min": dict(r2="B"),
     "+ r3 : x1,8 -> tout (vendu à x1,8)": dict(r3="1.8"),
     "+ r3 : x1,8 -> tout (prix 5 s après pessimiste)": dict(r3="close"),
     "+ les 3 (r2 au point de 5 min, r3 à x1,8)": dict(r1=True, r2="A", r3="1.8"),
     "+ les 3 (r3 pessimiste)": dict(r1=True, r2="A", r3="close")}
print(f"{len(rows)} tokens (MC >= 45 k$, créés <= 2 h avant), entrée au prix de copie +1 %, 50 $ par trade")
for n, kw in V.items():
    v = np.array([run2(r["P"], r["copy"] * 1.01, **kw) for r in rows])
    d = collections.defaultdict(list)
    for r, x in zip(rows, v): d[day(r)].append(x)
    print(f"{n:50s} {v.mean() / 50:+6.1%} par trade   " + "  ".join(f"{k}: {np.mean(x) / 50:+.1%}" for k, x in sorted(d.items())))
