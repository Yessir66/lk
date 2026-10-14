#!/usr/bin/env python3
"""
Replay "copy this wallet's buys, sell with my own rules" on real prices (data from copy_fetch.py).

Entries: each buy of the wallet on a token where our copy position is flat (one copy buy per position).
Entry price: post-trade spot price (USD) of the last trade landing <= LAT blocks after the wallet's buy
(trade-level data from swap-api.pump.fun); LAT = -1 means the wallet's own post-trade price (a theoretical
bound: no copier gets it). Buys without trade-level data enter at the close of the buy minute.
Path: every trade up to ~2 min after the buy, then minute candles (pump.fun's own, else GeckoTerminal with the
most traded pool each minute; inside a candle the order is o-l-h-c if it closed up, o-h-l-c otherwise), 24 h.
Entries without complete candles around the buy are skipped.
Costs: SIZE $ per trade, FEE per side (pump.fun programs 1.25% + GMGN 1%, other venues 3% + 1%), GAS $ per
transaction; take profits fill at trigger x (1 - SLIP), stops at the trigger (or the price reached, if lower,
in the first 2 min of trade-level data) x (1 - SLIP).

Rule: dict(tps=[(multiple, fraction)], sl=loss before the first take profit | None,
           sl_after=stop as a multiple of entry once the first take profit hit | None,
           trail=(drop from high, multiple that arms it) | None, tmin=minutes without take profit -> sell | None)
"""
import bisect
import calendar
import json
import os
import time

SIZE, GAS, SLIP, HORIZON = 25.0, 0.21, 0.03, 24 * 3600
PUMP_PROGS = {"pump", "pump_amm"}
NOT_MEME = {"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
            "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn", "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So"}
D = None


def setup(wallet):
    global D
    D = os.path.join(os.path.dirname(__file__), "..", "data", f"wallet_{wallet[:8]}")


def load():
    """the wallet's buys and sells per token: (kind, unix time, slot, tokens, SOL, signature)"""
    ev = {}
    for p in json.load(open(f"{D}/swaps.json")):
        if len(p["toks"]) != 1:
            continue
        (m, q), = p["toks"].items()
        if m in NOT_MEME:
            continue
        if q > 0 and p["sol"] < -0.1:
            ev.setdefault(m, []).append(("buy", p["bt"], p["slot"], q, -p["sol"], p["sig"]))
        elif q < 0 and p["sol"] > 0:
            ev.setdefault(m, []).append(("sell", p["bt"], p["slot"], -q, p["sol"], p["sig"]))
    for m in ev:
        ev[m].sort(key=lambda e: e[2])
    return ev


def candles(m):
    """merged minute candles (pump.fun's own preferred, GeckoTerminal otherwise) and the time from which they are complete"""
    rows, cov = {}, float("inf")
    f = f"{D}/ohlcv/{m}.json"
    if os.path.exists(f):
        d = json.load(open(f))
        for c in d["candles"].values():
            for k, v in c.items():
                k = int(k)
                if k not in rows or v[4] > rows[k][4]:
                    rows[k] = v
        if d["windows"]:
            cov = min(w[0] for w in d["windows"])
    f = f"{D}/pcandles/{m}.json"
    if os.path.exists(f):
        pc = {int(k): v for k, v in json.load(open(f))["candles"].items()}
        if pc:
            rows.update(pc)
            cov = min(cov, 0 if len(pc) < 1000 else min(pc) + 60)
    if not rows:
        return None
    ks = sorted(rows)
    return ks, [rows[k] for k in ks], cov


