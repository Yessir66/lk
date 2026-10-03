#!/usr/bin/env python3
"""
Would buying every callout of the followed accounts and selling on a take profit pay?

Data: data/callouts/callouts.json (fetch_kol_callouts.py), data/callout_paths/ (fetch_callout_paths.py).

Execution model (same conventions as backtest.py, extended to the PumpSwap pool after migration)
  - the callout is created at createdAt (ms); our bot sees it DETECT seconds later and its buy lands
    LATENCY slots after that, at a uniformly random position among the recorded trades of that slot.
  - prices: each recorded trade gives the spot price after it; reserves follow from k = vS * vT
    (curve: 30 * 1.073e9; PumpSwap: estimated per token from its own trades, ~1.73e10).
  - our buy of SIZE SOL: tokens = vT - k / (vS + SIZE / (1 + fee)); while we hold, our tokens stay out of
    the pool (later trades keep their recorded amounts); after a migration the pool is seeded the same
    way with or without us, so the shift stops.
  - fees: 1.25% per side on the curve (calibrated in calibrate_backtest.py); on PumpSwap the fee each
    token's sellers actually paid, from its recorded sells (clipped to 0.3-1.25%).
  - exit rules are evaluated at the end of each slot on the price including our position; the sell lands
    LATENCY slots after the decision at a random position in its slot. FIXED SOL per transaction.
  - one position per token: a callout on a token already called in the previous WINDOW is skipped.
Slot clock: interpolated between the median slot of each second of block time over every recorded trade.

Usage: callout_backtest.py [--size SOL]
Output: data/callout_backtest.json
"""
import glob
import hashlib
import itertools
import json
import os
import sys
from collections import defaultdict

import numpy as np

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
K_CURVE, K_AMM_DEFAULT = 30.0 * 1.073e9, 1.75e10
FEE_CURVE, FEE_AMM_RANGE = 0.0125, (0.003, 0.0125)
FIXED = 0.002
WINDOW = 900  # s of recorded trades after each callout
MIN_COMPLETE_S = 120  # paths complete over less than this after the callout (too many trades) are left out
VENUES = {"pump": False, "pump_amm": True}
SOL = {"So11111111111111111111111111111111111111112", "11111111111111111111111111111111"}


class Clock:
    """Solana slot <-> unix time, interpolated between the median slot of every second seen in the recorded
    trades (block times are floored to the second; slots run at ~2.5-3.3 per second)."""

    def __init__(self, paths):
        by_sec = defaultdict(list)
        for p in paths:
            for s, ts in zip(p.slot, p.ts):
                by_sec[int(ts)].append(s)
        secs = sorted(by_sec)
        self.t = np.array(secs, dtype=float) + 0.5
        self.s = np.maximum.accumulate(np.array([np.median(by_sec[x]) for x in secs]))

    def slot_at(self, t):
        return int(round(np.interp(t, self.t, self.s)))

    def time_at(self, slot):
        return float(np.interp(slot, self.s, self.t))


