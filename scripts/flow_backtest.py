#!/usr/bin/env python3
"""
Backtest of exit algorithms on many "trigger" buys (a followed wallet's buys), trade by trade.

Windows: all trades of the token from 5 min before to 45 min after each trigger buy (swap-api.pump.fun), as
json {mint, bt, sig, complete, trades}. Entry: spot price after the last trade landing <= LAT blocks after the
trigger's own transaction (a fast copy), plus our own price impact.

Indicators, one value per second from 60 s before entry to HORIZON after (numpy arrays, prefix sums):
  mc, liq (2 x SOL reserve x SOL price), vol1 / vol5 / vol30s ($), acc = 5 x vol1 / vol5, buys1 / sells1 and
  buys5 / sells5 (counts), bvol1 / svol1 ($), new1 / new30s (wallets whose first trade in the window is a buy),
  avgbuy (1 min), chg1 (price change over 1 min), wsold (share of the trigger wallet's tokens it has sold).
A sell decided at second s fills at the spot price at s + DELAY, minus slippage and our price impact.
Costs: fee per side (pump.fun programs 1.25% + bot 1%, other venues 3% + 1%), GAS $ per transaction.

An algorithm is a function(st) -> fraction of the initial position to sell now. st has: i (index), el (s since
entry), x (price / entry), A (the arrays), peak, peak_el, left, and a free dict mem.
"""
import calendar
import glob
import json
import math
import os
import pickle
import time

import numpy as np

LAT, DELAY, SLIP, GAS, HORIZON, PRE = 5, 2, 0.02, 0.21, 45 * 60, 60
PUMP = {"pump", "pump_amm"}


def ts(s):
    return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))