class Entry:
    def __init__(self, m, buy, lat, cdl, limit=None):
        _, bt, slot, q, sol, sig = buy
        self.m, self.bt, self.slot, self.ok, self.wallet_sol = m, bt, slot, False, sol
        ef = f"{D}/entry/{m}_{bt}.json"
        tr = [t for t in (json.load(open(ef))["trades"] if os.path.exists(ef) else []) if t["usd"]]
        mine = [i for i, t in enumerate(tr) if t["tx"] == sig]
        pump = bool(tr) and all(t["prog"] in PUMP_PROGS for t in tr[-20:])
        self.fee = 0.0225 if (pump or m.endswith("pump")) else 0.04
        pts, t_end = [], bt  # (time, open, high, low, close)
        self.wallet_px, self.exact = None, False
        if mine:
            self.wallet_px = tr[mine[0]]["usd"]
            if lat < 0:
                self.entry, after = self.wallet_px, tr[mine[0] + 1:]
            else:
                self.entry = [t for t in tr if t["slot"] <= slot + lat][-1]["usd"]
                after = [t for t in tr if t["slot"] > slot + lat]
            for t in after:
                ts = calendar.timegm(time.strptime(t["ts"][:19], "%Y-%m-%dT%H:%M:%S"))
                pts.append((ts, t["usd"], t["usd"], t["usd"], t["usd"]))
            t_end, self.exact = (bt + 90) // 60 * 60 + 60, True
        if not cdl or cdl[2] > bt - 60:
            return
        ks, vs, _ = cdl
        if not mine:
            i = bisect.bisect_right(ks, bt) - 1
            if i < 0:
                return
            self.entry, t_end = vs[i][3], ks[i] + 60
        pts += [(k, *v[:4]) for k, v in zip(ks, vs) if t_end <= k <= bt + HORIZON]
        if limit:  # buy only if the price comes back to limit[0] x the wallet's price within limit[1] minutes
            if not mine:
                return
            lim_px, t_lim = self.wallet_px * limit[0], bt + 60 * limit[1]
            for j, p in enumerate(pts):
                if p[0] > t_lim:
                    return
                if p[3] <= lim_px:
                    self.entry, pts, self.bt = lim_px, pts[j + 1:], p[0]
                    break
            else:
                return
        if not pts:
            return
        self.pts, self.ok = pts, True
        self.mfe = max(p[2] for p in pts) / self.entry - 1

    def price_at(self, t):
        best = None
        for p in self.pts:
            if p[0] > t:
                break
            best = p[4]
        return best if best is not None else self.entry

    def run(self, r, log=None):
        """($ result, exit time) of one copy position under rule r"""
        e, fee = self.entry, self.fee
        stake = SIZE / (1 + fee)
        left, cash, ntx, hit, peak, tps = 1.0, 0.0, 1, 0, 1.0, r["tps"]

        def sell(frac, mult):
            return frac * stake * mult * (1 - fee)

        for (t, o, h, l, c) in self.pts:
            for px in ((o, l, h, c) if c >= o else (o, h, l, c)):
                x = px / e
                peak = max(peak, x)
                while hit < len(tps) and x >= tps[hit][0]:
                    m_, f = tps[hit][0], min(tps[hit][1], left)
                    cash += sell(f, m_ * (1 - SLIP))
                    left, hit, ntx = left - f, hit + 1, ntx + 1
                    if log is not None:
                        log.append((t, f"vend {int(100 * f)}% à x{m_ * (1 - SLIP):.2f}"))
                if left <= 1e-9:
                    return cash - SIZE - GAS * ntx, t
                floor = None
                if hit == 0 and r["sl"] is not None:
                    floor = 1 - r["sl"]
                if hit > 0 and r["sl_after"] is not None:
                    floor = r["sl_after"]
                if r["trail"] and peak >= r["trail"][1]:
                    floor = max(floor or 0, peak * (1 - r["trail"][0]))
                if floor is not None and x <= floor:
                    fill = min(floor, x) if self.exact and t < self.bt + 120 else floor
                    cash += sell(left, fill * (1 - SLIP))
                    if log is not None:
                        log.append((t, f"stop: vend {int(round(100 * left))}% à x{fill * (1 - SLIP):.2f} (plus haut x{peak:.2f})"))
                    return cash - SIZE - GAS * (ntx + 1), t
            if hit == 0 and r["tmin"] and t - self.bt >= r["tmin"] * 60:
                cash += sell(left, c / e * (1 - SLIP))
                if log is not None:
                    log.append((t, f"limite de temps: vend tout à x{c / e * (1 - SLIP):.2f}"))
                return cash - SIZE - GAS * (ntx + 1), t
        cash += sell(left, self.pts[-1][4] / e * (1 - SLIP))  # end of the 24 h window: market sell
        return cash - SIZE - GAS * (ntx + 1), self.pts[-1][0]

    def mirror(self, lat_s=2):
        """copy the wallet's sells too (GMGN's default): sell the fraction of its holdings it sells, lat_s later"""
        e, fee = self.entry, self.fee
        stake = SIZE / (1 + fee)
        left, cash, ntx, held = 1.0, 0.0, 1, self.wallet_q
        for kind, t, _, q, _, _ in self.after:
            if kind == "buy":
                held += q
                continue
            if held <= 0 or left <= 1e-9:
                break
            f = left if q >= held * 0.999 else min(1.0, q / held) * left
            held -= q
            cash += f * stake * self.price_at(t + lat_s) / e * (1 - SLIP) * (1 - fee)
            left, ntx = left - f, ntx + 1
        if left > 1e-9:
            cash += left * stake * self.pts[-1][4] / e * (1 - SLIP) * (1 - fee)
            ntx += 1
        return cash - SIZE - GAS * ntx


def entries(lat, rule_for_flat, since, limit=None):
    """walk each token's events; copy a buy only when our previous copy position (under rule_for_flat) is closed"""
    out = []
    for m, es in load().items():
        cdl, busy_until, held = candles(m), -1, 0.0
        for i, e in enumerate(es):
            if e[0] == "buy":
                if e[1] > busy_until and since <= e[1] < time.time() - 3600:
                    en = Entry(m, e, lat, cdl, limit)
                    if en.ok:
                        en.wallet_q, en.after = held + e[3], es[i + 1:]
                        busy_until = en.run(rule_for_flat)[1]
                        out.append(en)
                held += e[3]
            else:
                held = max(0.0, held - e[3])
    return sorted(out, key=lambda en: en.bt)