class Path:
    def __init__(self, d):
        tr = [t for t in d["trades"] if t["venue"] in VENUES and t["quote"] in SOL and t["price_after"]
              and t["tokens"] > 0]
        self.id, self.mint, self.t_call, self.complete_to = d["calloutId"], d["mint"], d["t_call"], d["complete_to"]
        self.scope = ("evm" if self.mint.startswith("0x") else "aucun trade" if not d["trades"]
                      else "pump.fun" if tr else "autre DEX / autre paire")
        self.truncated = self.complete_to < self.t_call + MIN_COMPLETE_S
        self.ok = len(tr) >= 2 and not self.truncated
        if not self.ok:
            return
        self.slot = np.array([t["slot"] for t in tr], dtype=np.int64)
        self.ts = np.array([t["ts"] for t in tr])
        self.p = np.array([t["price_after"] for t in tr])
        self.amm = np.array([VENUES[t["venue"]] for t in tr])
        self.user = [t["user"] for t in tr]
        self.side = np.array([t["type"] == "buy" for t in tr])
        self.sol = np.array([t["sol"] for t in tr])
        self.tokens = np.array([t["tokens"] for t in tr])
        self.k_amm, self.fee_amm = self._amm_params()
        k0 = self.k_amm if self.amm[0] else K_CURVE
        vt1 = np.sqrt(k0 / self.p[0])
        vt0 = vt1 + self.tokens[0] if self.side[0] else vt1 - self.tokens[0]
        self.p0 = k0 / vt0 ** 2 if vt0 > 0 else self.p[0]

    def _amm_params(self):
        i = np.nonzero(self.amm[1:] & self.amm[:-1])[0] + 1
        if len(i) < 10:
            return K_AMM_DEFAULT, FEE_AMM_RANGE[1]
        p0, p1 = self.p[i - 1], self.p[i]
        d = np.abs(1 / np.sqrt(p1) - 1 / np.sqrt(p0))
        m = (d > 0) & (self.tokens[i] > 1e5)
        if m.sum() < 10:
            return K_AMM_DEFAULT, FEE_AMM_RANGE[1]
        k = float(np.median((self.tokens[i][m] / d[m]) ** 2))
        dvs = np.abs(np.sqrt(k * p1) - np.sqrt(k * p0))
        s = (~self.side[i]) & (self.sol[i] > 0.05) & (dvs > 0)
        fee = 1 - float(np.median(self.sol[i][s] / dvs[s])) if s.sum() >= 5 else FEE_AMM_RANGE[1]
        return k, float(np.clip(fee, *FEE_AMM_RANGE))

    def state(self, i):
        """Spot price and venue after the first i recorded trades."""
        if i == 0:
            return self.p0, bool(self.amm[0])
        return self.p[i - 1], bool(self.amm[i - 1])

    def k_fee(self, amm):
        return (self.k_amm, self.fee_amm) if amm else (K_CURVE, FEE_CURVE)

    def land_index(self, slot, rng):
        lo, hi = np.searchsorted(self.slot, slot, "left"), np.searchsorted(self.slot, slot, "right")
        return int(lo + rng.integers(0, hi - lo + 1))


def rng_for(cid, tag):
    return np.random.default_rng(int(hashlib.sha256(f"{cid}{tag}".encode()).hexdigest()[:12], 16))


class Position:
    """Our buy after a callout; prices seen at each slot end while holding; exits for many rules."""

    def __init__(self, path, clock, detect, latency, size):
        self.path, self.clock, self.latency, self.size = path, clock, latency, size
        t_dec = path.t_call + detect
        self.land = clock.slot_at(t_dec) + latency
        self.t_land = clock.time_at(self.land)
        self.i_in = path.land_index(self.land, rng_for(path.id, f"in{detect}{latency}"))
        p, self.amm_in = path.state(self.i_in)
        self.p_in_spot = p
        k, fee = path.k_fee(self.amm_in)
        vs, vt = np.sqrt(k * p), np.sqrt(k / p)
        self.tok = vt - k / (vs + size / (1 + fee))
        self.entry_px = size / self.tok
        j0 = np.searchsorted(path.slot, self.land + 1, "left")
        sl = path.slot[j0:]
        ends = j0 + np.nonzero(np.append(sl[1:] != sl[:-1], True))[0] if len(sl) else np.array([], dtype=int)
        self.S = path.slot[ends]
        self.P = np.array([self._px_ours(j) for j in ends])
        self.last_slot = clock.slot_at(path.complete_to)

    def _px_ours(self, j):
        p, amm = self.path.p[j], bool(self.path.amm[j])
        if amm != self.amm_in:
            return p
        k, _ = self.path.k_fee(amm)
        vt = np.sqrt(k / p) - self.tok
        return k / vt ** 2 if vt > 0 else p

    def max_gain(self, horizon_s):
        """Best spot price seen within horizon_s, relative to our entry price (fees and impact included)."""
        s_end = self.clock.slot_at(self.t_land + horizon_s)
        m = self.S <= s_end
        return float(self.P[m].max() / self.entry_px - 1) if m.any() else -1.0

    def exit(self, rule):
        """rule: dict(tp, sl, trail, max_s); None disables a condition. Returns (pnl SOL, reason, hold s)."""
        cands = []
        s_to = min(self.clock.slot_at(self.t_land + rule["max_s"]), self.last_slot)
        cands.append((s_to, "délai max" if s_to < self.last_slot else "fin des données"))
        live = self.S <= s_to
        S, P = self.S[live], self.P[live]
        if rule.get("tp") is not None:
            hit = np.nonzero(P >= self.entry_px * (1 + rule["tp"]))[0]
            if len(hit):
                cands.append((int(S[hit[0]]), "prise de profit"))
        if rule.get("sl") is not None:
            hit = np.nonzero(P <= self.entry_px * (1 - rule["sl"]))[0]
            if len(hit):
                cands.append((int(S[hit[0]]), "stop"))
        if rule.get("trail") is not None and len(P):
            peak = np.maximum.accumulate(np.maximum(P, self.entry_px))
            hit = np.nonzero(P <= peak * (1 - rule["trail"]))[0]
            if len(hit):
                cands.append((int(S[hit[0]]), "stop suiveur"))
        decide, reason = min(cands)
        x = decide + self.latency
        i = self.path.land_index(x, rng_for(self.path.id, f"out{x}"))
        p, amm = self.path.state(i)
        k, fee = self.path.k_fee(amm)
        vt = np.sqrt(k / p) - (self.tok if amm == self.amm_in else 0)
        sol = (k / vt - k / (vt + self.tok)) * (1 - fee)
        return float(sol - self.size - 2 * FIXED), reason, self.clock.time_at(x) - self.t_land