class Window:
    def __init__(self, path, wallet, cache_dir=None):
        d = json.load(open(path))
        self.mint, self.bt, self.sig, self.ok = d["mint"], d["bt"], d["sig"], False
        cache = os.path.join(cache_dir, f"{self.mint}_{self.bt}.pkl") if cache_dir else None
        if cache and os.path.exists(cache):
            self.__dict__.update(pickle.load(open(cache, "rb")))
            return
        if d.get("complete") and len(d["trades"]) >= 10:
            self._build(d, wallet)
        if cache:
            os.makedirs(cache_dir, exist_ok=True)
            pickle.dump(self.__dict__, open(cache, "wb"))

    def _build(self, d, wallet):
        tr = sorted((t for t in d["trades"] if t.get("priceUsd") and t.get("priceSol")), key=lambda t: t["slotIndexId"])
        mine = [i for i, t in enumerate(tr) if t["tx"] == self.sig]
        if not mine:
            return
        T = np.array([ts(t["timestamp"]) for t in tr])
        slot = np.array([int(t["slotIndexId"][:12]) for t in tr])
        px = np.array([float(t["priceUsd"]) for t in tr])
        pxs = np.array([float(t["priceSol"]) for t in tr])
        usd = np.array([float(t["amountUsd"] or 0) for t in tr])
        buy = np.array([t["type"] == "buy" for t in tr])
        w0 = mine[0]
        self.wallet_px, self.wallet_sol = px[w0], float(tr[w0]["amountSol"] or 0)
        ei = int(np.searchsorted(slot, slot[w0] + LAT, side="right") - 1)
        self.entry_spot, self.t0 = px[ei], int(T[ei])
        self.fee = 0.0225 if tr[w0]["program"] in PUMP else 0.04
        self.venue = tr[w0]["program"]
        sec = np.arange(self.t0 - PRE, self.t0 + HORIZON + 1)
        j = np.searchsorted(T, sec, side="right") - 1          # last trade at or before each second
        ok = j >= 0
        jj = np.where(ok, j, 0)
        cum = lambda v: np.concatenate([[0.0], np.cumsum(v)])
        cv, cb, cnb, cns = cum(usd), cum(usd * buy), cum(buy.astype(float)), cum((~buy).astype(float))
        upto = lambda c, w: c[np.searchsorted(T, sec, side="right")] - c[np.searchsorted(T, sec - w, side="right")]
        A = {}
        A["mc"] = np.where(ok, px[jj] * 1e9, np.nan)
        sol_usd = float(np.median(px[:200] / pxs[:200]))
        # liquidity: virtual reserve on the pump.fun curve; sqrt(k x price) on an AMM with k from large buys
        amm = np.array([t["program"] != "pump" for t in tr])
        k = []
        for a in range(1, len(tr)):
            if amm[a] and amm[a - 1] and buy[a] and float(tr[a]["amountSol"] or 0) > 1 and pxs[a] > pxs[a - 1] * 1.0001:
                k.append((float(tr[a]["amountSol"]) / (math.sqrt(pxs[a] / pxs[a - 1]) - 1)) ** 2 / pxs[a - 1])
        kk = float(np.median(k)) if k else None
        res = np.where(amm[jj] & (kk is not None), np.sqrt((kk or 0) * pxs[jj]), np.sqrt(pxs[jj] * 1e9 * 32.19))
        A["liq"] = np.where(ok, 2 * res * sol_usd, np.nan)
        A["vol1"], A["vol5"], A["vol30s"] = upto(cv, 60), upto(cv, 300), upto(cv, 30)
        A["bvol1"] = upto(cb, 60)
        A["svol1"] = A["vol1"] - A["bvol1"]
        A["buys1"], A["sells1"] = upto(cnb, 60), upto(cns, 60)
        A["buys5"], A["sells5"] = upto(cnb, 300), upto(cns, 300)
        A["acc"] = 5 * A["vol1"] / np.maximum(A["vol5"], 1)
        A["avgbuy"] = A["bvol1"] / np.maximum(A["buys1"], 1)
        first = {}
        for t, tt in zip(tr, T):
            first.setdefault(t["userAddress"], (tt, t["type"] == "buy"))
        fb = np.sort(np.array([v[0] for v in first.values() if v[1]]))
        A["new1"] = (np.searchsorted(fb, sec, side="right") - np.searchsorted(fb, sec - 60, side="right")).astype(float)
        A["new30s"] = (np.searchsorted(fb, sec, side="right") - np.searchsorted(fb, sec - 30, side="right")).astype(float)
        j60 = np.searchsorted(T, sec - 60, side="right") - 1
        A["chg1"] = np.where((j60 >= 0) & ok, px[jj] / px[np.maximum(j60, 0)] - 1, 0.0)
        # trigger wallet: share of its tokens sold so far
        held, sold, wt, ws = 0.0, 0.0, [], []
        for t, tt in zip(tr, T):
            if t["userAddress"] != wallet:
                continue
            q = float(t["baseAmount"] or 0)
            if t["type"] == "buy":
                held += q
            else:
                sold += q
            wt.append(tt)
            ws.append(min(1.0, sold / held) if held else 0.0)
        wi = np.searchsorted(np.array(wt), sec, side="right") - 1
        A["wsold"] = np.where(wi >= 0, np.array(ws + [0.0])[wi], 0.0)
        self.A, self.n = A, len(sec)
        self.i0 = PRE                                           # index of the entry second
        last = int(np.searchsorted(sec, T[-1], side="right") - 1)
        self.i1 = min(self.n - 1, last)
        self.ok = self.i1 > self.i0 + 5

    def replay(self, algo, stake=50.0, log=None):
        """$ result of one copy position"""
        A = self.A
        impact = min(0.2, stake / max(A["liq"][self.i0] / 2, 1))
        entry = self.entry_spot * (1 + impact)
        st = dict(peak=1.0, peak_el=0, mem={}, A=A, left=1.0)
        left, cash, ntx = 1.0, 0.0, 1
        value = stake / (1 + self.fee)
        mc = A["mc"]
        for i in range(self.i0 + 1, self.i1 + 1):
            x = mc[i] / 1e9 / entry
            if x > st["peak"]:
                st["peak"], st["peak_el"] = x, i - self.i0
            st["i"], st["el"], st["x"], st["left"] = i, i - self.i0, x, left
            f = algo(st)
            if f > 1e-9:
                f = min(left, f)
                fx = mc[min(i + DELAY, self.i1)] / 1e9 / entry * (1 - SLIP - impact)
                cash += f * value * fx * (1 - self.fee)
                left -= f
                ntx += 1
                if log is not None:
                    log.append((i - self.i0, f, fx))
                if left <= 1e-9:
                    break
        if left > 1e-9:
            fx = mc[self.i1] / 1e9 / entry * (1 - SLIP - impact)
            cash += left * value * fx * (1 - self.fee)
            ntx += 1
            if log is not None:
                log.append((self.i1 - self.i0, left, fx))
        return cash - stake - GAS * ntx


