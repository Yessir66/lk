#!/usr/bin/env python3
"""
Backtester for our own pump.fun bot, on real trade-by-trade paths (data/paths, fetch_candidate_paths.py).

Execution model
  - pump.fun bonding curve: virtual reserves start at 30 SOL / 1.073e9 tokens, k = vS * vT constant;
    every recorded trade moves vT by its token amount (checked: tokens received match the curve).
  - our buy of SIZE SOL lands LATENCY slots after the decision slot, after every trade of earlier slots
    and of its own slot (conservative); tokens = vT - k / (vS + SIZE / (1 + FEE)).
  - our sell lands LATENCY slots after the exit decision; SOL = (vS - k / (vT + q)) * (1 - FEE).
  - our own buy stays in the curve until we sell (later trades keep their recorded token amounts).
  - costs per transaction: FIXED_COST SOL (priority fee + tip + base fee).
Exit rules (evaluated on trades visible at each slot, the sell landing LATENCY slots later):
  hold N slots | take profit / stop loss on the spot price vs our entry | trailing stop | timeout.
"""
import json
import os

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
VS0, VT0 = 30.0, 1.073e9
K = VS0 * VT0
WALLET = "AfPWFykWPZZxU2CyF6BcPoY2v8VEkmYS7ZggvELK7Pv1"


def load_path(mint, exclude=()):
    p = os.path.join(DATA, "paths", f"{mint}.json")
    if not os.path.exists(p):
        return None
    d = json.load(open(p))
    if not d["complete"] or not d["trades"]:
        return None
    tr = [t for t in d["trades"] if t["user"] not in exclude]
    return {"mint": mint, "t_create": d["t_create"], "trades": d["trades"], "trades_ex": tr}


class Curve:
    """Replays the recorded trades; state_before(slot) = virtual token reserve after every trade with
    slot <= given slot."""

    def __init__(self, trades):
        self.slots, self.vts = [], []
        vt = VT0
        for t in trades:
            vt += -t["tokens"] if t["type"] == "buy" else t["tokens"]
            self.slots.append(t["slot"])
            self.vts.append(vt)

    def vt_at(self, slot):
        """Reserve after all recorded trades up to and including `slot`."""
        import bisect
        i = bisect.bisect_right(self.slots, slot)
        return self.vts[i - 1] if i else VT0


def buy(vt, size_sol, fee):
    vs = K / vt
    net = size_sol / (1 + fee)
    tokens = vt - K / (vs + net)
    return tokens, vt - tokens


def sell(vt, tokens, fee):
    vs = K / vt
    return (vs - K / (vt + tokens)) * (1 - fee), vt + tokens


def price(vt):
    return (K / vt) / vt


def simulate(path, entry_slot, size, rule, latency, fee=0.0125, fixed_cost=0.002, trades_key="trades_ex"):
    """One position: decision to buy at entry_slot, lands at entry_slot + latency.
    rule: dict(kind='hold', n) | (kind='tpsl', tp, sl, max_slots) | (kind='trail', trail, max_slots)
    Returns dict(pnl, ret, hold_slots, exit_reason) or None if the path is too short."""
    tr = path[trades_key]
    curve = Curve(tr)
    land = entry_slot + latency
    vt_before = curve.vt_at(land)
    tokens, _ = buy(vt_before, size, fee)
    my_shift = tokens  # our tokens stay out of the curve while we hold
    entry_px = size / tokens
    last_slot = tr[-1]["slot"] if tr else land
    peak = entry_px
    decide = None
    reason = None
    for s in range(land + 1, last_slot + 1):
        px = price(curve.vt_at(s) - my_shift)
        peak = max(peak, px)
        held = s - land
        if rule["kind"] == "hold" and held >= rule["n"]:
            decide, reason = s, "durée"
        elif rule["kind"] == "tpsl":
            if px >= entry_px * (1 + rule["tp"]):
                decide, reason = s, "prise de profit"
            elif px <= entry_px * (1 - rule["sl"]):
                decide, reason = s, "stop"
            elif held >= rule["max_slots"]:
                decide, reason = s, "délai max"
        elif rule["kind"] == "trail":
            if px <= peak * (1 - rule["trail"]) and held >= rule.get("min_slots", 0):
                decide, reason = s, "stop suiveur"
            elif held >= rule["max_slots"]:
                decide, reason = s, "délai max"
        if decide:
            break
    if decide is None:
        if last_slot < land + 5:
            return None
        decide, reason = last_slot, "fin des données"
    exit_land = decide + latency
    sol_out, _ = sell(curve.vt_at(exit_land) - my_shift, tokens, fee)
    pnl = sol_out - size - 2 * fixed_cost
    return {"pnl": pnl, "ret": pnl / size, "hold_slots": exit_land - land, "exit_reason": reason}