def rules():
    out = [{"tp": tp, "sl": None, "trail": None, "max_s": mx} for tp, mx in itertools.product(
        (0.25, 0.3), (60, 180, 300, 600, 900))]
    out += [{"tp": tp, "sl": sl, "trail": None, "max_s": mx} for tp, sl, mx in itertools.product(
        (0.1, 0.15, 0.25, 0.3, 0.5, 1.0), (0.1, 0.2, 0.3, 0.5), (60, 180, 300, 900))]
    out += [{"tp": None, "sl": None, "trail": tr, "max_s": mx} for tr, mx in itertools.product(
        (0.1, 0.2, 0.3), (180, 900))]
    out += [{"tp": None, "sl": None, "trail": None, "max_s": mx} for mx in (5, 15, 30, 60, 180, 900)]
    return out


def name(r):
    parts = []
    if r["tp"] is not None:
        parts.append(f"TP +{round(100 * r['tp'])}%")
    if r["sl"] is not None:
        parts.append(f"SL -{round(100 * r['sl'])}%")
    if r["trail"] is not None:
        parts.append(f"suiveur -{round(100 * r['trail'])}%")
    parts.append(f"max {r['max_s']} s" if r["max_s"] < 60 else f"max {r['max_s'] // 60} min")
    return " / ".join(parts)


def load():
    meta = {r["calloutId"]: r for r in json.load(open(os.path.join(DATA, "callouts", "callouts.json")))}
    paths = []
    for f in glob.glob(os.path.join(DATA, "callout_paths", "*.json")):
        p = Path(json.load(open(f)))
        p.meta = meta[p.id]
        paths.append(p)
    paths.sort(key=lambda p: p.t_call)
    kept, last_call = [], {}
    for p in paths:  # one position per token
        if p.mint in last_call and p.t_call - last_call[p.mint] < WINDOW:
            continue
        last_call[p.mint] = p.t_call
        kept.append(p)
    return paths, kept