def load_all(win_dir, wallet, cache_dir):
    ws = [Window(p, wallet, cache_dir) for p in sorted(glob.glob(os.path.join(win_dir, "*.json")))]
    return sorted([w for w in ws if w.ok], key=lambda w: w.bt)


# ---- algorithms ---------------------------------------------------------------------------------------------
def climax_retest(acc=2.0, chg=0.5, mult=2.0, first=0.5, retest=0.9, wait=90, stop=0.5, tmax=40, sl=0.5):
    """sell `first` at the volume climax (acceleration >= acc, +chg in 1 min, >= mult x entry), the rest on the
    retest of the climax high (retest x high, >= wait s later); safety: -stop under the high, -sl under entry
    before any climax, tmax minutes"""
    def algo(st):
        m, x, A, i = st["mem"], st["x"], st["A"], st["i"]
        if not m.get("climax"):
            if A["acc"][i] >= acc and A["chg1"][i] >= chg and x >= mult:
                m["climax"], m["hi"] = st["el"], x
                return first
            return 1.0 if (x <= 1 - sl or st["el"] > tmax * 60) else 0.0
        if st["el"] - m["climax"] < 60:
            m["hi"] = max(m["hi"], x)
            return 0.0
        if st["el"] - m["climax"] >= wait and x >= retest * m["hi"]:
            return 1.0
        return 1.0 if (x <= (1 - stop) * m["hi"] or st["el"] > tmax * 60) else 0.0
    return algo


# ---- fast evaluator: event-based, numpy first-hit times ----------------------------------------------------
class Path:
    """price multiple path of one window (per second from entry), fill multiples and indicator arrays"""
    def __init__(self, w, stake=50.0, delay_s=0, slip=SLIP, fee=None, gas=GAS):
        """delay_s: extra copy delay (entry that many seconds after the fast-copy entry)"""
        A = w.A
        i0 = min(w.i0 + delay_s, w.i1 - 1)
        self.impact = min(0.2, stake / max(A["liq"][i0] / 2, 1))
        self.entry = (w.entry_spot if delay_s == 0 else A["mc"][i0] / 1e9) * (1 + self.impact)
        self.t0 = w.t0 + (i0 - w.i0)
        self.x = A["mc"][i0:w.i1 + 1] / 1e9 / self.entry
        self.gas = gas
        n = len(self.x)
        self.fill = self.x[np.minimum(np.arange(n) + DELAY, n - 1)] * (1 - slip - self.impact)
        self.runmax = np.maximum.accumulate(self.x)
        self.A = {k: v[i0:w.i1 + 1] for k, v in A.items()}
        self.fee = w.fee if fee is None else (fee if w.fee < 0.03 else fee + 0.0175)
        self.stake, self.bt, self.mint, self.venue = stake, w.bt, w.mint, w.venue


def first(mask, start=0):
    """first index >= start where mask is true, or None"""
    if start >= len(mask):
        return None
    k = np.flatnonzero(mask[start:])
    return int(k[0]) + start if len(k) else None


