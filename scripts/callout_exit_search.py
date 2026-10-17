#!/usr/bin/env python3
"""
Which take-profit / stop-loss to use after a callout entry? Backtest on data/callout_candles.json.

  callout_exit_search.py [--delay 2] [--cost 0.02] [--max-run 1.3] [--max-coin-age 2] [--train 0.6]

Entry: the close of the first price bar at or after the call + --delay s (1 s bars), i.e. the price a buy sent
through Trojan gets ~2 s after the call. Callouts on coins older than --max-coin-age days or whose price is already
up more than --max-run x since the callout at entry are left out, as the bot does (0 turns a filter off).
Exits, checked bar by bar after the entry bar (1 s bars up to 5 min, 15 s up to 1 h, 1 min up to 4 h); within a
bar the stop is checked before the target, and a bar opening beyond a level fills at its open:
  - fixed: take profit at x TP and/or stop loss at x SL, or else sold at the max hold time;
  - trailing: once the peak reaches x ACT, sold when the price falls TRAIL below the peak (and the SL below it);
  - partial: half sold at x TP1, the rest under a trailing stop.
Returns are net of --cost per side (Trojan's 1 % fee plus slippage). Rules are ranked on the oldest --train share
of the callouts and checked on the newest ones; entry delays of 1-10 s are compared for the rules kept.
Output: data/callout_exit_search.json
"""
import json
import math
import os
import statistics
import sys

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
DELAY, COST = float(arg("--delay", "2")), float(arg("--cost", "0.02"))
MAX_RUN, MAX_COIN_AGE, TRAIN = float(arg("--max-run", "1.3")), float(arg("--max-coin-age", "2")), float(arg("--train", "0.6"))
HOLDS = {"5 min": 300, "15 min": 900, "1 h": 3600, "4 h": 14400}


def path(c):
    """Merged bars [t (s after the call), open, high, low, close]: 1 s up to 5 min, 15 s up to 1 h, 1 min after."""
    t0, out = c["t0"], []
    for res, lo, hi in (("1s", -60, 300), ("15s", 300, 3600), ("1m", 3600, 14400)):
        out += [[(b[0] - t0) / 1000] + b[1:] for b in c["bars"].get(res, []) if lo <= (b[0] - t0) / 1000 < hi]
    return out


def entry(bars, delay):
    """Index and price of the entry: the close of the first bar at or after the call + delay."""
    for i, b in enumerate(bars):
        if b[0] >= delay - 0.999:
            return i, b[4]
    return None, None


def simulate(bars, i0, px, rule):
    """Gross exit multiple of one rule: (kind, tp, sl, act, trail, hold, tp1)."""
    kind, tp, sl, act, trail, hold, tp1 = rule
    peak, sold, value = px, 0.0, 0.0      # sold: fraction already sold; value: its proceeds as a multiple
    for b in bars[i0 + 1:]:
        t, o, h, lo, c = b
        if t - bars[i0][0] > hold:
            return value + (1 - sold) * o / px
        stops = [px * sl] if sl else []
        if trail and peak >= px * act:
            stops.append(peak * (1 - trail))
        stop = max(stops) if stops else None
        if stop and lo <= stop:
            return value + (1 - sold) * min(o, stop) / px
        if kind == "partial" and not sold and h >= px * tp1:
            sold, value = 0.5, 0.5 * max(o, px * tp1) / px
        if tp and h >= px * tp:
            return value + (1 - sold) * max(o, px * tp) / px
        peak = max(peak, h)
    return value + (1 - sold) * bars[-1][4] / px


def rules():
    out = []
    for hold in HOLDS.values():
        for tp in (None, 1.2, 1.3, 1.5, 1.75, 2, 2.5, 3, 5):
            for sl in (None, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9):
                out.append(("fixed", tp, sl, None, None, hold, None))
        for act in (1.0, 1.3, 1.5):
            for trail in (0.15, 0.25, 0.35):
                for sl in (None, 0.7, 0.8):
                    out.append(("trailing", None, sl, act, trail, hold, None))
        for tp1 in (1.5, 2):
            for trail in (0.25, 0.35):
                for sl in (None, 0.7):
                    out.append(("partial", None, sl, 1.0, trail, hold, tp1))
    return out


def label(rule):
    kind, tp, sl, act, trail, hold, tp1 = rule
    h = next(k for k, v in HOLDS.items() if v == hold)
    s = f"SL x{sl:g} (-{(1 - sl) * 100:.0f} %)" if sl else "sans SL"
    if kind == "fixed":
        return f"TP x{tp:g}, {s}, max {h}" if tp else f"pas de TP, {s}, max {h}"
    if kind == "trailing":
        return f"trailing -{trail * 100:.0f} % dès x{act:g}, {s}, max {h}"
    return f"50 % à x{tp1:g} puis trailing -{trail * 100:.0f} %, {s}, max {h}"