def boot_ci(x, groups, n=2000, seed=0):
    """95% CI of the mean, resampling whole days (callouts of a day share the market regime)."""
    rng = np.random.default_rng(seed)
    keys = np.unique(groups)
    idx = {g: np.nonzero(groups == g)[0] for g in keys}
    means = []
    for _ in range(n):
        pick = np.concatenate([idx[g] for g in rng.choice(keys, len(keys))])
        means.append(x[pick].mean())
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def main():
    size = float(sys.argv[sys.argv.index("--size") + 1]) if "--size" in sys.argv else 0.5
    allp, paths = load()
    clock = Clock([p for p in allp if p.ok])
    usable = [p for p in paths if p.ok]
    scope = defaultdict(int)
    for p in allp:
        scope[p.scope] += 1
    print(f"{len(allp)} callouts récupérés: " + ", ".join(f"{k} {v}" for k, v in sorted(scope.items(), key=lambda kv: -kv[1])))
    print(f"{sum(p.truncated for p in allp if p.scope == 'pump.fun')} tokens pump.fun écartés car incomplets moins de "
          f"{MIN_COMPLETE_S} s après le callout; "
          f"{len(paths)} après une position par token (fenêtre {WINDOW // 60} min), "
          f"{len(usable)} sur un token pump.fun (courbe ou PumpSwap, paire SOL)")
    days = np.array([int(p.t_call // 86400) for p in usable])
    report = {"n_callouts": len(allp), "scope": dict(scope), "n_kept": len(paths), "n_usable": len(usable), "size": size}

    # 1. what happens around a callout (spot prices, no fees)
    print("\n=== Autour du callout (prix spot) ===")
    rows = []
    for p in usable:
        i_call = int(np.searchsorted(p.ts, p.t_call, "right"))  # trades of the callout's second count as after
        i_m60 = int(np.searchsorted(p.ts, p.t_call - 60, "left"))
        pc, _ = p.state(max(0, int(np.searchsorted(p.slot, clock.slot_at(p.t_call), "left"))))
        pm, _ = p.state(i_m60)
        after = p.p[p.slot > clock.slot_at(p.t_call)]
        author = p.meta["author_address"]
        a_sell = [p.ts[j] - p.t_call for j in range(len(p.user)) if p.user[j] == author and not p.side[j]
                  and p.ts[j] >= int(p.t_call)]
        rows.append({"pre60": pc / pm - 1, "max5": (after[p.ts[p.slot > clock.slot_at(p.t_call)] <= p.t_call + 300].max()
                     / pc - 1) if len(after) else 0.0, "author_sell_s": min(a_sell) if a_sell else None,
                     "amm": bool(p.state(i_call)[1]), "n_after": int((p.ts >= p.t_call).sum())})
    pre = np.array([r["pre60"] for r in rows])
    mx5 = np.array([r["max5"] for r in rows])
    a_s = [r["author_sell_s"] for r in rows]
    print(f"  variation du prix dans la minute AVANT le callout: médiane {100 * np.median(pre):+.1f}% "
          f"(p25 {100 * np.percentile(pre, 25):+.1f}%, p75 {100 * np.percentile(pre, 75):+.1f}%)")
    print(f"  plus haut dans les 5 min après le callout vs prix au callout: médiane {100 * np.median(mx5):+.1f}%, "
          f">= +25%: {100 * (mx5 >= 0.25).mean():.0f}%, >= +30%: {100 * (mx5 >= 0.30).mean():.0f}%")
    print(f"  token déjà migré sur PumpSwap au callout: {100 * np.mean([r['amm'] for r in rows]):.0f}%")
    sold = [s for s in a_s if s is not None]
    print(f"  l'auteur vend depuis son wallet de profil dans les 15 min: {100 * len(sold) / len(rows):.0f}% des callouts"
          + (f" (médiane {np.median(sold):.0f} s après)" if sold else ""))
    report["around"] = {"pre60_median": float(np.median(pre)), "max5_median": float(np.median(mx5)),
                        "max5_ge25": float((mx5 >= 0.25).mean()), "amm_share": float(np.mean([r["amm"] for r in rows])),
                        "author_sells_15min": len(sold) / len(rows)}

    # 2. the plan as stated: TP +25/30%, no stop, against detection delay
    R = rules()
    plan = [{"tp": 0.25, "sl": None, "trail": None, "max_s": 900}, {"tp": 0.3, "sl": None, "trail": None, "max_s": 900}]
    print(f"\n=== Le plan tel quel: acheter {size} SOL à chaque callout, vendre à +25/30%, sinon au bout de 15 min ===")
    report["plan"] = {}
    for detect in (0.5, 1, 2, 5, 15, 30):
        pos = [Position(p, clock, detect, 2, size) for p in usable]
        gain = np.array([q.max_gain(900) for q in pos])
        move = np.array([q.p_in_spot / p.state(int(np.searchsorted(p.slot, clock.slot_at(p.t_call), "left")))[0] - 1
                         for q, p in zip(pos, usable)])
        line = f"  détection {detect:>4} s: prix d'entrée vs callout méd. {100 * np.median(move):+5.1f}% | "
        report["plan"][str(detect)] = {"entry_vs_call_median": float(np.median(move))}
        for r in plan:
            res = [q.exit(r) for q in pos]
            pnl = np.array([x[0] for x in res])
            tp_hit = np.mean([x[1] == "prise de profit" for x in res])
            lo, hi = boot_ci(pnl, days)
            line += (f"TP+{round(100 * r['tp'])}%: atteint {100 * tp_hit:3.0f}%, {pnl.mean():+.4f} SOL/trade "
                     f"[{lo:+.4f}, {hi:+.4f}] | ")
            report["plan"][str(detect)][name(r)] = {"tp_hit": float(tp_hit), "mean": float(pnl.mean()),
                                                   "ci95": [lo, hi], "win": float((pnl > 0).mean()),
                                                   "total": float(pnl.sum())}
        print(line + f"max +25% atteignable: {100 * (gain >= 0.25).mean():.0f}%")

    # 3. other exit rules, chosen on the oldest 60%, checked on the newest 40%
    cut = int(len(usable) * 0.6)
    report["search"] = {}
    for detect in (1, 5):
        pos = [Position(p, clock, detect, 2, size) for p in usable]
        Pn = np.array([[q.exit(r)[0] for r in R] for q in pos])
        tr, te = Pn[:cut], Pn[cut:]
        order = np.argsort(-tr.mean(0))
        print(f"\n=== Autres règles de sortie, détection {detect} s (train {cut} callouts, test {len(usable) - cut}) ===")
        print(f"  {'règle':38s} {'train /trade':>12s} | {'test /trade':>11s} {'gagn.':>5s}")
        rows = []
        for j in order[:8]:
            rows.append({"rule": name(R[j]), "train": float(tr[:, j].mean()), "test": float(te[:, j].mean()),
                         "test_win": float((te[:, j] > 0).mean())})
            print(f"  {name(R[j]):38s} {tr[:, j].mean():+12.4f} | {te[:, j].mean():+11.4f} {100 * (te[:, j] > 0).mean():4.0f}%")
        lo, hi = boot_ci(te[:, order[0]], days[cut:])
        print(f"  meilleure règle du train sur test: {te[:, order[0]].mean():+.4f} SOL/trade, IC95 [{lo:+.4f}, {hi:+.4f}]")
        report["search"][str(detect)] = {"top": rows, "best_test_ci95": [lo, hi]}

    # 4. by author, plan TP +25% at 1 s detection (descriptive: 27 authors, expect some to look good by chance)
    pos = [Position(p, clock, 1, 2, size) for p in usable]
    res = np.array([q.exit(plan[0])[0] for q in pos])
    by = defaultdict(list)
    for p, x in zip(usable, res):
        by[p.meta["author"]].append(x)
    print("\n=== Par auteur (plan TP +25%, détection 1 s) — descriptif, pas une sélection ===")
    report["by_author"] = {}
    for a, xs in sorted(by.items(), key=lambda kv: -np.mean(kv[1])):
        xs = np.array(xs)
        report["by_author"][a] = {"n": len(xs), "mean": float(xs.mean()), "win": float((xs > 0).mean())}
        if len(xs) >= 10:
            print(f"  {a:16s} n={len(xs):4d}  {xs.mean():+.4f} SOL/trade  gagnants {100 * (xs > 0).mean():3.0f}%")
    json.dump(report, open(os.path.join(DATA, "callout_backtest.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