def evaluate(p, levels=(), sl0=None, sl1=None, trail=None, tmax=None, tall=None, wallet=None, climax=None, masks=(), t_start=0):
    """levels: [(multiple, fraction)] take profits in order; sl0: loss before the first take profit;
    sl1: stop (multiple of entry) once a take profit or climax sale happened; trail: (drop, arming multiple)
    on what is left; tmax: s without any sale -> sell all; tall: s -> sell all; wallet: (share sold by the
    trigger wallet, fraction we sell then); climax: (acc, chg1, multiple, fraction).
    Returns $ result."""
    x, n = p.x, len(p.x)
    left, cash, ntx, t, sold_any = 1.0, 0.0, 1, t_start, False
    value = p.stake / (1 + p.fee)

    def sell(i, f):
        nonlocal left, cash, ntx
        f = min(f, left)
        cash += f * value * p.fill[i] * (1 - p.fee)
        left -= f
        ntx += 1

    k = 0                       # next take-profit level
    w_done = c_done = False
    mk = [(fn(p), fr) for fn, fr in masks]
    m_done = [False] * len(mk)
    while left > 1e-9 and t < n:
        ev = []                 # (index, kind)
        if k < len(levels):
            i = first(x >= levels[k][0], t)
            if i is not None: ev.append((i, "tp"))
        if not sold_any and sl0 is not None:
            i = first(x <= 1 - sl0, t)
            if i is not None: ev.append((i, "stop"))
        if sold_any and sl1 is not None:
            i = first(x <= sl1, t)
            if i is not None: ev.append((i, "stop"))
        if trail is not None:
            i = first((p.runmax >= trail[1]) & (x <= (1 - trail[0]) * p.runmax), t)
            if i is not None: ev.append((i, "stop"))
        if not sold_any and tmax is not None and tmax < n and tmax >= t:
            ev.append((int(tmax), "stop"))
        if tall is not None and tall < n and tall >= t:
            ev.append((int(tall), "stop"))
        if wallet is not None and not w_done:
            i = first(p.A["wsold"] >= wallet[0], t)
            if i is not None: ev.append((i, "wallet"))
        if climax is not None and not c_done:
            i = first((p.A["acc"] >= climax[0]) & (p.A["chg1"] >= climax[1]) & (x >= climax[2]), t)
            if i is not None: ev.append((i, "climax"))
        for j, (mm, fr) in enumerate(mk):
            if not m_done[j]:
                i = first(mm, t)
                if i is not None: ev.append((i, f"mask{j}"))
        if not ev:
            break
        i, kind = min(ev)
        if kind == "tp":
            sell(i, levels[k][1]); k += 1; sold_any = True
        elif kind == "wallet":
            sell(i, wallet[1] if wallet[1] < 1 else left); w_done = True; sold_any = True
        elif kind == "climax":
            sell(i, climax[3]); c_done = True; sold_any = True
        elif kind.startswith("mask"):
            j = int(kind[4:]); m_done[j] = True
            sell(i, mk[j][1] if mk[j][1] < 1 else left)
        else:
            sell(i, left)
        t = i + 1
    if left > 1e-9:
        sell(n - 1, left)
    return cash - p.stake - p.gas * ntx


# ---- the "flow" algorithm (pre-registered form; parameters frozen in flow_report.py) ---------------------------
def mask_cum_new(t, nmin):
    """at t s after entry: True if fewer than nmin new wallets bought since entry (disjoint 30 s blocks)"""
    def fn(p):
        m = np.zeros(len(p.x), bool)
        if t < len(m) and cum_new(p, t) < nmin:     # new30s may be shorter than the price path (candles)
            m[t] = True
        return m
    return fn


def mask_exhaustion(frac, min_el=300, floor=20):
    """True once the new-wallet inflow (per minute) is under frac x its running max (max >= floor), after min_el s"""
    def fn(p):
        n = p.A["new1"]
        rm = np.maximum.accumulate(n)
        m = (n < frac * rm) & (rm >= floor)
        m[:min_el] = False
        return m
    return fn


def mask_slow(t, xmin):
    """at t s after entry: True if the price is under xmin x entry"""
    def fn(p):
        m = np.zeros(len(p.x), bool)
        if t < len(m) and p.x[t] < xmin:
            m[t] = True
        return m
    return fn