def stats(rets):
    if not rets:
        return {"n": 0}
    m, sd = statistics.mean(rets), statistics.pstdev(rets)
    return {"n": len(rets), "mean": m, "ci95": 1.96 * sd / math.sqrt(len(rets)), "median": statistics.median(rets),
            "win": sum(r > 0 for r in rets) / len(rets)}


def fmt(s):
    if not s["n"]:
        return "aucun trade"
    return (f"{s['mean'] * 100:+6.1f} % ± {s['ci95'] * 100:4.1f} par trade, médiane {s['median'] * 100:+6.1f} %, "
            f"gagnants {s['win'] * 100:3.0f} % (n={s['n']})")


def trades(calls, delay):
    """(callout, bars, entry index, entry price) for the callouts the bot would have bought."""
    out = []
    for c in calls:
        bars = path(c)
        i0, px = entry(bars, delay)
        if i0 is None or not px:
            continue
        if MAX_COIN_AGE and (not c.get("coin_created") or (c["t0"] - c["coin_created"]) / 8.64e7 > MAX_COIN_AGE):
            continue
        if MAX_RUN and c.get("price_call") and px / c["price_call"] > MAX_RUN:
            continue
        out.append((c, bars, i0, px))
    return out


def net(gross):
    return gross * (1 - COST) ** 2 - 1


def main():
    calls = sorted(json.load(open(os.path.join(DATA, "callout_candles.json"))).values(), key=lambda c: c["t0"])
    cut = int(len(calls) * TRAIN)
    train, test = trades(calls[:cut], DELAY), trades(calls[cut:], DELAY)
    filters = ", ".join(s for s in (MAX_COIN_AGE and f"âge du coin ≤ {MAX_COIN_AGE:g} j",
                                    MAX_RUN and f"prix ≤ x{MAX_RUN:g} au moment de l'achat") if s) or "sans filtre"
    print(f"{len(calls)} callouts ; achetés par le bot ({filters}, entrée {DELAY:g} s après le call) : {len(train)} pour "
          f"choisir, {len(test)} pour vérifier ; coûts {COST * 100:g} % par sens")
    all_t = train + test
    for horizon in (300, 3600):
        peaks = [max([b[2] for b in bars[i0 + 1:] if b[0] - bars[i0][0] <= horizon] or [px]) / px for _, bars, i0, px in all_t]
        lows = [min([b[3] for b in bars[i0 + 1:] if b[0] - bars[i0][0] <= horizon] or [px]) / px for _, bars, i0, px in all_t]
        print(f"  dans les {horizon // 60} min après l'achat : pic ≥ x1.5 sur {sum(p >= 1.5 for p in peaks) / len(peaks) * 100:.0f} %"
              f", ≥ x2 sur {sum(p >= 2 for p in peaks) / len(peaks) * 100:.0f} % ; creux ≤ -30 % sur "
              f"{sum(lo <= 0.7 for lo in lows) / len(lows) * 100:.0f} %, ≤ -50 % sur {sum(lo <= 0.5 for lo in lows) / len(lows) * 100:.0f} %")
    results = []
    for r in rules():
        tr = [net(simulate(bars, i0, px, r)) for _, bars, i0, px in train]
        te = [net(simulate(bars, i0, px, r)) for _, bars, i0, px in test]
        results.append({"rule": r, "label": label(r), "train": stats(tr), "test": stats(te)})
    results.sort(key=lambda x: -x["train"]["mean"])
    print("\nmeilleures règles sur les calls les plus anciens, et leur résultat sur les plus récents :")
    for x in results[:12]:
        print(f"  {x['label']:52} choix : {fmt(x['train'])}\n  {'':52} test  : {fmt(x['test'])}")
    print("\nrepères :")
    for want in [("fixed", None, None, None, None, 300, None), ("fixed", None, None, None, None, 3600, None),
                 ("fixed", 2, 0.7, None, None, 3600, None), ("fixed", 1.5, 0.8, None, None, 900, None)]:
        x = next(x for x in results if x["rule"] == want)
        print(f"  {x['label']:52} choix : {fmt(x['train'])}\n  {'':52} test  : {fmt(x['test'])}")
    best = results[0]
    print(f"\nrègle retenue « {best['label']} », selon le délai d'entrée (tous les calls) :")
    for d in (1, 2, 3, 5, 10):
        rets = [net(simulate(bars, i0, px, best["rule"])) for _, bars, i0, px in trades(calls, d)]
        print(f"  entrée {d:>2} s après le call : {fmt(stats(rets))}")
    print("\npar caller (règle retenue, tous les calls) :")
    by = {}
    for c, bars, i0, px in all_t:
        by.setdefault(c["caller"], []).append(net(simulate(bars, i0, px, best["rule"])))
    for caller, rets in sorted(by.items(), key=lambda kv: -statistics.mean(kv[1])):
        print(f"  {caller[:8]} : {fmt(stats(rets))}")
    json.dump([{**x, "rule": list(x["rule"])} for x in results], open(os.path.join(DATA, "callout_exit_search.json"), "w"))


if __name__ == "__main__":
    main()