def flow_rule(check_t=120, check_new=50, tp=1.5, tp_frac=0.5, exh=0.3, slow_t=300, slow_x=0.85, sl=None):
    """1. at check_t s: fewer than check_new new wallets since entry -> sell all;
    2. sell tp_frac at tp x entry; 3. the rest when new-wallet inflow < exh x its max (after 5 min);
    4. at slow_t s: price under slow_x x entry -> sell all; optional fixed stop sl; 45 min horizon."""
    return dict(levels=[(tp, tp_frac)], sl0=sl,
                masks=[(mask_cum_new(check_t, check_new), 1), (mask_exhaustion(exh), 1), (mask_slow(slow_t, slow_x), 1)])


# ---- long horizon: minute candles after the trade window ------------------------------------------------------
def extend_with_candles(p, w, wallet, until_s=3 * 3600):
    """Append to path p the price after the trade window, from minute candles (copy_sim.candles: pump.fun's
    own or GeckoTerminal, USD), up to until_s seconds after entry. Inside a minute: open, then low and high
    (or high and low if it closed down), then close, 15 s each; minutes without trades repeat the last close.
    Indicator arrays are not extended (signals stop at the trade window; price-based exits keep working).
    Returns False if the candles do not cover the period."""
    import copy_sim as CS
    CS.setup(wallet)
    c = CS.candles(w.mint)
    n0 = len(p.x)
    t0 = p.t0 if hasattr(p, "t0") else None
    if c is None or t0 is None:
        return False
    ks, vs, cov = c
    if cov > t0:
        return False
    start, end = t0 + n0, t0 + until_s
    import bisect
    i = bisect.bisect_left(ks, (start // 60) * 60)
    last = p.x[-1] * p.entry
    ext = []
    for minute in range((start // 60) * 60, end, 60):
        if i < len(ks) and ks[i] == minute:
            o, h, l, cl = vs[i][:4]
            seq = (o, l, h, cl) if cl >= o else (o, h, l, cl)
            i += 1
            last = cl
        else:
            seq = (last,) * 4
        for v in seq:
            ext += [v] * 15
    ext = np.array(ext[start - (start // 60) * 60:]) / p.entry
    if not len(ext):
        return True
    p.x = np.concatenate([p.x, ext])
    n = len(p.x)
    p.fill = p.x[np.minimum(np.arange(n) + DELAY, n - 1)] * (1 - SLIP - p.impact)
    p.runmax = np.maximum.accumulate(p.x)
    return True


def cum_new(p, t):
    """new wallets that bought since entry, up to t s (disjoint 30 s blocks)"""
    return float(sum(p.A["new30s"][k] for k in range(30, min(t, len(p.A["new30s"]) - 1) + 1, 30)))


def evaluate_modes(p, t_c, classify, rules, hard_stop=0.4):
    """Three-mode algorithm. Before t_c s: only a hard stop (-hard_stop). At t_c: classify(p, t_c) returns
    "exit" (sell everything), or a key of `rules` whose evaluate() arguments apply from t_c on.
    Returns ($ result, mode)."""
    if t_c >= len(p.x):
        return evaluate(p), "court"
    i = first(p.x[:t_c] <= 1 - hard_stop)
    if i is not None:
        return evaluate(p, sl0=hard_stop), "stop"
    mode = classify(p, t_c)
    if mode == "exit":
        return evaluate(p, masks=[(lambda q, t=t_c: np.arange(len(q.x)) == t, 1)]), mode
    return evaluate(p, t_start=t_c, **rules[mode]), mode


def mask_exhaustion_unless(frac, above, min_el=300, floor=20):
    """exhaustion exit (mask_exhaustion), except while the price is at or above `above` x entry"""
    base = mask_exhaustion(frac, min_el, floor)

    def fn(p):
        m = base(p)
        return m & (p.x[:len(m)] < above)
    return fn


def runner_rule(above=3.0, trail=0.5, horizon=3 * 3600):
    """F1R: F1, but once the price reaches `above` x entry the exhaustion exit stops applying and what is left
    runs with a trailing stop `trail` under its high, up to `horizon` s (needs paths extended with candles)."""
    return dict(levels=[(1.5, 0.5)],
                masks=[(mask_cum_new(120, 50), 1), (mask_slow(300, 0.85), 1), (mask_exhaustion_unless(0.3, above), 1)],
                trail=(trail, above), tall=horizon)
